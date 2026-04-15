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


# ── SemanticContextPruner 테스트 ──────────────────────────────────────────────

from core.context import SemanticContextPruner


def _tool_pair_msgs(tool_id: str) -> list[Message]:
    """tool_use + tool_result 쌍 (SemanticContextPruner 용)."""
    return [
        Message(
            role="assistant",
            content=[{"type": "tool_use", "id": tool_id, "name": "read_file", "input": {}}],
        ),
        Message(
            role="user",
            content=[{"type": "tool_result", "tool_use_id": tool_id, "content": "file content"}],
        ),
    ]


class TestSemanticContextPrunerBasic:
    """SemanticContextPruner 기본 동작 테스트."""

    def test_empty_returns_empty(self):
        pruner = SemanticContextPruner(max_tokens=10_000)
        assert pruner.fit([]) == []

    def test_system_messages_preserved(self):
        pruner = SemanticContextPruner(max_tokens=10_000)
        messages = [
            _msg("system", "sys"),
            _msg("user", "initial task"),
        ]
        result = pruner.fit(messages)
        assert any(m.role == "system" for m in result)

    def test_anchor_message_preserved(self):
        """초기 user 메시지(anchor)는 항상 보존된다."""
        pruner = SemanticContextPruner(max_tokens=10_000, recent_turns=1, middle_turns=1)
        anchor = _msg("user", "initial task ANCHOR")
        turns = []
        for i in range(10):
            turns.append(_msg("assistant", f"assistant turn {i}"))
            turns.append(_msg("user", f"user tool result {i}"))

        messages = [anchor] + turns
        result = pruner.fit(messages)
        assert any(m.content == "initial task ANCHOR" for m in result)

    def test_returns_list_of_message(self):
        pruner = SemanticContextPruner(max_tokens=10_000)
        result = pruner.fit([_msg("user", "hi")])
        assert isinstance(result, list)

    def test_small_history_not_pruned(self):
        """recent_turns 내의 히스토리는 전문 보존된다."""
        pruner = SemanticContextPruner(max_tokens=10_000, recent_turns=4, middle_turns=4)
        messages = [_msg("user", "task")]
        for i in range(3):
            messages.append(_msg("assistant", f"a{i}"))
            messages.append(_msg("user", f"u{i}"))
        result = pruner.fit(messages)
        # 히스토리가 recent_turns 이하 → 전문 보존
        assert len(result) == len(messages)


class TestSemanticContextPrunerTiers:
    """3-tier pruning 동작 테스트."""

    def _build_history(self, n_turns: int, anchor_text: str = "initial task"):
        """anchor + n_turns 개 (assistant, user) 쌍."""
        messages = [_msg("user", anchor_text)]
        for i in range(n_turns):
            messages.append(_msg("assistant", f"assistant reasoning {i}"))
            messages.append(_msg("user", f"tool result {i}"))
        return messages

    def test_recent_turns_kept_verbatim(self):
        """Tier 1 (recent_turns) 메시지는 [SUMMARY] 없이 전문 보존."""
        pruner = SemanticContextPruner(max_tokens=10_000, recent_turns=2, middle_turns=2)
        messages = self._build_history(10)
        result = pruner.fit(messages)

        # 마지막 2개 쌍(최근)은 전문이어야 함
        text_contents = [
            m.content for m in result
            if isinstance(m.content, str) and m.role in ("assistant", "user")
        ]
        # 최근 turn 은 SUMMARY 없어야 함 (원본 그대로)
        recent_texts = [t for t in text_contents if "assistant reasoning" in t or "tool result" in t]
        # 일부는 원본 텍스트로 남아있어야 함
        assert any("assistant reasoning" in t for t in recent_texts)

    def test_middle_turns_compressed(self):
        """Tier 2 (middle_turns) 메시지는 [SUMMARY] 로 압축된다."""
        pruner = SemanticContextPruner(max_tokens=10_000, recent_turns=2, middle_turns=2)
        messages = self._build_history(8)  # 8쌍: tier3(4) + tier2(2) + tier1(2)
        result = pruner.fit(messages)

        # [SUMMARY] 가 포함된 메시지가 있어야 함
        all_content = " ".join(
            m.content for m in result if isinstance(m.content, str)
        )
        assert "[SUMMARY]" in all_content

    def test_oldest_turns_dropped(self):
        """Tier 3 (oldest) 메시지는 결과에 포함되지 않는다."""
        pruner = SemanticContextPruner(max_tokens=10_000, recent_turns=2, middle_turns=2)
        messages = self._build_history(8)
        result = pruner.fit(messages)

        # 총 메시지 수 < 원본 메시지 수 (tier3 드롭됨)
        assert len(result) < len(messages)

    def test_no_pruning_when_all_fits_in_recent(self):
        """recent_turns 가 전체 턴 수보다 크면 droping 없음."""
        pruner = SemanticContextPruner(max_tokens=10_000, recent_turns=100, middle_turns=0)
        messages = self._build_history(5)
        result = pruner.fit(messages)
        # 5턴 < recent_turns=100 → 전문 보존
        assert len(result) == len(messages)


