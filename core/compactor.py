"""
core/compactor.py — 시맨틱 auto-compaction.

ReactLoop의 대화 히스토리가 임계치를 넘으면 중간 구간을 작은 LLM으로
"지금까지의 진행 요약"으로 압축한다. 캐시 prefix(system + 첫 user 메시지)와
최근 N 메시지는 반드시 보존하고, tool_use/tool_result 페어는 깨지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from llm.base import Message

logger = logging.getLogger(__name__)


_SUMMARY_PROMPT_HEADER = (
    "다음은 코드 작성 에이전트의 작업 히스토리다. 이 히스토리를 300단어 이내로 요약하라.\n"
    "포함할 것:\n"
    "- 지금까지 읽은 주요 파일과 그 핵심 내용\n"
    "- 수정/생성한 파일과 그 이유\n"
    "- 테스트 실행 결과의 핵심 (실패 테스트명, 에러 요지)\n"
    "- 현재 진행 중인 작업과 다음 의도\n\n"
    "생략할 것:\n"
    "- 성공한 단순 파일 읽기 로그\n"
    "- 전체 스택 트레이스 (핵심 한 줄만)\n"
    "- 도구 스키마 정보\n\n"
    "[히스토리]\n"
)


@dataclass
class CompactionResult:
    """compact_history() 반환값."""

    summary_message: Message           # 요약을 담은 새 user 메시지
    dropped_range: tuple[int, int]     # (start_idx, end_idx) — messages에서 드롭된 구간 [start, end)
    input_tokens_used: int
    output_tokens_used: int
    dropped_message_count: int = 0
    dropped_tokens_estimate: int = 0
    summary_tokens_estimate: int = 0


# ── 토큰 추정 ─────────────────────────────────────────────────────────────────


def estimate_tokens(message: Message | dict) -> int:
    """
    메시지의 대략적인 입력 토큰 수를 추정한다.

    정확한 tokenization 대신 `len(text) / 4` 휴리스틱을 사용.
    content가 list(tool_use/tool_result)인 경우 내부 문자열을 재귀적으로 합산한다.
    """
    role = getattr(message, "role", None) or (
        message.get("role") if isinstance(message, dict) else ""
    )
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")

    return max(1, len(role) // 4) + _walk_tokens(content)


def _walk_tokens(obj: Any) -> int:
    if obj is None:
        return 0
    if isinstance(obj, str):
        return len(obj) // 4
    if isinstance(obj, (list, tuple)):
        return sum(_walk_tokens(x) for x in obj)
    if isinstance(obj, dict):
        return sum(_walk_tokens(v) for v in obj.values())
    # SDK 객체 (e.g. anthropic ToolUseBlock)
    if hasattr(obj, "__dict__"):
        return sum(_walk_tokens(v) for v in vars(obj).values())
    # 숫자/불리언 등은 무시
    return 0


# ── 블록 판별 유틸 ────────────────────────────────────────────────────────────


def _block_type(block: Any) -> str | None:
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _has_tool_use(message: Message) -> bool:
    """assistant 메시지가 tool_use 블록을 포함하는지."""
    if message.role != "assistant":
        return False
    if not isinstance(message.content, list):
        return False
    return any(_block_type(b) == "tool_use" for b in message.content)


def _has_tool_result(message: Message) -> bool:
    """user 메시지가 tool_result 블록을 포함하는지."""
    if message.role != "user":
        return False
    if not isinstance(message.content, list):
        return False
    return any(_block_type(b) == "tool_result" for b in message.content)


def _is_safe_cut_point(messages: list[Message], idx: int) -> bool:
    """
    messages[idx-1]와 messages[idx] 사이에서 대화를 잘라도 tool_use/tool_result
    페어가 깨지지 않는지 판별한다.

    tool_use 직후에 잘라 tool_result 가 반대편에 남으면 API 400 에러의 원인이
    되므로 이 위치는 피해야 한다.
    """
    if idx <= 0 or idx >= len(messages):
        return True
    return not _has_tool_use(messages[idx - 1])


# ── 메시지 직렬화 (요약 입력용) ───────────────────────────────────────────────


def _block_to_text(block: Any) -> str:
    btype = _block_type(block)
    if btype == "text":
        if isinstance(block, dict):
            return block.get("text", "")
        return getattr(block, "text", "") or ""
    if btype == "tool_use":
        name = block.get("name") if isinstance(block, dict) else getattr(block, "name", "")
        inp = block.get("input") if isinstance(block, dict) else getattr(block, "input", {})
        return f"<tool_use name={name!r} input={inp!r}>"
    if btype == "tool_result":
        content = (
            block.get("content") if isinstance(block, dict)
            else getattr(block, "content", "")
        )
        if isinstance(content, list):
            content = "".join(_block_to_text(b) for b in content)
        return f"<tool_result>{content}</tool_result>"
    # 알 수 없는 블록 타입 — 문자열로 강제 변환
    return str(block)


def _message_to_text(message: Message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_block_to_text(b) for b in content)
    return str(content)


def _serialize_history(messages: list[Message]) -> str:
    lines = []
    for m in messages:
        text = _message_to_text(m)
        lines.append(f"[{m.role}]\n{text}")
    return "\n\n".join(lines)


# ── 메인 API ──────────────────────────────────────────────────────────────────


def compact_history(
    messages: list[Message],
    llm_client,
    keep_first_n: int = 2,
    keep_last_n: int = 4,
) -> CompactionResult | None:
    """
    messages 배열의 가운데 구간을 요약하여 단일 user 메시지로 대체한다.

    Args:
        messages:     원본 메시지 리스트 (system + user(task) + [assistant, user, ...])
        llm_client:   요약 생성용 LLM (빠른 모델 권장)
        keep_first_n: 앞에서 보존할 메시지 개수 (캐시 prefix)
        keep_last_n:  뒤에서 보존할 메시지 개수 (최근 컨텍스트)

    Returns:
        CompactionResult — 요약 메시지 + 드롭된 구간 정보 + 토큰 사용량.
        드롭할 만큼 메시지가 충분치 않으면 None.
    """
    if keep_first_n < 0 or keep_last_n < 0:
        raise ValueError("keep_first_n / keep_last_n must be >= 0")

    n = len(messages)
    if n <= keep_first_n + keep_last_n:
        return None  # 압축할 거리 부족

    # 1. 드롭 경계 산출 — 페어 보호를 위해 안전한 cut point 로 스냅.
    #    drop_start: 앞에서 뒤로(advance) — 앞쪽 보호 영역을 침범하지 않도록.
    #    drop_end  : 뒤에서 앞으로(retreat) — keep_last_n 계약을 지키기 위해
    #                tool_use 를 뒤쪽(꼬리) 에 남기는 방향으로만 조정한다.
    drop_start = keep_first_n
    while drop_start < n and not _is_safe_cut_point(messages, drop_start):
        drop_start += 1

    drop_end = n - keep_last_n
    while drop_end > drop_start and not _is_safe_cut_point(messages, drop_end):
        drop_end -= 1

    if drop_end - drop_start < 2:
        # 드롭 구간이 너무 작으면 압축 의미 없음
        return None

    # 2. 보호 영역 검증 — 첫 keep_first_n 메시지는 절대 드롭되지 않아야 한다.
    if drop_start < keep_first_n:
        logger.error(
            "compact_history: drop_start(%d) < keep_first_n(%d) — 보호 영역 위반",
            drop_start, keep_first_n,
        )
        return None

    # 3. 뒤쪽 보호 영역 검증 — keep_last_n 계약 준수 (tail ≥ keep_last_n).
    preserved_tail = n - drop_end
    if preserved_tail < keep_last_n:
        logger.warning(
            "compact_history: 안전 cut point 가 뒤쪽 보호 영역을 침범해 압축 스킵 "
            "(keep_last_n=%d, preserved_tail=%d, range=(%d, %d))",
            keep_last_n, preserved_tail, drop_start, drop_end,
        )
        return None

    dropped_msgs = messages[drop_start:drop_end]
    dropped_tokens = sum(estimate_tokens(m) for m in dropped_msgs)

    # 3. 요약 LLM 호출
    prompt_body = _serialize_history(dropped_msgs)
    summary_user_message = Message(
        role="user",
        content=_SUMMARY_PROMPT_HEADER + prompt_body,
    )

    input_tokens_used = 0
    output_tokens_used = 0
    summary_text: str
    try:
        # tools 없이 호출 — 일부 클라이언트는 kwargs 미지정 시 기본 스키마를 쓰므로
        # 명시적으로 None 을 전달.
        response = llm_client.chat(
            messages=[summary_user_message],
            tools=None,
        )
        input_tokens_used = getattr(response, "input_tokens", 0) or 0
        output_tokens_used = getattr(response, "output_tokens", 0) or 0
        summary_text = _extract_summary_text(response)
    except Exception as exc:
        logger.warning("compact_history: 요약 LLM 호출 실패 — 스킵 (%s)", exc)
        return None

    if not summary_text.strip():
        logger.warning("compact_history: 요약 결과가 비어 있음 — 스킵")
        return None

    summary_message = Message(
        role="user",
        content=(
            "[이전 대화 요약]\n"
            f"{summary_text.strip()}\n"
            "----\n"
            "(이하 최근 대화 이어서)"
        ),
    )

    summary_tokens_estimate = estimate_tokens(summary_message)

    return CompactionResult(
        summary_message=summary_message,
        dropped_range=(drop_start, drop_end),
        input_tokens_used=input_tokens_used,
        output_tokens_used=output_tokens_used,
        dropped_message_count=len(dropped_msgs),
        dropped_tokens_estimate=dropped_tokens,
        summary_tokens_estimate=summary_tokens_estimate,
    )


def _extract_summary_text(response) -> str:
    """LLMResponse에서 텍스트 블록만 이어붙여 반환한다."""
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if _block_type(block) == "text":
            if isinstance(block, dict):
                parts.append(block.get("text", "") or "")
            else:
                parts.append(getattr(block, "text", "") or "")
    return "\n".join(parts)
