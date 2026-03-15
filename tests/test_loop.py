"""
tests/test_loop.py

core/loop.py 단위 테스트.
실제 LLM 호출 없이 Mock LLM으로 모든 경로를 검증.

실행:
    pytest tests/test_loop.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from llm.base import Message
from core.loop import (
    LoopResult,
    ReactLoop,
    StopReason,
    ToolCall,
    ToolResult,
    _extract_text,
    _extract_tool_calls,
    _is_fatal_error,
)


# ── Mock 헬퍼 ──────────────────────────────────────────────────────────────


def _text_response(text: str):
    """end_turn 응답 (텍스트만 반환)"""
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[{"type": "text", "text": text}],
    )


def _tool_response(tool_id: str, tool_name: str, tool_input: dict):
    """tool_use 응답"""
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            {
                "type": "tool_use",
                "id": tool_id,
                "name": tool_name,
                "input": tool_input,
            }
        ],
    )


def _empty_tool_response():
    """stop_reason=tool_use 이지만 tool_use 블록이 없는 비정상 응답"""
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[{"type": "text", "text": "텍스트만 있음"}],
    )


class _SequentialMockLLM:
    """call 순서대로 미리 준비한 응답을 반환하는 Mock LLM"""

    def __init__(self, responses: list):
        self._responses = iter(responses)

    def build_messages(self, user_input, history=None):
        msgs = []
        for h in (history or []):
            # dict 형식과 Message 객체 모두 허용
            msgs.append(
                Message(role=h["role"], content=h["content"])
                if isinstance(h, dict)
                else h
            )
        msgs.append(Message(role="user", content=user_input))
        return msgs

    def chat(self, messages, **kwargs):
        return next(self._responses)


# ── autouse 픽스처: get_tools_schema 패치 ────────────────────────────────────


@pytest.fixture(autouse=True)
def patch_get_tools_schema(monkeypatch):
    """테스트용 Mock LLM은 registry에 없으므로 스키마 조회를 빈 리스트로 패치"""
    monkeypatch.setattr(ReactLoop, "get_tools_schema", lambda self: [])


# ── _is_fatal_error ────────────────────────────────────────────────────────


class TestIsFatalError:
    @pytest.mark.parametrize(
        "msg",
        [
            "permission denied",
            "disk full error",
            "out of memory",
            "process killed",
            "PERMISSION DENIED",  # 대소문자 무시
        ],
    )
    def test_fatal_keywords(self, msg):
        assert _is_fatal_error(msg) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "file not found",
            "invalid syntax",
            "index out of range",
            "",
        ],
    )
    def test_non_fatal_messages(self, msg):
        assert _is_fatal_error(msg) is False


# ── _extract_text ──────────────────────────────────────────────────────────


class TestExtractText:
    def test_extracts_single_text_block(self):
        content = [{"type": "text", "text": "hello"}]
        assert _extract_text(content) == "hello"

    def test_joins_multiple_text_blocks(self):
        content = [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
        assert _extract_text(content) == "first\nsecond"

    def test_ignores_non_text_blocks(self):
        content = [
            {"type": "tool_use", "id": "1", "name": "foo", "input": {}},
            {"type": "text", "text": "result"},
        ]
        assert _extract_text(content) == "result"

    def test_empty_content_returns_empty_string(self):
        assert _extract_text([]) == ""

    def test_supports_object_style_blocks(self):
        """SDK가 반환하는 객체(속성 방식)도 처리 가능해야 함"""
        block = SimpleNamespace(type="text", text="object text")
        assert _extract_text([block]) == "object text"


# ── _extract_tool_calls ────────────────────────────────────────────────────


class TestExtractToolCalls:
    def test_extracts_dict_style_tool_use(self):
        content = [
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "read_file",
                "input": {"path": "/tmp/x.py"},
            }
        ]
        calls = _extract_tool_calls(content)

        assert len(calls) == 1
        assert calls[0].id == "call_1"
        assert calls[0].name == "read_file"
        assert calls[0].input == {"path": "/tmp/x.py"}

    def test_extracts_object_style_tool_use(self):
        block = SimpleNamespace(
            type="tool_use",
            id="call_obj",
            name="write_file",
            input={"path": "/tmp/y.py", "content": "hello"},
        )
        calls = _extract_tool_calls([block])

        assert len(calls) == 1
        assert calls[0].name == "write_file"

    def test_ignores_non_tool_blocks(self):
        content = [
            {"type": "text", "text": "no tool here"},
        ]
        assert _extract_tool_calls(content) == []

    def test_extracts_multiple_tool_calls(self):
        content = [
            {
                "type": "tool_use",
                "id": "1",
                "name": "read_file",
                "input": {"path": "a"},
            },
            {
                "type": "tool_use",
                "id": "2",
                "name": "write_file",
                "input": {"path": "b", "content": ""},
            },
        ]
        calls = _extract_tool_calls(content)
        assert len(calls) == 2


# ── LoopResult 프로퍼티 ────────────────────────────────────────────────────


class TestLoopResult:
    def test_succeeded_true_on_end_turn(self):
        r = LoopResult(answer="ok", stop_reason=StopReason.END_TURN)
        assert r.succeeded is True

    @pytest.mark.parametrize(
        "reason",
        [
            StopReason.MAX_ITER,
            StopReason.TOOL_ERROR,
            StopReason.LLM_ERROR,
        ],
    )
    def test_succeeded_false_on_non_end_turn(self, reason):
        r = LoopResult(answer="fail", stop_reason=reason)
        assert r.succeeded is False

    def test_total_tool_calls_counts_all_iterations(self):
        tc = ToolCall(id="1", name="foo", input={})
        tr = ToolResult(tool_use_id="1", content="ok")
        from core.loop import LoopIteration

        iterations = [
            LoopIteration(
                index=1, tool_calls=[tc, tc], tool_results=[tr, tr], elapsed_ms=10
            ),
            LoopIteration(index=2, tool_calls=[tc], tool_results=[tr], elapsed_ms=5),
        ]
        r = LoopResult(
            answer="ok", stop_reason=StopReason.END_TURN, iterations=iterations
        )
        assert r.total_tool_calls == 3


# ── ReactLoop.run() ────────────────────────────────────────────────────────


class TestReactLoopRun:
    # ── 성공 경로 ──────────────────────────────────────────────────────────

    def test_end_turn_immediately(self):
        """도구 호출 없이 첫 응답에서 바로 종료"""
        llm = _SequentialMockLLM([_text_response("완료했습니다.")])
        loop = ReactLoop(llm=llm)

        result = loop.run("안녕")

        assert result.succeeded is True
        assert result.stop_reason == StopReason.END_TURN
        assert result.answer == "완료했습니다."
        assert result.iterations == []

    def test_one_tool_call_then_end_turn(self, tmp_path):
        """파일 읽기 1회 후 종료"""
        f = tmp_path / "hello.txt"
        f.write_text("world", encoding="utf-8")

        llm = _SequentialMockLLM(
            [
                _tool_response("id1", "read_file", {"path": str(f)}),
                _text_response("파일 내용은 world 입니다."),
            ]
        )
        loop = ReactLoop(llm=llm)

        result = loop.run("hello.txt 읽어줘")

        assert result.succeeded is True
        assert len(result.iterations) == 1
        assert result.iterations[0].tool_calls[0].name == "read_file"
        assert result.total_tool_calls == 1

    def test_multiple_tool_calls_in_one_turn(self, tmp_path):
        """한 턴에 여러 도구 동시 호출"""
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")

        multi_tool_response = SimpleNamespace(
            stop_reason="tool_use",
            content=[
                {
                    "type": "tool_use",
                    "id": "id1",
                    "name": "read_file",
                    "input": {"path": str(tmp_path / "a.txt")},
                },
                {
                    "type": "tool_use",
                    "id": "id2",
                    "name": "read_file",
                    "input": {"path": str(tmp_path / "b.txt")},
                },
            ],
        )
        llm = _SequentialMockLLM([multi_tool_response, _text_response("둘 다 읽었음")])
        loop = ReactLoop(llm=llm)

        result = loop.run("두 파일 읽어줘")

        assert result.succeeded is True
        assert result.total_tool_calls == 2

    # ── 메시지 배열 관리 ──────────────────────────────────────────────────

    def test_messages_include_user_message(self):
        llm = _SequentialMockLLM([_text_response("ok")])
        loop = ReactLoop(llm=llm)

        result = loop.run("테스트 입력")

        first_msg = result.messages[0]
        assert first_msg.role == "user"
        assert first_msg.content == "테스트 입력"

    def test_history_prepended_to_messages(self):
        llm = _SequentialMockLLM([_text_response("ok")])
        loop = ReactLoop(llm=llm)
        history = [
            {"role": "user", "content": "이전 질문"},
            {"role": "assistant", "content": "이전 답변"},
        ]

        result = loop.run("새 질문", history=history)

        assert result.messages[0].content == "이전 질문"
        assert result.messages[2].content == "새 질문"

    def test_tool_result_appended_as_user_turn(self, tmp_path):
        """tool_result 가 user 턴으로 messages에 추가되어야 함"""
        f = tmp_path / "t.txt"
        f.write_text("data", encoding="utf-8")

        llm = _SequentialMockLLM(
            [
                _tool_response("id1", "read_file", {"path": str(f)}),
                _text_response("done"),
            ]
        )
        loop = ReactLoop(llm=llm)

        result = loop.run("읽어줘")

        # messages: [user, assistant(tool_use), user(tool_result), ...]
        tool_result_msg = result.messages[2]
        assert tool_result_msg.role == "user"
        assert tool_result_msg.content[0]["type"] == "tool_result"
        assert tool_result_msg.content[0]["tool_use_id"] == "id1"

    # ── 반복 제어 ──────────────────────────────────────────────────────────

    def test_max_iterations_exceeded(self, tmp_path):
        """max_iterations 초과 시 MAX_ITER로 종료"""
        f = tmp_path / "f.txt"
        f.write_text("x", encoding="utf-8")

        # 항상 tool_use만 반환 → 루프가 멈추지 않음
        always_tool = _tool_response("id", "read_file", {"path": str(f)})
        llm = _SequentialMockLLM([always_tool] * 5)
        loop = ReactLoop(llm=llm, max_iterations=3)

        result = loop.run("무한루프 테스트")

        assert result.stop_reason == StopReason.MAX_ITER
        assert result.succeeded is False
        assert len(result.iterations) == 3

    # ── 오류 경로 ──────────────────────────────────────────────────────────

    def test_llm_error_returns_llm_error_stop_reason(self):
        """LLM 호출 예외 → LLM_ERROR"""
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("API 연결 실패")
        loop = ReactLoop(llm=mock_llm)

        result = loop.run("질문")

        assert result.stop_reason == StopReason.LLM_ERROR
        assert result.succeeded is False
        assert "오류" in result.answer

    def test_fatal_tool_error_stops_loop(self, tmp_path):
        """permission denied 오류 → TOOL_ERROR 즉시 종료"""
        f = tmp_path / "secret.txt"
        f.write_text("data", encoding="utf-8")

        # write_file에 permission denied 에러를 강제로 발생시키기 위해 patch 사용
        with patch(
            "tools.file_tools.Path.write_text",
            side_effect=PermissionError("permission denied"),
        ):
            llm = _SequentialMockLLM(
                [
                    _tool_response(
                        "id1", "write_file", {"path": str(f), "content": "x"}
                    ),
                    _text_response("never reached"),
                ]
            )
            loop = ReactLoop(llm=llm)

            result = loop.run("파일 써줘")

        assert result.stop_reason == StopReason.TOOL_ERROR
        assert result.succeeded is False

    def test_non_fatal_tool_error_continues_loop(self, tmp_path):
        """file not found 같은 비치명 오류는 루프를 계속 진행"""
        llm = _SequentialMockLLM(
            [
                _tool_response("id1", "read_file", {"path": "/nonexistent/file.txt"}),
                _text_response("파일이 없었지만 계속 진행했어요."),
            ]
        )
        loop = ReactLoop(llm=llm)

        result = loop.run("없는 파일 읽어줘")

        assert result.stop_reason == StopReason.END_TURN
        assert result.succeeded is True
        # tool_result에 is_error=True 가 기록되어야 함
        tool_result_content = result.messages[2].content[0]
        assert tool_result_content["is_error"] is True

    def test_unknown_tool_name_returns_error_result(self):
        """registry에 없는 도구 호출 → ToolResult.is_error=True, 루프 계속"""
        llm = _SequentialMockLLM(
            [
                _tool_response("id1", "nonexistent_tool", {}),
                _text_response("알 수 없는 도구였어요."),
            ]
        )
        loop = ReactLoop(llm=llm)

        result = loop.run("없는 도구 써줘")

        assert result.succeeded is True
        tool_result_content = result.messages[2].content[0]
        assert tool_result_content["is_error"] is True

    def test_tool_use_response_without_tool_blocks(self):
        """stop_reason=tool_use인데 tool_use 블록이 없는 경우 → end_turn 처리"""
        llm = _SequentialMockLLM([_empty_tool_response()])
        loop = ReactLoop(llm=llm)

        result = loop.run("비정상 응답 테스트")

        assert result.stop_reason == StopReason.END_TURN

    # ── 콜백 훅 ──────────────────────────────────────────────────────────

    def test_on_tool_call_callback_invoked(self, tmp_path):
        f = tmp_path / "cb.txt"
        f.write_text("hi", encoding="utf-8")

        called_with: list[ToolCall] = []
        llm = _SequentialMockLLM(
            [
                _tool_response("id1", "read_file", {"path": str(f)}),
                _text_response("done"),
            ]
        )
        loop = ReactLoop(llm=llm, on_tool_call=called_with.append)

        loop.run("콜백 테스트")

        assert len(called_with) == 1
        assert called_with[0].name == "read_file"

    def test_on_tool_result_callback_invoked(self, tmp_path):
        f = tmp_path / "res.txt"
        f.write_text("result_data", encoding="utf-8")

        results_received: list[ToolResult] = []
        llm = _SequentialMockLLM(
            [
                _tool_response("id1", "read_file", {"path": str(f)}),
                _text_response("done"),
            ]
        )
        loop = ReactLoop(llm=llm, on_tool_result=results_received.append)

        loop.run("결과 콜백 테스트")

        assert len(results_received) == 1
        assert results_received[0].is_error is False
        assert "result_data" in results_received[0].content
