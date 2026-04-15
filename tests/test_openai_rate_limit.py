from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("openai")

from llm.openai_client import _parse_retry_after, _rate_limit_delay


class _FakeRateLimitError(Exception):
    def __init__(self, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.response = SimpleNamespace(headers=headers or {})


def test_parse_retry_after_from_header():
    err = _FakeRateLimitError("rate limit", {"retry-after": "2.5"})
    assert _parse_retry_after(err) == 2.5


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Please try again in 1.25s", 1.25),
        ("Please try again in 750ms", 0.75),
    ],
)
def test_parse_retry_after_from_error_message(message: str, expected: float):
    err = _FakeRateLimitError(message)
    assert _parse_retry_after(err) == expected


def test_parse_retry_after_returns_none_without_hint():
    err = _FakeRateLimitError("rate limited")
    assert _parse_retry_after(err) is None


def test_rate_limit_delay_uses_suggested_value_directly(monkeypatch):
    # rate_limiter.poison() 이 공통 해제 시각을 담당하므로 suggested 값에
    # 더 이상 jitter 를 더하지 않는다.
    err = _FakeRateLimitError("Please try again in 1s")
    monkeypatch.setattr("llm.openai_client.random.uniform", lambda a, b: 0.4)

    delay = _rate_limit_delay(2, err)
    assert delay == 1.0


def test_rate_limit_delay_uses_backoff_when_suggestion_missing(monkeypatch):
    err = _FakeRateLimitError("rate limited")
    monkeypatch.setattr("llm.openai_client.random.uniform", lambda a, b: 0.5)

    delay = _rate_limit_delay(1, err)
    # _BASE_DELAY=2.0, attempt=1 -> base=4.0
    assert delay == 4.5
