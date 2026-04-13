"""
core/loop.py — ReAct 루프 (Reason → Act → Observe → Heal → Replan)

흐름:
  1. LLM에 메시지 + 도구 스키마 전송
  2. tool_use 블록 감지 → 실제 도구 실행
  3. TRANSIENT 에러: 자동 재시도 (LLM 관여 없음)
     FIXABLE 에러: 힐 프롬프트 주입 → LLM 재계획 (Self-Healing)
     FATAL 에러: 즉시 루프 종료
  4. tool_result를 다시 LLM에 전달
  5. stop_reason == "end_turn"이면 종료

신규 파라미터:
  max_heal_attempts  : FIXABLE 에러당 LLM 재시도 한도 (기본 2)
  enable_healing     : False → 기존 동작 완벽 보존
  verification_gate  : VerificationGate 인스턴스 (git_commit 전 검증)
  context_pruner     : SemanticContextPruner 인스턴스 (시맨틱 컨텍스트 관리)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from typing import Any, Callable

from tools.registry import (
    TOOLS_SCHEMA_ANTHROPIC,
    TOOLS_SCHEMA_OPENAI,
    TOOLS_SCHEMA_OLLAMA,
    call_tool,
)

import re

from llm.base import Message, LLMResponse, BaseLLMClient, StopReason

logger = logging.getLogger(__name__)

# 실행 전 사용자 승인이 필요한 도구 목록
_APPROVAL_REQUIRED = {"write_file", "edit_file", "append_to_file", "execute_command", "git_commit"}

# 파일 쓰기 도구 (코드블록 감지 → 도구 사용 유도에 사용)
_WRITE_TOOLS = frozenset({"write_file", "edit_file", "append_to_file"})


# ── 타입 정의 ─────────────────────────────────────────────────────────────────

class LoopPhase(str, Enum):
    """ReAct 루프의 실행 단계."""
    PLAN    = "plan"
    EXECUTE = "execute"
    OBSERVE = "observe"
    HEAL    = "heal"
    REPLAN  = "replan"


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
    phase: LoopPhase = LoopPhase.OBSERVE


@dataclass
class HealEvent:
    """자가 수정(Self-Healing) 이벤트 기록."""
    iteration: int
    tool_name: str
    attempt: int
    error_class: str
    error_summary: str


@dataclass
class HealAttemptTracker:
    """루프 전체에서 도구별 힐 시도 횟수를 추적한다."""
    counts: dict[str, int] = field(default_factory=dict)

    def increment(self, tool_use_id: str) -> int:
        self.counts[tool_use_id] = self.counts.get(tool_use_id, 0) + 1
        return self.counts[tool_use_id]

    def get(self, tool_use_id: str) -> int:
        return self.counts.get(tool_use_id, 0)


@dataclass
class LoopResult:
    """run()의 최종 반환값"""

    answer: str
    stop_reason: StopReason
    iterations: list[LoopIteration] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    heal_events: list[HealEvent] = field(default_factory=list)  # 자가 수정 이벤트 기록

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
        max_iterations: int = 15,
        tool_timeout_s: float = 30.0,  # 도구 실행 타임아웃 (초)
        on_tool_call=None,     # Callable[[ToolCall], None] — CLI 훅
        on_tool_result=None,   # Callable[[ToolResult], None] — CLI 훅
        on_tool_approval=None, # Callable[[ToolCall], bool] — 승인 요청 훅
        on_token: Callable[[str], None] | None = None,  # 스트리밍 콜백
        on_iteration: Callable[[dict], None] | None = None,  # 매 ReAct 반복 완료 후 훅
        max_tool_result_chars: int = 4000,  # 도구 결과 최대 문자 수 (초과 시 잘림)
        history_window: int = 6,  # 보존할 최근 turn 쌍 수 (0=무제한)
        write_deadline: int | None = None,  # 이 반복 수 내 write 도구 미호출 시 WRITE_LOOP 종료
        stop_check: Callable[[], bool] | None = None,  # True 반환 시 LLM 호출 전 즉시 중단
        # ── 신규: 4개 모듈 파라미터 ──────────────────────────────────────────
        max_heal_attempts: int = 2,      # [Module 1] FIXABLE 에러당 LLM 재시도 한도
        enable_healing: bool = True,     # [Module 1] False → 기존 에러 동작 완벽 보존
        verification_gate=None,          # [Module 4] VerificationGate | None
        context_pruner=None,             # [Module 3] SemanticContextPruner | None
    ):
        self.llm: BaseLLMClient = llm
        self.max_iterations = max_iterations
        self.tool_timeout_s = tool_timeout_s
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_tool_approval = on_tool_approval
        self.on_token = on_token
        self.on_iteration = on_iteration
        self.max_tool_result_chars = max_tool_result_chars
        self.history_window = history_window
        self.write_deadline = write_deadline
        self.stop_check = stop_check
        self.max_heal_attempts = max_heal_attempts
        self.enable_healing = enable_healing
        self.verification_gate = verification_gate
        self.context_pruner = context_pruner
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
        heal_events: list[HealEvent] = []
        total_input_tokens: int = 0
        total_output_tokens: int = 0
        _consecutive_missing_tool_use = 0  # tool_use 블록 없이 연속 발생 횟수
        _write_tool_used = False  # 루프 동안 쓰기 도구가 호출되었는지 추적
        _nudge_attempted = False  # 코드블록 감지 → 도구 사용 유도를 이미 시도했는지
        _heal_tracker = HealAttemptTracker()  # [Module 1] 도구별 힐 시도 횟수

        for i in range(self.max_iterations):
            # ── 중단 체크: LLM 호출 전 ────────────────────────────────────────
            if self.stop_check and self.stop_check():
                logger.info("stop_check 트리거 — 루프 즉시 종료 (반복 %d)", i + 1)
                return LoopResult(
                    answer="[ABORTED] 사용자 즉시 중단 요청",
                    stop_reason=StopReason.ABORTED,
                    iterations=iterations,
                    messages=messages,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    heal_events=heal_events,
                )

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
                    heal_events=heal_events,
                )

            # 토큰 누적 (LLMResponse 에 input_tokens/output_tokens 가 있을 때)
            if hasattr(response, "input_tokens"):
                total_input_tokens += response.input_tokens or 0
            if hasattr(response, "output_tokens"):
                total_output_tokens += response.output_tokens or 0

            # ── 종료 조건: 도구 없이 텍스트만 반환 ───────────────────────────
            if response.stop_reason == "end_turn":
                final_text = _extract_text(response.content)

                # 코드 블록이 포함된 텍스트를 출력했지만 write_file 등 쓰기 도구를
                # 한 번도 호출하지 않은 경우 — 도구 사용을 유도하는 재시도
                if (
                    not _write_tool_used
                    and not _nudge_attempted
                    and _has_code_block(final_text)
                    and self.TOOLS_SCHEMA  # 도구가 제공되었을 때만
                ):
                    _nudge_attempted = True
                    logger.warning(
                        "end_turn 응답에 코드 블록이 있지만 write_file 미호출 — 도구 사용 유도"
                    )
                    messages.append(Message(role="assistant", content=response.content or []))
                    messages.append(Message(
                        role="user",
                        content=(
                            "코드를 텍스트로 출력했지만, 실제 파일이 생성되지 않았습니다. "
                            "반드시 `write_file` 도구를 호출하여 파일을 생성하세요. "
                            "텍스트로 코드를 보여주는 것은 파일 생성으로 인정되지 않습니다."
                        ),
                    ))
                    continue

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
                logger.debug("루프 종료 — end_turn (총 %d회 반복)", i + 1)
                return LoopResult(
                    answer=final_text,
                    stop_reason=StopReason.END_TURN,
                    iterations=iterations,
                    messages=messages,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    heal_events=heal_events,
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

            # ── Execute + Observe: 도구 실행 (Self-Healing 포함) ─────────────
            tool_results: list[ToolResult] = []
            hard_stop = False
            heal_injected = False  # 이번 iteration 에서 힐 프롬프트를 주입했는지

            for tc in tool_calls:
                # 쓰기 도구 사용 추적
                if tc.name in _WRITE_TOOLS:
                    _write_tool_used = True

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

                # [Module 1] TRANSIENT 에러 자동 재시도 (LLM 관여 없음)
                tr = self._execute_tool_with_transient_retry(tc)
                tool_results.append(tr)

                if self.on_tool_result:
                    self.on_tool_result(tr)

                if tr.is_error:
                    if self.enable_healing:
                        # [Module 1] 에러 분류 → 처리 방식 결정
                        from core.heal import classify_error, ErrorClass, HealContext, build_heal_prompt
                        ec = classify_error(tc.name, tr.content)

                        if ec == ErrorClass.FATAL or _is_fatal_error(tr.content):
                            hard_stop = True
                            break

                        if ec == ErrorClass.FIXABLE:
                            attempt = _heal_tracker.increment(tc.id)
                            logger.warning(
                                "[HEAL] '%s' FIXABLE 에러 (attempt %d/%d): %s",
                                tc.name, attempt, self.max_heal_attempts,
                                tr.content[:120],
                            )
                            heal_events.append(HealEvent(
                                iteration=i + 1,
                                tool_name=tc.name,
                                attempt=attempt,
                                error_class=ec.value,
                                error_summary=tr.content[:200],
                            ))

                            if attempt <= self.max_heal_attempts:
                                # 부분 tool_results + 힐 프롬프트를 메시지에 주입
                                partial_content = [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": r.tool_use_id,
                                        "content": _truncate_tool_result(
                                            r.content, self.max_tool_result_chars
                                        ),
                                        "is_error": r.is_error,
                                    }
                                    for r in tool_results
                                ]
                                messages.append(
                                    Message(role="user", content=partial_content)
                                )
                                heal_prompt = build_heal_prompt(HealContext(
                                    tool_name=tc.name,
                                    tool_input=tc.input,
                                    error_content=tr.content,
                                    attempt=attempt,
                                    max_attempts=self.max_heal_attempts,
                                    error_class=ec,
                                ))
                                messages.append(
                                    Message(role="user", content=heal_prompt)
                                )
                                heal_injected = True
                                break  # tool_calls 루프 탈출 → outer for-loop 다음 iteration
                            else:
                                # 힐 한도 초과 → FATAL 처리
                                logger.error(
                                    "[HEAL] '%s' 최대 힐 시도(%d) 초과 — 루프 종료",
                                    tc.name, self.max_heal_attempts,
                                )
                                hard_stop = True
                                break
                    else:
                        # enable_healing=False: 기존 동작 (FATAL 키워드만 검사)
                        if _is_fatal_error(tr.content):
                            hard_stop = True
                            break

            # 힐 프롬프트를 주입한 경우 tool_results 블록을 다시 추가하지 않는다
            if not heal_injected:
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

            # [Module 3] 시맨틱 컨텍스트 pruner 또는 기존 슬라이딩 윈도우
            if self.context_pruner is not None:
                messages = self.context_pruner.fit(messages)
            else:
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
            if self.on_iteration:
                self.on_iteration({
                    "iteration": i + 1,
                    "tool_calls": [
                        {"name": tc.name, "input_preview": repr(tc.input)[:150]}
                        for tc in tool_calls
                    ],
                    "elapsed_ms": round(elapsed, 1),
                })
            logger.debug(
                "반복 %d 완료 — %.1fms, 도구 %d개", i + 1, elapsed, len(tool_calls)
            )

            # write_deadline: N회 반복 후에도 쓰기 도구가 한 번도 호출되지 않으면 조기 종료
            if (
                self.write_deadline is not None
                and not _write_tool_used
                and (i + 1) >= self.write_deadline
            ):
                logger.warning(
                    "write_deadline(%d회) 도달 — 쓰기 도구 미호출 (탐색 루프 감지), 조기 종료",
                    self.write_deadline,
                )
                return LoopResult(
                    answer=(
                        f"{self.write_deadline}회 탐색했지만 write_file/edit_file을 "
                        "호출하지 않았습니다. 테스트 파일이 태스크 스펙을 반영하지 않거나 "
                        "구현 방향을 특정할 수 없는 상태입니다."
                    ),
                    stop_reason=StopReason.WRITE_LOOP,
                    iterations=iterations,
                    messages=messages,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    heal_events=heal_events,
                )

            if hard_stop:
                return LoopResult(
                    answer="도구 실행 중 복구 불가능한 오류가 발생했습니다.",
                    stop_reason=StopReason.TOOL_ERROR,
                    iterations=iterations,
                    messages=messages,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    heal_events=heal_events,
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
            heal_events=heal_events,
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _execute_tool_with_transient_retry(
        self, tc: ToolCall, max_retries: int = 2
    ) -> ToolResult:
        """
        [Module 1] TRANSIENT 에러 시 LLM 없이 자동 재시도.

        대기 시간: 0.5s → 1.0s (지수 백오프).
        TRANSIENT 가 아닌 에러는 즉시 반환 (재시도 없음).

        Args:
            tc:          실행할 도구 호출
            max_retries: 최대 재시도 횟수 (기본 2)

        Returns:
            ToolResult — 성공 or 마지막 시도 결과
        """
        from core.heal import classify_error, ErrorClass

        for attempt in range(max_retries + 1):
            tr = self._execute_tool(tc)
            if not tr.is_error:
                return tr
            # TRANSIENT 가 아니면 즉시 반환 (힐 로직이 처리)
            if classify_error(tc.name, tr.content) != ErrorClass.TRANSIENT:
                return tr
            if attempt < max_retries:
                delay = 0.5 * (2 ** attempt)
                logger.warning(
                    "[TRANSIENT RETRY] '%s' — %d/%d, %.1fs 대기",
                    tc.name, attempt + 1, max_retries, delay,
                )
                time.sleep(delay)
        return tr  # type: ignore[return-value]  # 루프 후 tr 은 항상 할당됨

    def _execute_tool(self, tc: ToolCall) -> ToolResult:
        """
        단일 도구를 실행하고 ToolResult를 반환합니다.
        타임아웃·예외를 모두 여기서 처리합니다.

        [Module 4] git_commit 도구는 verification_gate 가 설정된 경우
        실제 커밋 전에 검증 커맨드를 실행한다. 검증 실패 시 에러 ToolResult 를 반환.
        """
        # ── [Module 4] git_commit 검증 게이트 ────────────────────────────────
        if tc.name == "git_commit" and self.verification_gate is not None:
            repo_path = tc.input.get("repo_path", ".")
            logger.info("[VERIFICATION GATE] git_commit 전 검증 시작 (repo=%s)", repo_path)
            gate_result = self.verification_gate.check(repo_path)
            if not gate_result.all_passed:
                logger.warning(
                    "[VERIFICATION GATE] 검증 실패 — git_commit 차단 (repo=%s)", repo_path
                )
                return ToolResult(
                    tool_use_id=tc.id,
                    content=gate_result.failure_summary,
                    is_error=True,
                )
            logger.info("[VERIFICATION GATE] 검증 통과 — git_commit 진행")

        # ── 도구 실행 ─────────────────────────────────────────────────────────
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
            "GlmClient": TOOLS_SCHEMA_OPENAI,
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

# 마크다운 코드 블록 패턴 (```python ... ``` 등)
_CODE_BLOCK_RE = re.compile(r"```\w*\n.+?\n```", re.DOTALL)


def _has_code_block(text: str) -> bool:
    """텍스트에 마크다운 코드 블록(```...```)이 포함되어 있는지 확인한다."""
    return bool(_CODE_BLOCK_RE.search(text))


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
    최근 window 턴만 남긴다.

    한 턴 = (assistant 메시지, user 메시지) 쌍. 쌍을 원자 단위로 취급하므로
    assistant(tool_calls)와 그에 대응하는 user(tool_results)가 분리되지 않는다.
    GLM·OpenAI·Claude 모두 tool_call ↔ tool_result 쌍이 깨지면 오류를 반환한다.

    window=0이면 트리밍하지 않는다.
    """
    if window <= 0:
        return messages

    # messages[0]은 초기 user(task). 이후 메시지를 (assistant, user) 쌍으로 묶는다.
    turns: list[tuple[int, int | None]] = []
    i = 1
    while i < len(messages):
        if messages[i].role == "assistant":
            if i + 1 < len(messages) and messages[i + 1].role == "user":
                turns.append((i, i + 1))
                i += 2
            else:
                turns.append((i, None))
                i += 1
        else:
            i += 1

    if len(turns) <= window:
        return messages

    dropped = len(turns) - window
    logger.debug("히스토리 트리밍: %d턴 드롭 (window=%d)", dropped, window)
    kept = turns[-window:]
    start_idx = kept[0][0]
    return [messages[0]] + messages[start_idx:]


def _is_fatal_error(error_message: str) -> bool:
    """
    루프를 즉시 중단해야 하는 치명적 오류인지 판별합니다.

    core/heal.py 의 classify_error() 로 위임한다.
    enable_healing=False 인 경우에도 이 함수로 FATAL 에러를 검출하므로
    backward-compatible 으로 유지한다.
    """
    from core.heal import classify_error, ErrorClass
    return classify_error("", error_message) == ErrorClass.FATAL
