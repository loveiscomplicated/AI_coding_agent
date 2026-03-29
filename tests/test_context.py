"""
tests/test_context.py

컨텍스트 길이 관리 테스트.

설계:
  ContextManager(max_tokens) 클래스.
  fit(messages) 로 토큰 한도 내로 히스토리를 잘라 반환한다.

  - 시스템 메시지는 항상 보존
  - 최신 메시지부터 역순으로 포함 (오래된 메시지 먼저 탈락)
  - tool_use / tool_result 쌍은 같이 잘려야 한다 (쌍 깨지면 안 됨)
  - 토큰 추정: 글자 수 / 4 (rough estimate), 또는 실제 tiktoken

아직 구현되지 않음 — 처음엔 실패한다.

실행:
    pytest tests/test_context.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from llm.base import Message
from core.context import ContextManager   # 아직 없음


# ── 픽스처 ────────────────────────────────────────────────────────────────────


def _msg(role: str, content: str) -> Message:
    return Message(role=role, content=content)


def _tool_pair(tool_id: str) -> list[Message]:
    """tool_use + tool_result 한 쌍 반환"""
    assistant = Message(
        role="assistant",
        content=[{"type": "tool_use", "id": tool_id, "name": "read_file", "input": {}}],
    )
    user = Message(
        role="user",
        content=[{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}],
    )
    return [assistant, user]


# ── 기본 동작 ──────────────────────────────────────────────────────────────────


class TestContextManagerBasic:
    def test_under_limit_returns_all_messages(self):
        """토큰 한도 내면 모든 메시지 그대로 반환."""
        mgr = ContextManager(max_tokens=10_000)
        messages = [
            _msg("system", "시스템"),
            _msg("user", "질문"),
            _msg("assistant", "답변"),
        ]
        result = mgr.fit(messages)
        assert result == messages

    def test_empty_messages_returns_empty(self):
        mgr = ContextManager(max_tokens=1000)
        assert mgr.fit([]) == []

    def test_system_message_always_preserved(self):
        """한도 초과 시에도 system 메시지는 반드시 포함."""
        mgr = ContextManager(max_tokens=10)  # 매우 작은 한도
        messages = [
            _msg("system", "중요한 시스템 지시"),
            _msg("user", "아주 긴 질문 " * 100),
            _msg("assistant", "아주 긴 답변 " * 100),
        ]
        result = mgr.fit(messages)
        assert any(m.role == "system" for m in result)

    def test_recent_messages_kept_over_old(self):
        """오래된 메시지가 먼저 탈락해야 한다."""
        mgr = ContextManager(max_tokens=50)
        messages = [
            _msg("user", "오래된 질문"),       # 탈락 대상
            _msg("assistant", "오래된 답변"),   # 탈락 대상
            _msg("user", "최근 질문"),
            _msg("assistant", "최근 답변"),
        ]
        result = mgr.fit(messages)

        contents = [m.content for m in result if isinstance(m.content, str)]
        assert "최근 질문" in contents
        assert "최근 답변" in contents
        assert "오래된 질문" not in contents

    def test_returns_list_of_message(self):
        """반환값이 list[Message] 타입이어야 한다."""
        mgr = ContextManager(max_tokens=1000)
        result = mgr.fit([_msg("user", "hi")])
        assert isinstance(result, list)
        assert all(isinstance(m, Message) for m in result)


# ── 토큰 추정 ──────────────────────────────────────────────────────────────────


class TestContextManagerTokenEstimation:
    def test_count_tokens_string_content(self):
        """문자열 메시지의 토큰 수를 추정할 수 있어야 한다."""
        mgr = ContextManager(max_tokens=1000)
        tokens = mgr.count_tokens(_msg("user", "hello world"))
        assert isinstance(tokens, int)
        assert tokens > 0

    def test_count_tokens_list_content(self):
        """구조체(list) 콘텐츠도 토큰 수 추정이 가능해야 한다."""
        mgr = ContextManager(max_tokens=1000)
        msg = Message(
            role="user",
            content=[{"type": "tool_result", "tool_use_id": "abc", "content": "result"}],
        )
        tokens = mgr.count_tokens(msg)
        assert isinstance(tokens, int)
        assert tokens > 0

    def test_longer_message_has_more_tokens(self):
        """긴 메시지가 짧은 메시지보다 토큰이 많아야 한다."""
        mgr = ContextManager(max_tokens=1000)
        short = mgr.count_tokens(_msg("user", "hi"))
        long  = mgr.count_tokens(_msg("user", "안녕하세요 " * 50))
        assert long > short

    def test_total_tokens_property(self):
        """messages 전체 토큰 합산이 가능해야 한다."""
        mgr = ContextManager(max_tokens=1000)
        messages = [_msg("user", "a"), _msg("assistant", "b")]
        total = mgr.total_tokens(messages)
        assert total == sum(mgr.count_tokens(m) for m in messages)


# ── tool_use / tool_result 쌍 보존 ───────────────────────────────────────────


class TestContextManagerToolPairs:
    def test_tool_pair_not_split(self):
        """tool_use 와 tool_result 는 같이 잘리거나 같이 보존돼야 한다."""
        mgr = ContextManager(max_tokens=30)
        system = _msg("system", "sys")
        pair = _tool_pair("id1")
        recent = _msg("user", "최근 질문")

        messages = [system] + pair + [recent]
        result = mgr.fit(messages)

        # tool_use 가 있으면 tool_result 도 있어야 하고, 반대도 마찬가지
        has_tool_use    = any(
            isinstance(m.content, list) and
            any(b.get("type") == "tool_use" for b in m.content if isinstance(b, dict))
            for m in result
        )
        has_tool_result = any(
            isinstance(m.content, list) and
            any(b.get("type") == "tool_result" for b in m.content if isinstance(b, dict))
            for m in result
        )
        assert has_tool_use == has_tool_result

    def test_multiple_tool_pairs_handled(self):
        """여러 쌍이 있을 때도 쌍이 깨지지 않아야 한다."""
        mgr = ContextManager(max_tokens=100)
        messages = (
            [_msg("system", "sys")]
            + _tool_pair("id1")
            + _tool_pair("id2")
            + _tool_pair("id3")
            + [_msg("user", "최종 질문")]
        )
        result = mgr.fit(messages)

        tool_use_ids = set()
        tool_result_ids = set()
        for m in result:
            if isinstance(m.content, list):
                for b in m.content:
                    if isinstance(b, dict):
                        if b.get("type") == "tool_use":
                            tool_use_ids.add(b["id"])
                        if b.get("type") == "tool_result":
                            tool_result_ids.add(b["tool_use_id"])

        assert tool_use_ids == tool_result_ids


# ── 경계 / 오류 케이스 ────────────────────────────────────────────────────────


class TestContextManagerEdgeCases:
    def test_max_tokens_zero_returns_only_system(self):
        """max_tokens=0 이면 시스템 메시지만 남아야 한다."""
        mgr = ContextManager(max_tokens=0)
        messages = [
            _msg("system", "sys"),
            _msg("user", "q"),
            _msg("assistant", "a"),
        ]
        result = mgr.fit(messages)
        assert all(m.role == "system" for m in result)

    def test_single_message_never_truncated(self):
        """메시지가 1개뿐이면 한도 초과여도 그대로 반환한다."""
        mgr = ContextManager(max_tokens=1)
        msg = _msg("user", "아주 긴 메시지 " * 1000)
        result = mgr.fit([msg])
        assert result == [msg]

    def test_no_system_message_still_works(self):
        """시스템 메시지 없이도 동작해야 한다."""
        mgr = ContextManager(max_tokens=20)
        messages = [_msg("user", "q"), _msg("assistant", "a")]
        result = mgr.fit(messages)
        assert isinstance(result, list)

    def test_negative_max_tokens_treated_as_zero(self):
        """음수 max_tokens 는 0으로 처리해야 한다."""
        mgr = ContextManager(max_tokens=-100)
        messages = [_msg("system", "s"), _msg("user", "q")]
        result = mgr.fit(messages)
        assert len(result) <= len(messages)

    def test_only_system_message(self):
        """시스템 메시지만 있을 때 그대로 반환."""
        mgr = ContextManager(max_tokens=100)
        messages = [_msg("system", "sys")]
        assert mgr.fit(messages) == messages

    def test_fit_does_not_mutate_input(self):
        """원본 messages 리스트를 변경하면 안 된다."""
        mgr = ContextManager(max_tokens=10)
        original = [_msg("user", "q " * 100), _msg("assistant", "a " * 100)]
        copy = list(original)
        mgr.fit(original)
        assert original == copy

    def test_very_large_history_handled(self):
        """메시지 1000개도 크래시 없이 처리돼야 한다."""
        mgr = ContextManager(max_tokens=500)
        messages = [_msg("user" if i % 2 == 0 else "assistant", f"msg{i}") for i in range(1000)]
        result = mgr.fit(messages)
        assert isinstance(result, list)
        assert len(result) <= len(messages)
