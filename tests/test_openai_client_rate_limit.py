from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("openai")

import llm.openai_client as openai_client
from llm.base import LLMConfig, Message
from llm.openai_client import OpenaiClient


class _FakeRateLimitError(Exception):
    def __init__(self, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.response = SimpleNamespace(headers=headers or {})


def _make_client(create_mock: MagicMock) -> OpenaiClient:
    client = OpenaiClient.__new__(OpenaiClient)
    client.config = LLMConfig(
        model="gpt-4.1-mini",
        system_prompt="system",
        max_tokens=128,
        temperature=0.1,
    )
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create_mock)
        )
    )
    return client


def _chat_response(text: str = "ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        model="gpt-4.1-mini",
    )


def _stream_chunks(*parts: str):
    return [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=part))]
        )
        for part in parts
    ]


def test_chat_retries_on_rate_limit_then_succeeds(monkeypatch):
    create_mock = MagicMock(side_effect=[_FakeRateLimitError("try again in 120ms"), _chat_response("done")])
    client = _make_client(create_mock)
    slept: list[float] = []

    monkeypatch.setattr(openai_client, "RateLimitError", _FakeRateLimitError)
    monkeypatch.setattr(openai_client, "_rate_limit_delay", lambda attempt, e: 0.25)
    monkeypatch.setattr(openai_client.time, "sleep", lambda sec: slept.append(sec))

    resp = client.chat([Message(role="user", content="hello")])

    assert create_mock.call_count == 2
    assert slept == [0.25]
    assert resp.content[0]["text"] == "done"
    assert resp.input_tokens == 11
    assert resp.output_tokens == 7


def test_chat_raises_after_max_retries(monkeypatch):
    create_mock = MagicMock(side_effect=_FakeRateLimitError("rate limited"))
    client = _make_client(create_mock)
    slept: list[float] = []

    monkeypatch.setattr(openai_client, "RateLimitError", _FakeRateLimitError)
    monkeypatch.setattr(openai_client, "_MAX_RETRIES", 2)
    monkeypatch.setattr(openai_client, "_rate_limit_delay", lambda attempt, e: 0.1)
    monkeypatch.setattr(openai_client.time, "sleep", lambda sec: slept.append(sec))

    with pytest.raises(_FakeRateLimitError):
        client.chat([Message(role="user", content="hello")])

    assert create_mock.call_count == 3
    assert slept == [0.1, 0.1]


def test_stream_retries_on_rate_limit_then_yields(monkeypatch):
    create_mock = MagicMock(
        side_effect=[_FakeRateLimitError("Please try again in 1s"), _stream_chunks("hel", "lo")]
    )
    client = _make_client(create_mock)
    slept: list[float] = []

    monkeypatch.setattr(openai_client, "RateLimitError", _FakeRateLimitError)
    monkeypatch.setattr(openai_client, "_rate_limit_delay", lambda attempt, e: 0.3)
    monkeypatch.setattr(openai_client.time, "sleep", lambda sec: slept.append(sec))

    out = list(client.stream([Message(role="user", content="stream")]))

    assert out == ["hel", "lo"]
    assert create_mock.call_count == 2
    assert slept == [0.3]
