"""tests/test_retry_prompt.py — RetryPrompt 단위 테스트.

inline_select와 cli.interface._prompt_session.prompt를 mock해서
사용자 응답을 시뮬레이션한다.
"""

from __future__ import annotations

import pytest

from cli import interface as ui
from cli.retry_prompt import RetryDecision, RetryPrompt


def _patch_select(monkeypatch, return_values, captured=None):
    if isinstance(return_values, str) or return_values is None:
        values = iter([return_values])
    else:
        values = iter(return_values)

    def fake_select(options, message=None, detail=None, default_index=0):
        if captured is not None:
            captured.append({
                "options": list(options),
                "message": message,
                "detail": detail,
            })
        return next(values)

    monkeypatch.setattr("cli.retry_prompt.inline_select", fake_select)


def _patch_prompt(monkeypatch, hint_values):
    """_prompt_session.prompt를 mock — hint_values는 단일 str 또는 시퀀스."""
    if isinstance(hint_values, str):
        values = iter([hint_values])
    else:
        values = iter(hint_values)

    def fake_prompt(*args, **kwargs):
        return next(values)

    monkeypatch.setattr(ui._prompt_session, "prompt", fake_prompt)


def test_test_failure_retry(monkeypatch):
    _patch_select(monkeypatch, "retry")
    decision = RetryPrompt().ask_on_test_failure("실패 요약", auto_retry_count=2)
    assert decision == RetryDecision(action="retry")


def test_test_failure_hint(monkeypatch):
    _patch_select(monkeypatch, "retry_with_hint")
    _patch_prompt(monkeypatch, "  내 힌트  ")  # strip되어 "내 힌트"
    decision = RetryPrompt().ask_on_test_failure("실패 요약", auto_retry_count=2)
    assert decision.action == "retry_with_hint"
    assert decision.hint == "내 힌트"


def test_test_failure_hint_empty_reprompts(monkeypatch):
    _patch_select(monkeypatch, "retry_with_hint")
    # 3회 모두 빈 입력 → _get_hint가 None 반환 → quit으로 강등
    _patch_prompt(monkeypatch, ["", "  ", ""])
    decision = RetryPrompt().ask_on_test_failure("실패 요약")
    assert decision == RetryDecision(action="quit")


def test_test_failure_hint_esc_quits(monkeypatch):
    """힌트 입력 중 Esc(EOFError) → 즉시 quit, 재프롬프트 없음."""
    _patch_select(monkeypatch, "retry_with_hint")

    call_count = [0]

    def raise_eof(*args, **kwargs):
        call_count[0] += 1
        raise EOFError()

    monkeypatch.setattr(ui._prompt_session, "prompt", raise_eof)
    decision = RetryPrompt().ask_on_test_failure("실패 요약")

    assert decision == RetryDecision(action="quit")
    assert call_count[0] == 1  # 재프롬프트 없이 첫 번째 Esc에 즉시 종료


def test_test_failure_quit(monkeypatch):
    _patch_select(monkeypatch, "quit")
    decision = RetryPrompt().ask_on_test_failure("실패 요약")
    assert decision == RetryDecision(action="quit")


def test_test_failure_escape(monkeypatch):
    _patch_select(monkeypatch, None)  # Esc → None
    decision = RetryPrompt().ask_on_test_failure("실패 요약")
    assert decision == RetryDecision(action="quit")


def test_review_rejected_ignore(monkeypatch):
    _patch_select(monkeypatch, "ignore")
    decision = RetryPrompt().ask_on_review_rejected("리뷰어 피드백")
    assert decision == RetryDecision(action="ignore")


def test_pipeline_error_no_ignore(monkeypatch):
    captured: list[dict] = []
    _patch_select(monkeypatch, "retry", captured=captured)
    decision = RetryPrompt().ask_on_pipeline_error("docker error")
    assert decision == RetryDecision(action="retry")

    assert len(captured) == 1
    values = {opt.value for opt in captured[0]["options"]}
    assert "ignore" not in values
    assert values == {"retry", "quit"}