class TestSemanticContextPrunerScoring:
    """TF-IDF 관련성 점수 테스트."""

    def test_score_higher_for_relevant_message(self):
        """태스크와 관련된 메시지가 무관한 메시지보다 높은 점수."""
        pruner = SemanticContextPruner(
            max_tokens=10_000,
            task_description="write pytest unit tests for authentication module",
        )
        relevant = _msg("user", "running pytest tests for auth login function")
        irrelevant = _msg("user", "cooking recipe for chocolate cake")
        assert pruner.score(relevant) > pruner.score(irrelevant)

    def test_score_zero_when_no_task(self):
        """태스크 설명이 없으면 score=0."""
        pruner = SemanticContextPruner(max_tokens=10_000, task_description="")
        msg = _msg("user", "something relevant")
        assert pruner.score(msg) == 0.0

    def test_update_task_changes_scores(self):
        """update_task() 후 새 태스크에 맞게 점수가 바뀐다."""
        pruner = SemanticContextPruner(
            max_tokens=10_000,
            task_description="pytest tests",
        )
        msg = _msg("user", "git commit changes to authentication module")
        score_before = pruner.score(msg)

        pruner.update_task("git commit and authentication")
        score_after = pruner.score(msg)

        assert score_after > score_before

    def test_score_range(self):
        """score 는 [0.0, 1.0] 범위 내여야 한다."""
        pruner = SemanticContextPruner(
            max_tokens=10_000,
            task_description="fix the login bug",
        )
        for content in ["login bug fixed", "unrelated topic", "login login login"]:
            s = pruner.score(_msg("user", content))
            assert 0.0 <= s <= 1.0, f"Score out of range: {s}"


class TestSemanticContextPrunerSummarize:
    """_summarize() 메서드 테스트."""

    def test_string_content_summarized(self):
        pruner = SemanticContextPruner(max_tokens=10_000)
        msg = _msg("assistant", "This is the first sentence. This is another one.")
        summary = pruner._summarize(msg)
        assert "[SUMMARY]" in summary.content
        assert summary.role == "assistant"

    def test_tool_use_block_summarized(self):
        pruner = SemanticContextPruner(max_tokens=10_000)
        msg = Message(
            role="assistant",
            content=[{"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a.py"}}],
        )
        summary = pruner._summarize(msg)
        assert "[SUMMARY]" in summary.content
        assert "read_file" in summary.content

    def test_tool_result_block_summarized(self):
        pruner = SemanticContextPruner(max_tokens=10_000)
        msg = Message(
            role="user",
            content=[{"type": "tool_result", "tool_use_id": "t1", "content": "file content here"}],
        )
        summary = pruner._summarize(msg)
        # tool_result 요약은 tool_use_id를 보존한 구조화된 블록으로 반환된다
        assert isinstance(summary.content, list)
        block = summary.content[0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "t1"
        assert "[SUMMARY]" in block["content"]

    def test_original_message_not_mutated(self):
        """요약 시 원본 Message 객체를 변경하지 않는다."""
        pruner = SemanticContextPruner(max_tokens=10_000)
        original_content = "Original content. Second sentence."
        msg = _msg("user", original_content)
        pruner._summarize(msg)
        assert msg.content == original_content


class TestSemanticContextPrunerPerformance:
    """성능 테스트 — 컨텍스트 관리가 지연을 유발하지 않아야 한다."""

    def test_fit_500_messages_under_50ms(self):
        """500개 메시지 fit() 이 50ms 미만으로 완료되어야 한다."""
        import time
        pruner = SemanticContextPruner(
            max_tokens=10_000,
            task_description="write tests",
            recent_turns=4,
            middle_turns=4,
        )
        messages = [_msg("user", "initial task")]
        for i in range(250):
            messages.append(_msg("assistant", f"assistant reasoning step {i} " * 5))
            messages.append(_msg("user", f"tool result output {i} " * 5))

        start = time.perf_counter()
        pruner.fit(messages)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 50, f"fit() took {elapsed_ms:.1f}ms (limit: 50ms)"

    def test_tool_pair_not_split_in_pruner(self):
        """SemanticContextPruner 에서도 tool_use/tool_result 쌍이 분리되지 않는다."""
        pruner = SemanticContextPruner(max_tokens=100, recent_turns=1, middle_turns=1)
        messages = [_msg("user", "task")]
        for i in range(5):
            messages.extend(_tool_pair_msgs(f"id{i}"))

        result = pruner.fit(messages)

        # 결과에서 tool_use_id 와 tool_result 의 tool_use_id 가 일치해야 함
        tool_use_ids = set()
        tool_result_ids = set()
        for m in result:
            if isinstance(m.content, list):
                for b in m.content:
                    if isinstance(b, dict):
                        if b.get("type") == "tool_use":
                            tool_use_ids.add(b.get("id"))
                        if b.get("type") == "tool_result":
                            tool_result_ids.add(b.get("tool_use_id"))

        # 존재하는 쌍은 양쪽 모두 있어야 함
        assert tool_use_ids == tool_result_ids
