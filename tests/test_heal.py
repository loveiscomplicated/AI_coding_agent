"""
tests/test_heal.py — core/heal.py 단위 테스트

Module 1: Recursive ReAct Loop (Self-Healing)
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call
from dataclasses import dataclass

from core.heal import (
    ErrorClass,
    HealContext,
    classify_error,
    build_heal_prompt,
    _FATAL_KEYWORDS,
    _TRANSIENT_KEYWORDS,
)


# ── classify_error 테스트 ──────────────────────────────────────────────────────

class TestClassifyError:
    """classify_error() 에러 분류 로직 테스트."""

    def test_fatal_permission_denied(self):
        assert classify_error("write_file", "permission denied") == ErrorClass.FATAL

    def test_fatal_disk_full(self):
        assert classify_error("write_file", "disk full — no space left") == ErrorClass.FATAL

    def test_fatal_out_of_memory(self):
        assert classify_error("execute_command", "out of memory") == ErrorClass.FATAL

    def test_fatal_killed(self):
        assert classify_error("execute_command", "process killed by signal") == ErrorClass.FATAL

    def test_fatal_case_insensitive(self):
        assert classify_error("write_file", "PERMISSION DENIED") == ErrorClass.FATAL

    def test_transient_timeout(self):
        assert classify_error("execute_command", "connection timed out") == ErrorClass.TRANSIENT

    def test_transient_rate_limit(self):
        assert classify_error("read_file", "rate limit exceeded, retry after 60s") == ErrorClass.TRANSIENT

    def test_transient_connection_refused(self):
        assert classify_error("execute_command", "connection refused") == ErrorClass.TRANSIENT

    def test_transient_service_unavailable(self):
        assert classify_error("execute_command", "service temporarily unavailable") == ErrorClass.TRANSIENT

    def test_transient_429(self):
        assert classify_error("execute_command", "HTTP 429 too many requests") == ErrorClass.TRANSIENT

    def test_fixable_syntax_error(self):
        assert classify_error("execute_command", "SyntaxError: invalid syntax on line 5") == ErrorClass.FIXABLE

    def test_fixable_file_not_found(self):
        assert classify_error("read_file", "FileNotFoundError: No such file: 'foo.py'") == ErrorClass.FIXABLE

    def test_fixable_import_error(self):
        assert classify_error("execute_command", "ImportError: cannot import 'bar'") == ErrorClass.FIXABLE

    def test_fixable_assertion_error(self):
        assert classify_error("execute_command", "AssertionError: test_add FAILED") == ErrorClass.FIXABLE

    def test_fixable_generic_error(self):
        assert classify_error("edit_file", "old_str not found in file") == ErrorClass.FIXABLE

    def test_fatal_takes_priority_over_transient(self):
        # "permission denied" 와 "timeout" 이 모두 포함 → FATAL 우선
        msg = "permission denied: operation timed out"
        assert classify_error("write_file", msg) == ErrorClass.FATAL

    def test_empty_error_is_fixable(self):
        assert classify_error("read_file", "") == ErrorClass.FIXABLE

    def test_tool_name_does_not_affect_classification(self):
        # 도구 이름은 현재 분류에 영향을 주지 않음
        result1 = classify_error("read_file", "SyntaxError")
        result2 = classify_error("git_commit", "SyntaxError")
        assert result1 == result2 == ErrorClass.FIXABLE


# ── build_heal_prompt 테스트 ───────────────────────────────────────────────────

class TestBuildHealPrompt:
    """build_heal_prompt() 출력 형식 및 내용 테스트."""

    def _make_ctx(self, attempt=1, max_attempts=2, error_content="Some error"):
        return HealContext(
            tool_name="edit_file",
            tool_input={"path": "src/main.py", "old_str": "foo", "new_str": "bar"},
            error_content=error_content,
            attempt=attempt,
            max_attempts=max_attempts,
            error_class=ErrorClass.FIXABLE,
        )

    def test_contains_heal_request_header(self):
        prompt = build_heal_prompt(self._make_ctx())
        assert "[HEAL REQUEST]" in prompt

    def test_contains_tool_name(self):
        prompt = build_heal_prompt(self._make_ctx())
        assert "edit_file" in prompt

    def test_contains_error_content(self):
        prompt = build_heal_prompt(self._make_ctx(error_content="old_str not found"))
        assert "old_str not found" in prompt

    def test_contains_attempt_info(self):
        prompt = build_heal_prompt(self._make_ctx(attempt=1, max_attempts=3))
        assert "1" in prompt and "3" in prompt

    def test_last_attempt_warning(self):
        prompt = build_heal_prompt(self._make_ctx(attempt=2, max_attempts=2))
        assert "마지막" in prompt

    def test_not_last_attempt_no_warning(self):
        prompt = build_heal_prompt(self._make_ctx(attempt=1, max_attempts=3))
        assert "마지막" not in prompt

    def test_contains_do_not_repeat_instruction(self):
        prompt = build_heal_prompt(self._make_ctx())
        # 동일한 호출 반복 금지 지시 포함 확인
        assert "반복" in prompt or "unchanged" in prompt.lower()

    def test_long_error_truncated(self):
        long_error = "X" * 1000
        prompt = build_heal_prompt(self._make_ctx(error_content=long_error))
        # 600자 이상이 그대로 포함되면 안 됨
        assert len(prompt) < 2000  # 전체 프롬프트도 너무 길지 않아야 함

    def test_long_input_truncated(self):
        ctx = HealContext(
            tool_name="write_file",
            tool_input={"path": "a.py", "content": "A" * 500},
            error_content="error",
            attempt=1,
            max_attempts=2,
            error_class=ErrorClass.FIXABLE,
        )
        prompt = build_heal_prompt(ctx)
        assert len(prompt) < 3000


# ── ReactLoop Self-Healing 통합 테스트 ────────────────────────────────────────

@dataclass
class _FakeToolResult:
    tool_use_id: str
    content: str
    is_error: bool


class TestReactLoopSelfHealing:
    """
    ReactLoop 의 Self-Healing 동작을 최소 mock 으로 검증한다.
    LLM/도구 실제 호출 없음.
    """

    def _make_loop(self, **kwargs):
        """최소 설정의 ReactLoop 인스턴스 생성.

        get_tools_schema() 를 patch 해서 LLM 타입 검사를 우회한다.
        """
        from core.loop import ReactLoop
        from llm.base import LLMConfig

        mock_llm = MagicMock()
        mock_llm.config = LLMConfig(model="test-model", system_prompt="test")
        mock_llm.build_messages = MagicMock(return_value=[
            MagicMock(role="user", content="do the task")
        ])
        with patch.object(ReactLoop, "get_tools_schema", return_value=[]):
            loop = ReactLoop(llm=mock_llm, max_iterations=5, **kwargs)
        return loop, mock_llm

    def test_heal_disabled_behaves_like_original(self):
        """enable_healing=False: 기존 동작 — FIXABLE 에러도 그대로 통과."""
        from core.loop import ReactLoop, ToolResult
        from llm.base import StopReason

        loop, mock_llm = self._make_loop(enable_healing=False)

        # LLM → tool_use 1회 → end_turn
        response_tool = MagicMock()
        response_tool.stop_reason = "tool_use"
        response_tool.content = [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "x.py"}}]
        response_tool.input_tokens = 10
        response_tool.output_tokens = 5

        response_end = MagicMock()
        response_end.stop_reason = "end_turn"
        response_end.content = [{"type": "text", "text": "done"}]
        response_end.input_tokens = 10
        response_end.output_tokens = 5

        mock_llm.chat = MagicMock(side_effect=[response_tool, response_end])

        # 도구가 FIXABLE 에러 반환
        with patch("core.loop.call_tool") as mock_call:
            mock_schema_result = MagicMock()
            mock_schema_result.success = False
            mock_schema_result.error = "old_str not found in file"
            mock_schema_result.output = ""
            mock_call.return_value = mock_schema_result

            result = loop.run("task")

        # enable_healing=False: 힐 이벤트 없음
        assert result.heal_events == []

    def test_new_params_have_defaults(self):
        """신규 파라미터들이 기본값으로 초기화되는지 확인."""
        from core.loop import ReactLoop
        mock_llm = MagicMock()
        mock_llm.config = MagicMock(system_prompt="test")

        with patch.object(ReactLoop, "get_tools_schema", return_value=[]):
            loop = ReactLoop(llm=mock_llm)
        assert loop.max_heal_attempts == 2
        assert loop.enable_healing is True
        assert loop.verification_gate is None
        assert loop.context_pruner is None

    def test_loop_result_has_heal_events_field(self):
        """LoopResult 에 heal_events 필드가 있는지 확인."""
        from core.loop import LoopResult
        from llm.base import StopReason
        result = LoopResult(answer="ok", stop_reason=StopReason.END_TURN)
        assert hasattr(result, "heal_events")
        assert result.heal_events == []

    def test_heal_attempt_tracker(self):
        """HealAttemptTracker 가 올바르게 카운트를 추적하는지."""
        from core.loop import HealAttemptTracker
        tracker = HealAttemptTracker()
        assert tracker.get("t1") == 0
        assert tracker.increment("t1") == 1
        assert tracker.increment("t1") == 2
        assert tracker.get("t1") == 2
        assert tracker.get("t2") == 0  # 다른 ID는 독립적

    def test_loop_phase_enum(self):
        """LoopPhase 열거형 값 확인."""
        from core.loop import LoopPhase
        assert LoopPhase.PLAN.value == "plan"
        assert LoopPhase.HEAL.value == "heal"
