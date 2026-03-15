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
from enum import Enum
from typing import Any

from tools.registry import TOOLS_SCHEMA, call_tool

logger = logging.getLogger(__name__)


# ── 타입 정의 ─────────────────────────────────────────────────────────────────


class StopReason(str, Enum):
    END_TURN = "end_turn"  # LLM이 스스로 종료
    MAX_ITER = "max_iterations"  # 반복 한도 초과
    TOOL_ERROR = "tool_error"  # 도구 오류로 강제 종료
    LLM_ERROR = "llm_error"  # LLM 호출 실패


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
    messages: list[dict] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.stop_reason == StopReason.END_TURN

    @property
    def total_tool_calls(self) -> int:
        return sum(len(it.tool_calls) for it in self.iterations)


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
        on_tool_call=None,  # Callable[[ToolCall], None] — CLI 훅
        on_tool_result=None,  # Callable[[ToolResult], None] — CLI 훅
    ):
        self.llm = llm
        self.max_iterations = max_iterations
        self.tool_timeout_s = tool_timeout_s
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result

    # ── 공개 인터페이스 ────────────────────────────────────────────────────────

    def run(
        self,
        user_message: str,
        system_prompt: str | None = None,
        history: list[dict] | None = None,
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
        messages = self._build_initial_messages(user_message, history)
        iterations: list[LoopIteration] = []

        for i in range(self.max_iterations):
            t0 = time.perf_counter()
            logger.debug("루프 반복 %d 시작", i + 1)

            # ── Reason: LLM 호출 ──────────────────────────────────────────────
            try:
                response = self.llm.chat(
                    messages=messages,
                    tools=TOOLS_SCHEMA,
                    system=system_prompt or _DEFAULT_SYSTEM,
                )
            except Exception as exc:
                logger.error("LLM 호출 실패: %s", exc)
                return LoopResult(
                    answer=f"LLM 호출 중 오류가 발생했습니다: {exc}",
                    stop_reason=StopReason.LLM_ERROR,
                    iterations=iterations,
                    messages=messages,
                )

            # ── 종료 조건: 도구 없이 텍스트만 반환 ───────────────────────────
            if response.stop_reason == "end_turn":
                final_text = _extract_text(response.content)
                logger.debug("루프 종료 — end_turn (총 %d회 반복)", i + 1)
                return LoopResult(
                    answer=final_text,
                    stop_reason=StopReason.END_TURN,
                    iterations=iterations,
                    messages=messages,
                )

            # ── Act: tool_use 블록 수집 ───────────────────────────────────────
            tool_calls = _extract_tool_calls(response.content)
            if not tool_calls:
                # stop_reason이 tool_use인데 tool_use 블록이 없는 예외 상황
                logger.warning(
                    "tool_use stop_reason이지만 tool_use 블록이 없음 — 텍스트 반환"
                )
                return LoopResult(
                    answer=_extract_text(response.content),
                    stop_reason=StopReason.END_TURN,
                    iterations=iterations,
                    messages=messages,
                )

            # assistant 턴을 히스토리에 추가
            messages.append({"role": "assistant", "content": response.content})

            # ── Observe: 도구 실행 ────────────────────────────────────────────
            tool_results: list[ToolResult] = []
            hard_stop = False

            for tc in tool_calls:
                if self.on_tool_call:
                    self.on_tool_call(tc)

                tr = self._execute_tool(tc)
                tool_results.append(tr)

                if self.on_tool_result:
                    self.on_tool_result(tr)

                # 치명적 오류면 루프 종료
                if tr.is_error and _is_fatal_error(tr.content):
                    hard_stop = True
                    break

            # tool_results를 다음 user 턴으로 추가
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tr.tool_use_id,
                            "content": tr.content,
                            "is_error": tr.is_error,
                        }
                        for tr in tool_results
                    ],
                }
            )

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
                )

        # ── 최대 반복 초과 ────────────────────────────────────────────────────
        logger.warning("최대 반복 횟수(%d) 초과", self.max_iterations)
        return LoopResult(
            answer=f"최대 반복 횟수({self.max_iterations}회)를 초과했습니다. 작업을 더 작게 나눠 시도해주세요.",
            stop_reason=StopReason.MAX_ITER,
            iterations=iterations,
            messages=messages,
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _build_initial_messages(
        self,
        user_message: str,
        history: list[dict] | None,
    ) -> list[dict]:
        """히스토리 + 새 유저 메시지로 초기 messages 배열 구성"""
        messages = list(history or [])
        messages.append({"role": "user", "content": user_message})
        return messages

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


# ── 기본 시스템 프롬프트 ──────────────────────────────────────────────────────

_DEFAULT_SYSTEM = """\
당신은 로컬 파일 시스템에서 작동하는 코딩 에이전트입니다.

작업 원칙:
- 파일을 읽기 전에 내용을 가정하지 마세요. 반드시 read_file로 먼저 확인하세요.
- 파일 수정 시 edit_file을 우선 사용하세요. write_file은 새 파일 생성에만 씁니다.
- 한 번의 응답에서 여러 도구를 병렬로 호출해도 됩니다.
- 오류가 발생하면 원인을 분석하고 다른 방법으로 재시도하세요.
- 최종 답변에는 어떤 작업을 했는지 간결하게 요약하세요.
"""
