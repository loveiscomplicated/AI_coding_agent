"""
tests/test_token_usage.py

토큰 사용량 추적 및 표시 테스트.

설계:
  - LoopResult 에 total_input_tokens, total_output_tokens 필드 추가
  - ReactLoop 이 각 LLM 호출의 토큰을 누적
  - cli/interface.py 에 print_token_usage(result) 함수 추가
  - 각 응답 후 자동으로 토큰 통계 출력 (옵션)

아직 구현되지 않음 — 처음엔 실패한다.

실행:
    pytest tests/test_token_usage.py -v
"""

from __future__ import annotations

import io
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.loop import ReactLoop, LoopResult, StopReason
from llm.base import Message, LLMResponse
from cli import interface as ui
from rich.console import Console


# ── Mock 헬퍼 ──────────────────────────────────────────────────────────────────


def _response(text: str, input_tokens: int = 100, output_tokens: int = 50):
    return LLMResponse(
        content=[{"type": "text", "text": text}],
        model="test-model",
        stop_reason="end_turn",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _tool_response_with_tokens(
    tool_id: str, name: str, input_: dict,
    input_tokens: int = 200, output_tokens: int = 30,
):
    return LLMResponse(
        content=[{"type": "tool_use", "id": tool_id, "name": name, "input": input_}],
        model="test-model",
        stop_reason="tool_use",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class _MockLLM:
    def __init__(self, responses):
        self._iter = iter(responses)

    def build_messages(self, user_input, history=None):
        msgs = [Message(role=h.role, content=h.content) for h in (history or [])]
        msgs.append(Message(role="user", content=user_input))
        return msgs

    def chat(self, messages, **kwargs):
        return next(self._iter)


@pytest.fixture(autouse=True)
def patch_schema(monkeypatch):
    monkeypatch.setattr(ReactLoop, "get_tools_schema", lambda self: [])


@pytest.fixture
def captured():
    buf = io.StringIO()
    test_console = Console(file=buf, highlight=False, markup=False, width=120)
    with patch("cli.interface.console", test_console):
        yield buf


# ── LoopResult 토큰 필드 ───────────────────────────────────────────────────────


class TestLoopResultTokenFields:
    def test_has_total_input_tokens_field(self):
        result = LoopResult(answer="ok", stop_reason=StopReason.END_TURN)
        assert hasattr(result, "total_input_tokens")

    def test_has_total_output_tokens_field(self):
        result = LoopResult(answer="ok", stop_reason=StopReason.END_TURN)
        assert hasattr(result, "total_output_tokens")

    def test_default_tokens_are_zero(self):
        result = LoopResult(answer="ok", stop_reason=StopReason.END_TURN)
        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0

    def test_total_tokens_property(self):
        result = LoopResult(
            answer="ok",
            stop_reason=StopReason.END_TURN,
            total_input_tokens=100,
            total_output_tokens=50,
        )
        assert result.total_tokens == 150


# ── ReactLoop 토큰 누적 ────────────────────────────────────────────────────────


class TestReactLoopTokenAccumulation:
    def test_single_turn_tokens_recorded(self):
        """단일 턴 응답의 토큰이 LoopResult 에 기록된다."""
        llm = _MockLLM([_response("ok", input_tokens=100, output_tokens=50)])
        loop = ReactLoop(llm=llm)
        result = loop.run("hi")

        assert result.total_input_tokens == 100
        assert result.total_output_tokens == 50

    def test_multi_turn_tokens_accumulated(self, tmp_path):
        """여러 턴의 토큰이 합산된다."""
        f = tmp_path / "x.txt"
        f.write_text("data", encoding="utf-8")

        llm = _MockLLM([
            _tool_response_with_tokens("id1", "read_file", {"path": str(f)},
                                       input_tokens=200, output_tokens=30),
            _response("done", input_tokens=150, output_tokens=60),
        ])
        loop = ReactLoop(llm=llm)
        result = loop.run("read file")

        assert result.total_input_tokens == 350   # 200 + 150
        assert result.total_output_tokens == 90   # 30 + 60

    def test_zero_tokens_when_llm_error(self):
        """LLM 호출 실패 시 토큰은 0이어야 한다."""
        from unittest.mock import MagicMock
        llm = MagicMock()
        llm.build_messages.return_value = [Message(role="user", content="hi")]
        llm.chat.side_effect = RuntimeError("fail")

        loop = ReactLoop(llm=llm)
        result = loop.run("hi")

        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0

    def test_tokens_accumulate_across_max_iter(self, tmp_path):
        """max_iterations 초과로 종료돼도 누적 토큰이 기록된다."""
        f = tmp_path / "x.txt"
        f.write_text("x", encoding="utf-8")

        responses = [
            _tool_response_with_tokens("id", "read_file", {"path": str(f)},
                                       input_tokens=10, output_tokens=5)
        ] * 5
        llm = _MockLLM(responses)
        loop = ReactLoop(llm=llm, max_iterations=3)
        result = loop.run("loop")

        assert result.total_input_tokens == 30   # 3회 * 10
        assert result.total_output_tokens == 15  # 3회 * 5


# ── print_token_usage ──────────────────────────────────────────────────────────


class TestPrintTokenUsage:
    def _make_result(self, input_t=100, output_t=50):
        return LoopResult(
            answer="ok",
            stop_reason=StopReason.END_TURN,
            total_input_tokens=input_t,
            total_output_tokens=output_t,
        )

    def test_shows_input_tokens(self, captured):
        ui.print_token_usage(self._make_result(input_t=123))
        assert "123" in captured.getvalue()

    def test_shows_output_tokens(self, captured):
        ui.print_token_usage(self._make_result(output_t=456))
        assert "456" in captured.getvalue()

    def test_shows_total_tokens(self, captured):
        ui.print_token_usage(self._make_result(input_t=100, output_t=50))
        assert "150" in captured.getvalue()

    def test_zero_tokens_still_prints(self, captured):
        """토큰이 0이어도 크래시 없이 출력돼야 한다."""
        ui.print_token_usage(self._make_result(input_t=0, output_t=0))
        out = captured.getvalue()
        assert "0" in out

    def test_large_token_count_formatted(self, captured):
        """큰 숫자가 읽기 좋게 출력된다 (예: 1,000 or 1000)."""
        ui.print_token_usage(self._make_result(input_t=100_000, output_t=50_000))
        out = captured.getvalue()
        # 숫자 어딘가에 있어야 함
        assert "100" in out and "50" in out

    def test_output_contains_label(self, captured):
        """'token' 또는 '토큰' 같은 레이블이 포함돼야 한다."""
        ui.print_token_usage(self._make_result())
        out = captured.getvalue().lower()
        assert "token" in out or "토큰" in out
