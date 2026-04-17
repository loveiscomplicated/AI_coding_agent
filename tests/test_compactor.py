"""
tests/test_compactor.py — core/compactor.py 단위 테스트.

실제 LLM 호출 없이 fake client 로 compact_history()의 경계·보호 로직을 검증한다.

실행:
    pytest tests/test_compactor.py -v
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from llm.base import Message
from core.compactor import (
    CompactionResult,
    compact_history,
    estimate_tokens,
    _has_tool_use,
    _has_tool_result,
    _is_safe_cut_point,
)


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────


class _FakeSummaryLLM:
    """요약용 가짜 LLM — chat() 호출 시 미리 지정된 요약 텍스트를 반환."""

    def __init__(
        self,
        summary_text: str = "요약: 파일 A를 읽고 B에 구현을 작성했다.",
        input_tokens: int = 500,
        output_tokens: int = 300,
        raise_on_call: Exception | None = None,
    ):
        self.summary_text = summary_text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.raise_on_call = raise_on_call
        self.call_count = 0
        self.last_messages: list[Message] = []
        self.last_tools = None

    def chat(self, messages, **kwargs):
        self.call_count += 1
        self.last_messages = messages
        self.last_tools = kwargs.get("tools")
        if self.raise_on_call:
            raise self.raise_on_call
        return SimpleNamespace(
            content=[{"type": "text", "text": self.summary_text}],
            model="fake-fast",
            stop_reason="end_turn",
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


def _tool_use_msg(tool_id: str = "t1", name: str = "read_file", path: str = "a.py") -> Message:
    return Message(
        role="assistant",
        content=[
            {"type": "text", "text": f"{name} 호출"},
            {"type": "tool_use", "id": tool_id, "name": name, "input": {"path": path}},
        ],
    )


def _tool_result_msg(tool_id: str = "t1", content: str = "파일 내용") -> Message:
    return Message(
        role="user",
        content=[
            {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": content,
                "is_error": False,
            }
        ],
    )


def _system() -> Message:
    return Message(role="system", content="당신은 코딩 에이전트입니다." * 10)


def _user_task(text: str = "파일 A를 읽고 요약해줘.") -> Message:
    return Message(role="user", content=text)


def _build_pair_history(n_pairs: int, content_size: int = 1000) -> list[Message]:
    """
    system + user(task) + n_pairs * (tool_use_assistant, tool_result_user) 구조를 만든다.

    tool_result 의 content 를 content_size 자 이상으로 키워서 토큰 추정치를 크게 만든다.
    """
    msgs: list[Message] = [_system(), _user_task()]
    for i in range(n_pairs):
        tid = f"call_{i}"
        msgs.append(_tool_use_msg(tool_id=tid, name="read_file", path=f"file_{i}.py"))
        body = f"file_{i} 내용 " * (content_size // 12 + 1)
        msgs.append(_tool_result_msg(tool_id=tid, content=body))
    return msgs


# ── estimate_tokens ──────────────────────────────────────────────────────────


class TestEstimateTokens:
    def test_string_content(self):
        m = Message(role="user", content="a" * 400)
        # 400 chars / 4 ≈ 100 tokens + role
        assert estimate_tokens(m) >= 100

    def test_list_content_with_tool_result(self):
        m = _tool_result_msg(content="x" * 400)
        assert estimate_tokens(m) >= 100

    def test_empty_content(self):
        m = Message(role="user", content="")
        assert estimate_tokens(m) >= 1  # role 최소치


# ── 블록 판별 ─────────────────────────────────────────────────────────────────


class TestBlockDetection:
    def test_tool_use_assistant(self):
        assert _has_tool_use(_tool_use_msg()) is True

    def test_text_only_assistant_is_not_tool_use(self):
        m = Message(role="assistant", content=[{"type": "text", "text": "hi"}])
        assert _has_tool_use(m) is False

    def test_tool_result_user(self):
        assert _has_tool_result(_tool_result_msg()) is True

    def test_plain_user_is_not_tool_result(self):
        assert _has_tool_result(_user_task()) is False

    def test_safe_cut_point_after_tool_use_is_false(self):
        msgs = [_system(), _user_task(), _tool_use_msg(), _tool_result_msg()]
        # cut before tool_result (idx 3) splits the pair → unsafe
        assert _is_safe_cut_point(msgs, 3) is False
        # cut at idx 2 (before the tool_use assistant) is safe
        assert _is_safe_cut_point(msgs, 2) is True

    def test_safe_cut_at_boundaries(self):
        msgs = [_system(), _user_task()]
        assert _is_safe_cut_point(msgs, 0) is True
        assert _is_safe_cut_point(msgs, len(msgs)) is True


# ── compact_history: 임계치 / 경계 ────────────────────────────────────────────


class TestCompactHistory:
    def test_not_enough_messages_returns_none(self):
        """메시지 수가 keep_first_n + keep_last_n 이하면 압축 스킵."""
        msgs = _build_pair_history(n_pairs=2)  # 2 + 4 = 6 messages
        llm = _FakeSummaryLLM()
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is None
        assert llm.call_count == 0

    def test_above_threshold_produces_result(self):
        msgs = _build_pair_history(n_pairs=6)  # 2 + 12 = 14 messages
        llm = _FakeSummaryLLM()
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is not None
        assert isinstance(result, CompactionResult)
        assert llm.call_count == 1

    def test_message_count_decreases_after_replacement(self):
        msgs = _build_pair_history(n_pairs=6)
        llm = _FakeSummaryLLM()
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is not None
        start, end = result.dropped_range
        new_msgs = msgs[:start] + [result.summary_message] + msgs[end:]
        # 기존 14 → 최종 2(head) + 1(summary) + 4(tail) = 7
        assert len(new_msgs) < len(msgs)
        assert len(new_msgs) == 2 + 1 + 4

    def test_first_n_messages_preserved(self):
        """system + 첫 user(task) 메시지는 절대 드롭되지 않는다."""
        msgs = _build_pair_history(n_pairs=6)
        original_first_two = msgs[:2]
        llm = _FakeSummaryLLM()
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is not None
        start, end = result.dropped_range
        assert start >= 2  # 처음 두 메시지 보존
        # 드롭 구간이 처음 두 메시지를 포함하지 않음
        assert msgs[0] is original_first_two[0]
        assert msgs[1] is original_first_two[1]

    def test_last_n_messages_preserved(self):
        msgs = _build_pair_history(n_pairs=6)
        llm = _FakeSummaryLLM()
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is not None
        start, end = result.dropped_range
        # 뒤에서 4개 이상 보존 (pair 경계 존중으로 더 많이 보존 가능)
        assert len(msgs) - end >= 4

    def test_tool_use_tool_result_pair_not_split_at_start(self):
        """드롭 시작 경계가 tool_use/tool_result 페어를 쪼개지 않는다."""
        msgs = _build_pair_history(n_pairs=6)
        llm = _FakeSummaryLLM()
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is not None
        start, _ = result.dropped_range
        # start 직전 메시지가 tool_use 이면 페어가 깨진 것
        if start > 0:
            assert not _has_tool_use(msgs[start - 1]), (
                f"drop_start={start} 직전이 tool_use assistant → 페어 깨짐"
            )

    def test_tool_use_tool_result_pair_not_split_at_end(self):
        """드롭 끝 경계가 tool_use/tool_result 페어를 쪼개지 않는다."""
        msgs = _build_pair_history(n_pairs=6)
        llm = _FakeSummaryLLM()
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is not None
        _, end = result.dropped_range
        # end-1(드롭되는 마지막 메시지) 이 tool_use 이면 직후에 tool_result 가
        # 남아 있어 페어가 깨진 것
        if end < len(msgs):
            assert not _has_tool_use(msgs[end - 1]), (
                f"drop_end={end} 직전(드롭된 마지막)이 tool_use assistant → 페어 깨짐"
            )

    def test_summary_message_has_user_role_and_marker(self):
        msgs = _build_pair_history(n_pairs=6)
        llm = _FakeSummaryLLM(summary_text="READ a.py, WROTE b.py, TESTS FAIL: t1")
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is not None
        assert result.summary_message.role == "user"
        assert "[이전 대화 요약]" in result.summary_message.content
        assert "READ a.py" in result.summary_message.content
        assert "(이하 최근 대화 이어서)" in result.summary_message.content

    def test_token_usage_recorded(self):
        msgs = _build_pair_history(n_pairs=6)
        llm = _FakeSummaryLLM(input_tokens=1200, output_tokens=250)
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is not None
        assert result.input_tokens_used == 1200
        assert result.output_tokens_used == 250
        assert result.dropped_message_count > 0
        assert result.dropped_tokens_estimate > 0
        assert result.summary_tokens_estimate > 0

    def test_llm_failure_returns_none(self):
        msgs = _build_pair_history(n_pairs=6)
        llm = _FakeSummaryLLM(raise_on_call=RuntimeError("API down"))
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is None

    def test_empty_summary_returns_none(self):
        msgs = _build_pair_history(n_pairs=6)
        llm = _FakeSummaryLLM(summary_text="   ")
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is None

    def test_tools_not_passed_to_compactor_llm(self):
        """요약 호출은 도구 없이 이뤄져야 한다 (불필요한 tool_use 생성 방지)."""
        msgs = _build_pair_history(n_pairs=6)
        llm = _FakeSummaryLLM()
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is not None
        assert llm.last_tools is None

    def test_drop_end_retreats_preserving_keep_last_n(self):
        """
        keep_last_n 계약 회귀 테스트.

        regression: 이전 구현은 drop_end 를 앞으로 전진(advance) 시켜 tail 이
        keep_last_n 보다 작아질 수 있었다. 올바른 동작은 drop_end 를 뒤로 후퇴
        (retreat) 시켜 tail 을 keep_last_n 이상으로 유지.

        재현: drop_end 초기값에 해당하는 msgs[drop_end-1] 가 tool_use 이면
        뒤로 한 칸 물러나야 한다.
        """
        # 8 메시지: [sys, user_task, assistant, user_tool_result, assistant_tool_use, user_tool_result, assistant_tool_use, user_tool_result]
        msgs = [
            _system(),
            _user_task(),
            Message(role="assistant", content=[{"type": "text", "text": "plan"}]),
            Message(role="user", content="plain"),
            _tool_use_msg(tool_id="A"),
            _tool_result_msg(tool_id="A"),
            _tool_use_msg(tool_id="B"),
            _tool_result_msg(tool_id="B"),
        ]
        llm = _FakeSummaryLLM()
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=3)
        assert result is not None
        start, end = result.dropped_range
        preserved_tail = len(msgs) - end
        assert preserved_tail >= 3, (
            f"keep_last_n=3 계약 위반: preserved_tail={preserved_tail}, range=({start}, {end})"
        )

    def test_drop_end_retreats_with_larger_keep_last(self):
        """keep_last_n=5 보존 확인."""
        msgs = _build_pair_history(n_pairs=6)  # 2 + 12 = 14 messages
        llm = _FakeSummaryLLM()
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=5)
        assert result is not None
        _, end = result.dropped_range
        preserved_tail = len(msgs) - end
        assert preserved_tail >= 5, (
            f"keep_last_n=5 계약 위반: preserved_tail={preserved_tail}"
        )

    def test_skips_when_cannot_preserve_keep_last_n(self):
        """
        drop_end 가 후퇴해도 keep_last_n 을 보장할 수 없는 상황에서는
        조용히 스킵(None 반환) 하며 원본 메시지를 건드리지 않는다.
        """
        # tool_use 페어만으로 채워 모든 가능한 drop_end 후보가 unsafe 한 상황은
        # 실제로 만들기 어려우므로, 대신 keep_last_n 이 너무 커서 드롭 구간이
        # 2 개 미만이 되는 케이스로 대체.
        msgs = _build_pair_history(n_pairs=4)  # 2 + 8 = 10
        llm = _FakeSummaryLLM()
        # keep_first_n + keep_last_n = 10 -> 드롭 구간 0
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=8)
        assert result is None
        assert llm.call_count == 0

    def test_replacement_produces_valid_message_sequence(self):
        """압축 후 메시지 시퀀스에서 tool_use/tool_result 페어가 모두 대응된다."""
        msgs = _build_pair_history(n_pairs=6)
        llm = _FakeSummaryLLM()
        result = compact_history(msgs, llm, keep_first_n=2, keep_last_n=4)
        assert result is not None
        start, end = result.dropped_range
        new_msgs = msgs[:start] + [result.summary_message] + msgs[end:]

        # tool_use id 집합과 tool_result id 집합이 동일해야 한다
        tool_use_ids = set()
        tool_result_ids = set()
        for m in new_msgs:
            if _has_tool_use(m):
                for b in m.content:
                    if (b.get("type") if isinstance(b, dict) else None) == "tool_use":
                        tool_use_ids.add(b.get("id"))
            if _has_tool_result(m):
                for b in m.content:
                    if (b.get("type") if isinstance(b, dict) else None) == "tool_result":
                        tool_result_ids.add(b.get("tool_use_id"))
        assert tool_use_ids == tool_result_ids, (
            f"압축 후 tool_use/tool_result 매칭 불일치: "
            f"use={tool_use_ids}, result={tool_result_ids}"
        )
