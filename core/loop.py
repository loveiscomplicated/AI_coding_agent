"""
core/loop.py — ReAct 루프 (Reason → Act → Observe)

흐름:
  1. LLM에 메시지 + 도구 스키마 전송
  2. tool_use 블록 감지 → 실제 도구 실행
  3. tool_result를 다시 LLM에 전달
  4. stop_reason == "end_turn"이면 종료
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from typing import Any, Callable

from tools.registry import (
    TOOLS_SCHEMA_ANTHROPIC,
    TOOLS_SCHEMA_OPENAI,
    TOOLS_SCHEMA_OLLAMA,
    call_tool,
)

from llm.base import Message, LLMResponse, BaseLLMClient, StopReason

logger = logging.getLogger(__name__)

# 실행 전 사용자 승인이 필요한 도구 목록
_APPROVAL_REQUIRED = {"write_file", "edit_file", "append_to_file", "execute_command", "git_commit"}


# ── 타입 정의 ─────────────────────────────────────────────────────────────────
@dataclass
class ToolCall:
    """LLM이 요청한 도구 호출 하나"""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    """도구 실행 결과 — LLM에게 돌려줄 형태"""

    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class LoopIteration:
    """단일 반복의 스냅샷 (디버깅·로깅용)"""

    index: int
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    elapsed_ms: float


@dataclass
class LoopResult:
    """run()의 최종 반환값"""

    answer: str
    stop_reason: StopReason
    iterations: list[LoopIteration] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    @property
    def succeeded(self) -> bool:
        return self.stop_reason == StopReason.END_TURN

    @property
    def total_tool_calls(self) -> int:
        return sum(len(it.tool_calls) for it in self.iterations)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


# ── 메인 루프 ─────────────────────────────────────────────────────────────────


class ReactLoop:
    """
    ReAct 루프 엔진.

    사용 예:
        loop = ReactLoop(llm=OllamaClient(), max_iterations=15)
        result = loop.run("main.py에서 TODO 주석 찾아줘")
        print(result.answer)
    """

    def __init__(
        self,
        llm,  # BaseLLM 구현체
        max_iterations: int = 10,
        tool_timeout_s: float = 30.0,  # 도구 실행 타임아웃 (초)
        on_tool_call=None,     # Callable[[ToolCall], None] — CLI 훅
        on_tool_result=None,   # Callable[[ToolResult], None] — CLI 훅
        on_tool_approval=None, # Callable[[ToolCall], bool] — 승인 요청 훅
        on_token: Callable[[str], None] | None = None,  # 스트리밍 콜백
        max_tool_result_chars: int = 4000,  # 도구 결과 최대 문자 수 (초과 시 잘림)
        history_window: int = 6,  # 보존할 최근 turn 쌍 수 (0=무제한)
    ):
        self.llm: BaseLLMClient = llm
        self.max_iterations = max_iterations
        self.tool_timeout_s = tool_timeout_s
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_tool_approval = on_tool_approval
        self.on_token = on_token
        self.max_tool_result_chars = max_tool_result_chars
        self.history_window = history_window
        self.TOOLS_SCHEMA = self.get_tools_schema()

    # ── 공개 인터페이스 ────────────────────────────────────────────────────────

    def run(
        self,
        user_message: str,
        history: list[Message] | None = None,
    ) -> LoopResult:
        """
        ReAct 루프를 돌려 최종 응답을 반환합니다.

        Args:
            user_message:  사용자 입력 문자열
            system_prompt: 에이전트 역할 지시 (None이면 기본값 사용)
            history:       이전 대화 히스토리 (멀티턴 지원)

        Returns:
            LoopResult — answer, stop_reason, 반복 기록 포함
        """
        messages: list[Message] = self.llm.build_messages(
            user_input=user_message, history=history
        )
        iterations: list[LoopIteration] = []
        total_input_tokens: int = 0
        total_output_tokens: int = 0
        _consecutive_missing_tool_use = 0  # tool_use 블록 없이 연속 발생 횟수

        for i in range(self.max_iterations):
            t0 = time.perf_counter()
            logger.debug("루프 반복 %d 시작", i + 1)

            # ── Reason: LLM 호출 ──────────────────────────────────────────────
            try:
                response: LLMResponse = self.llm.chat(
                    messages=messages,
                    tools=self.TOOLS_SCHEMA,
                )
            except Exception as exc:
                logger.error("LLM 호출 실패: %s", exc)
                return LoopResult(
                    answer=f"LLM 호출 중 오류가 발생했습니다: {exc}",
                    stop_reason=StopReason.LLM_ERROR,
                    iterations=iterations,
                    messages=messages,
                )

            # 토큰 누적 (LLMResponse 에 input_tokens/output_tokens 가 있을 때)
            if hasattr(response, "input_tokens"):
                total_input_tokens += response.input_tokens or 0
            if hasattr(response, "output_tokens"):
                total_output_tokens += response.output_tokens or 0

            # ── 종료 조건: 도구 없이 텍스트만 반환 ───────────────────────────
            if response.stop_reason == "end_turn":
                # on_token 이 설정된 경우 stream() 을 사용해 토큰 전달
                if self.on_token is not None:
                    try:
                        chunks = list(self.llm.stream(messages, tools=self.TOOLS_SCHEMA))
                        for chunk in chunks:
                            self.on_token(chunk)
                        final_text = "".join(chunks)
                    except Exception as exc:
                        logger.error("스트림 호출 실패: %s", exc)
                        return LoopResult(
                            answer=f"스트림 오류가 발생했습니다: {exc}",
                            stop_reason=StopReason.LLM_ERROR,
                            iterations=iterations,
                            messages=messages,
                            total_input_tokens=total_input_tokens,
                            total_output_tokens=total_output_tokens,
                        )
                else:
                    final_text = _extract_text(response.content)
                logger.debug("루프 종료 — end_turn (총 %d회 반복)", i + 1)
                return LoopResult(
                    answer=final_text,
                    stop_reason=StopReason.END_TURN,
                    iterations=iterations,
                    messages=messages,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                )

            # ── Act: tool_use 블록 수집 ───────────────────────────────────────
            tool_calls = _extract_tool_calls(response.content)
            if not tool_calls:
                _consecutive_missing_tool_use += 1
                if _consecutive_missing_tool_use >= 3:
                    # 3회 연속 tool_use 블록 없음 → 루프 종료 (무한 루프 방지)
                    logger.warning(
                        "tool_use stop_reason이지만 tool_use 블록이 %d회 연속 없음 — 루프 종료",
                        _consecutive_missing_tool_use,
                    )
                    return LoopResult(
                        answer=_extract_text(response.content),
                        stop_reason=StopReason.END_TURN,
                        iterations=iterations,
                        messages=messages,
                        total_input_tokens=total_input_tokens,
                        total_output_tokens=total_output_tokens,
                    )
                # 재시도 힌트 전달
                logger.warning(
                    "tool_use stop_reason이지만 tool_use 블록이 없음 — 재시도 힌트 전달 (반복 %d)", i + 1
                )
                messages.append(Message(role="assistant", content=response.content or []))
                messages.append(Message(
                    role="user",
                    content="도구를 호출하려고 했지만 tool_use 블록이 전달되지 않았습니다. "
                            "write_file 등 필요한 도구를 명시적으로 호출해 주세요.",
                ))
                continue

            _consecutive_missing_tool_use = 0  # 정상 tool_use → 카운터 리셋

            # assistant 턴을 히스토리에 추가
            messages.append(Message(role="assistant", content=response.content))
            # ── Observe: 도구 실행 ────────────────────────────────────────────
            tool_results: list[ToolResult] = []
            hard_stop = False

            for tc in tool_calls:
                if self.on_tool_call:
                    self.on_tool_call(tc)

                # 승인이 필요한 도구는 사용자 확인 후 실행
                if self.on_tool_approval and tc.name in _APPROVAL_REQUIRED:
                    approved = self.on_tool_approval(tc)
                    if not approved:
                        tr = ToolResult(
                            tool_use_id=tc.id,
                            content="사용자가 실행을 취소했습니다.",
                            is_error=True,
                        )
                        tool_results.append(tr)
                        if self.on_tool_result:
                            self.on_tool_result(tr)
                        continue

                tr = self._execute_tool(tc)
                tool_results.append(tr)

                if self.on_tool_result:
                    self.on_tool_result(tr)

                # 치명적 오류면 루프 종료
                if tr.is_error and _is_fatal_error(tr.content):
                    hard_stop = True
                    break

            # tool_results를 다음 user 턴으로 추가 (결과가 너무 크면 잘라냄)
            messages.append(
                Message(
                    role="user",
                    content=[
                        {
                            "type": "tool_result",
                            "tool_use_id": tr.tool_use_id,
                            "content": _truncate_tool_result(tr.content, self.max_tool_result_chars),
                            "is_error": tr.is_error,
                        }
                        for tr in tool_results
                    ],
                )
            )

            # 슬라이딩 윈도우: 초기 태스크 메시지 + 최근 history_window 쌍만 유지
            messages = _trim_history(messages, self.history_window)

            elapsed = (time.perf_counter() - t0) * 1000
            iterations.append(
                LoopIteration(
                    index=i + 1,
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                    elapsed_ms=elapsed,
                )
            )
            logger.debug(
                "반복 %d 완료 — %.1fms, 도구 %d개", i + 1, elapsed, len(tool_calls)
            )

            if hard_stop:
                return LoopResult(
                    answer="도구 실행 중 복구 불가능한 오류가 발생했습니다.",
                    stop_reason=StopReason.TOOL_ERROR,
                    iterations=iterations,
                    messages=messages,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                )

        # ── 최대 반복 초과 ────────────────────────────────────────────────────
        logger.warning("최대 반복 횟수(%d) 초과", self.max_iterations)
        return LoopResult(
            answer=f"최대 반복 횟수({self.max_iterations}회)를 초과했습니다. 작업을 더 작게 나눠 시도해주세요.",
            stop_reason=StopReason.MAX_ITER,
            iterations=iterations,
            messages=messages,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────
    def _execute_tool(self, tc: ToolCall) -> ToolResult:
        """
        단일 도구를 실행하고 ToolResult를 반환합니다.
        타임아웃·예외를 모두 여기서 처리합니다.
        """
        logger.debug("도구 실행: %s(%s)", tc.name, tc.input)
        try:
            result = call_tool(tc.name, **tc.input)
            if result.success:
                return ToolResult(
                    tool_use_id=tc.id,
                    content=result.output,
                    is_error=False,
                )
            else:
                return ToolResult(
                    tool_use_id=tc.id,
                    content=f"도구 오류 [{tc.name}]: {result.error}",
                    is_error=True,
                )
        except TypeError as exc:
            # 인자 이름이 맞지 않는 경우 (스키마 불일치)
            msg = f"도구 호출 인자 오류 [{tc.name}]: {exc}"
            logger.warning(msg)
            return ToolResult(tool_use_id=tc.id, content=msg, is_error=True)
        except Exception as exc:
            msg = f"도구 실행 중 예외 [{tc.name}]: {type(exc).__name__}: {exc}"
            logger.error(msg, exc_info=True)
            return ToolResult(tool_use_id=tc.id, content=msg, is_error=True)

    def get_tools_schema(self):
        llm_client = type(self.llm).__name__
        schema_dict = {
            "OpenaiClient": TOOLS_SCHEMA_OPENAI,
            "ClaudeClient": TOOLS_SCHEMA_ANTHROPIC,
            "OllamaClient": TOOLS_SCHEMA_OLLAMA,
        }
        TOOLS_SCHEMA = schema_dict.get(llm_client, None)
        if TOOLS_SCHEMA is None:
            raise ValueError(
                f"지원하지 않는 LLMClient: {llm_client!r} (OpenaiClient | ClaudeClient)"
            )
        return TOOLS_SCHEMA


# ── 모듈 수준 헬퍼 ────────────────────────────────────────────────────────────


def _extract_tool_calls(content: list) -> list[ToolCall]:
    """response.content에서 tool_use 블록만 추출"""
    calls = []
    for block in content:
        # SDK 객체(anthropic)와 dict(ollama 정규화) 모두 지원
        block_type = getattr(block, "type", None) or block.get("type")
        if block_type == "tool_use":
            calls.append(
                ToolCall(
                    id=getattr(block, "id", None) or block.get("id"),
                    name=getattr(block, "name", None) or block.get("name"),
                    input=getattr(block, "input", None) or block.get("input", {}),
                )
            )
    return calls


def _extract_text(content: list) -> str:
    """response.content에서 text 블록을 이어붙여 반환"""
    parts = []
    for block in content:
        block_type = getattr(block, "type", None) or block.get("type")
        if block_type == "text":
            text = getattr(block, "text", None) or block.get("text", "")
            parts.append(text)
    return "\n".join(parts).strip()


def _truncate_tool_result(content: str, max_chars: int) -> str:
    """
    도구 결과 문자열이 max_chars를 초과하면 앞부분만 남기고 잘라낸다.
    read_file 등으로 거대한 파일을 읽을 때 컨텍스트 폭발을 방지한다.
    """
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    dropped = len(content) - max_chars
    return content[:max_chars] + f"\n... [{dropped}자 생략 — 컨텍스트 한도 초과]"


def _trim_history(messages: list[Message], window: int) -> list[Message]:
    """
    초기 태스크 메시지(messages[0])를 유지하고,
    최근 window 쌍(assistant + tool_result)만 남긴다.

    각 쌍은 2개 메시지(assistant 턴 + user/tool_result 턴)로 구성되므로
    보존 기준은 1 + 2*window 개 메시지다.

    window=0이면 트리밍하지 않는다.
    """
    if window <= 0:
        return messages
    max_msgs = 1 + 2 * window
    if len(messages) <= max_msgs:
        return messages
    trimmed = len(messages) - max_msgs
    logger.debug("히스토리 트리밍: %d개 메시지 드롭 (window=%d)", trimmed, window)
    return [messages[0]] + messages[-(2 * window):]


def _is_fatal_error(error_message: str) -> bool:
    """
    루프를 즉시 중단해야 하는 치명적 오류인지 판별합니다.
    파일 없음, 권한 거부 등 재시도해도 의미 없는 오류는 False.
    """
    fatal_keywords = [
        "permission denied",
        "disk full",
        "out of memory",
        "killed",
    ]
    lower = error_message.lower()
    return any(kw in lower for kw in fatal_keywords)
