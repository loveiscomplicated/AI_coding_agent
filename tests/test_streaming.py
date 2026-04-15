"""
tests/test_streaming.py

스트리밍 출력 기능 테스트.

설계:
  ReactLoop(on_token=callback) 으로 생성하면
  최종 응답(end_turn) 시 LLM.stream() 을 사용하여
  토큰을 on_token 콜백으로 실시간 전달한다.
  도구 호출 중간 턴은 기존 chat() 그대로 사용.

chat-then-stream 설계:
  1. 매 턴마다 chat() 으로 stop_reason 확인
  2. stop_reason == end_turn 이고 on_token 이 설정된 경우 → stream() 호출
  3. stop_reason == tool_use → 기존 도구 처리 경로

실행:
    pytest tests/test_streaming.py -v
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.loop import ReactLoop, StopReason, ToolCall, ToolResult
from llm.base import Message


# ── Mock 헬퍼 ──────────────────────────────────────────────────────────────


def _text_response(text: str):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[{"type": "text", "text": text}],
    )


def _tool_response(tool_id, name, input_):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[{"type": "tool_use", "id": tool_id, "name": name, "input": input_}],
    )


def _end_turn_response():
    """end_turn 감지용 chat 응답 (스트리밍 경로에서 텍스트는 stream() 에서 옴)"""
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[],
    )


class _MockLLM:
    def __init__(self, chat_responses, stream_chunks=None):
        self._chat_iter = iter(chat_responses)
        self._stream_chunks = stream_chunks or []

    def build_messages(self, user_input, history=None):
        msgs = [Message(role=h.role, content=h.content) for h in (history or [])]
        msgs.append(Message(role="user", content=user_input))
        return msgs

    def chat(self, messages, **kwargs):
        return next(self._chat_iter)

    def stream(self, messages, **kwargs):
        yield from self._stream_chunks


@pytest.fixture(autouse=True)
def patch_schema(monkeypatch):
    monkeypatch.setattr(ReactLoop, "get_tools_schema", lambda self: [])


# ── on_token 콜백 기본 동작 ────────────────────────────────────────────────


class TestStreamingBasic:
    def test_stream_called_with_tools_schema_kwarg(self, monkeypatch):
        """end_turn + on_token 경로에서 stream()에 tools kwargs가 전달되어야 한다."""
        tools_schema = [{"name": "read_file", "input_schema": {"type": "object"}}]
        monkeypatch.setattr(ReactLoop, "get_tools_schema", lambda self: tools_schema)

        llm = MagicMock()
        llm.build_messages.return_value = [Message(role="user", content="hi")]
        llm.chat.return_value = _end_turn_response()
        llm.stream.return_value = iter(["ok"])

        loop = ReactLoop(llm=llm, on_token=lambda _: None)
        result = loop.run("hi")

        assert result.succeeded is True
        llm.stream.assert_called_once()
        _, kwargs = llm.stream.call_args
        assert kwargs.get("tools") == tools_schema

    def test_on_token_called_during_end_turn(self):
        """end_turn 응답 시 on_token 콜백이 최소 1회 이상 호출돼야 한다."""
        chunks = ["Hello", " world", "!"]
        end_turn = _end_turn_response()
        llm = _MockLLM(chat_responses=[end_turn], stream_chunks=chunks)
        received: list[str] = []

        loop = ReactLoop(llm=llm, on_token=received.append)
        result = loop.run("hi")

        assert len(received) > 0

    def test_on_token_chunks_assemble_to_answer(self):
        """on_token 으로 받은 청크를 이어붙이면 최종 answer 와 같아야 한다."""
        chunks = ["파이썬", "은 ", "좋아요"]
        end_turn = _end_turn_response()
        llm = _MockLLM(chat_responses=[end_turn], stream_chunks=chunks)
        received: list[str] = []

        loop = ReactLoop(llm=llm, on_token=received.append)
        result = loop.run("파이썬 어때?")

        assembled = "".join(received)
        assert assembled == result.answer

    def test_on_token_not_called_during_tool_turns(self, tmp_path):
        """도구 호출 중간 턴에는 on_token 이 불리지 않아야 한다."""
        f = tmp_path / "x.txt"
        f.write_text("data", encoding="utf-8")

        tool_turn = _tool_response("id1", "read_file", {"path": str(f)})
        end_turn = _end_turn_response()
        final_chunks = ["결과입니다"]

        # chat() 은 tool_use 를 반환한 뒤 end_turn 반환, stream() 은 최종 응답 청크 반환
        llm = _MockLLM(chat_responses=[tool_turn, end_turn], stream_chunks=final_chunks)
        token_calls: list[str] = []

        loop = ReactLoop(llm=llm, on_token=token_calls.append)
        loop.run("파일 읽어줘")

        # 스트리밍은 최종 응답에서만 발생
        assert token_calls == final_chunks

    def test_on_token_none_uses_chat_not_stream(self):
        """on_token=None 이면 stream() 을 호출하지 않고 chat() 만 사용한다."""
        llm = MagicMock()
        llm.build_messages.return_value = [Message(role="user", content="hi")]
        llm.chat.return_value = _text_response("ok")

        loop = ReactLoop(llm=llm, on_token=None)
        loop.run("hi")

        llm.stream.assert_not_called()
        llm.chat.assert_called_once()

    def test_empty_stream_returns_empty_answer(self):
        """스트림이 비어있으면 answer 가 빈 문자열이어야 한다."""
        end_turn = _end_turn_response()
        llm = _MockLLM(chat_responses=[end_turn], stream_chunks=[])
        loop = ReactLoop(llm=llm, on_token=lambda t: None)
        result = loop.run("empty")

        assert result.answer == ""
        assert result.stop_reason == StopReason.END_TURN

    def test_stream_error_returns_llm_error(self):
        """stream() 중 예외 발생 → LLM_ERROR 로 종료."""
        llm = MagicMock()
        llm.build_messages.return_value = [Message(role="user", content="hi")]
        llm.chat.return_value = _end_turn_response()
        llm.stream.side_effect = RuntimeError("스트림 끊김")

        loop = ReactLoop(llm=llm, on_token=lambda t: None)
        result = loop.run("hi")

        assert result.stop_reason == StopReason.LLM_ERROR
        assert result.succeeded is False

    def test_large_stream_all_chunks_received(self):
        """청크가 많아도 전부 전달돼야 한다."""
        chunks = [f"chunk{i}" for i in range(100)]
        end_turn = _end_turn_response()
        llm = _MockLLM(chat_responses=[end_turn], stream_chunks=chunks)
        received: list[str] = []

        loop = ReactLoop(llm=llm, on_token=received.append)
        loop.run("big stream")

        assert received == chunks

    def test_unicode_chunks_handled(self):
        """한국어 / 이모지 청크가 깨지지 않아야 한다."""
        chunks = ["안녕", "하세요", " 🎉"]
        end_turn = _end_turn_response()
        llm = _MockLLM(chat_responses=[end_turn], stream_chunks=chunks)
        received: list[str] = []

        loop = ReactLoop(llm=llm, on_token=received.append)
        result = loop.run("인사해줘")

        assert "".join(received) == "안녕하세요 🎉"
        assert result.answer == "안녕하세요 🎉"


# ── 스트리밍 + 도구 조합 ────────────────────────────────────────────────────


class TestStreamingWithTools:
    def test_tool_then_stream_full_flow(self, tmp_path):
        """tool_use → end_turn(stream) 전체 흐름이 정상 동작해야 한다."""
        f = tmp_path / "data.txt"
        f.write_text("42", encoding="utf-8")

        tool_turn = _tool_response("id1", "read_file", {"path": str(f)})
        end_turn = _end_turn_response()
        final_chunks = ["파일에는 ", "42가 있어요"]
        llm = _MockLLM(chat_responses=[tool_turn, end_turn], stream_chunks=final_chunks)

        received: list[str] = []
        loop = ReactLoop(llm=llm, on_token=received.append)
        result = loop.run("파일 읽어줘")

        assert result.succeeded is True
        assert "42" in result.answer
        assert received == final_chunks

    def test_multiple_tool_turns_then_stream(self, tmp_path):
        """여러 번 도구 호출 후 최종 스트리밍."""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("A", encoding="utf-8")
        f2.write_text("B", encoding="utf-8")

        tool1 = _tool_response("id1", "read_file", {"path": str(f1)})
        tool2 = _tool_response("id2", "read_file", {"path": str(f2)})
        end_turn = _end_turn_response()
        final_chunks = ["A와 B를 읽었어요"]

        llm = _MockLLM(chat_responses=[tool1, tool2, end_turn], stream_chunks=final_chunks)
        received: list[str] = []

        loop = ReactLoop(llm=llm, on_token=received.append)
        result = loop.run("둘 다 읽어줘")

        assert result.succeeded is True
        assert received == final_chunks
