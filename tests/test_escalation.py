"""
tests/test_escalation.py

orchestrator/escalation.py 단위 테스트.

실행:
    pytest tests/test_escalation.py -v
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from orchestrator.escalation import (
    ESCALATION_CHAIN,
    resolve_tier_chain,
    should_escalate_tier,
)


# ── resolve_tier_chain ────────────────────────────────────────────────────────


class TestResolveTierChain:
    def test_resolve_tier_chain_simple(self):
        assert resolve_tier_chain("simple") == ["simple", "standard"]

    def test_resolve_tier_chain_non_simple(self):
        assert resolve_tier_chain("non-simple") == ["standard", "complex"]

    def test_resolve_tier_chain_none_falls_back_to_non_simple(self):
        assert resolve_tier_chain(None) == ["standard", "complex"]

    def test_resolve_tier_chain_unknown_falls_back_to_non_simple(self):
        assert resolve_tier_chain("garbage") == ["standard", "complex"]

    def test_tier_chains_are_ordered_by_capability(self):
        simple_chain = resolve_tier_chain("simple")
        non_simple_chain = resolve_tier_chain("non-simple")
        assert simple_chain[0] == "simple"
        assert non_simple_chain[0] == "standard"
        assert non_simple_chain[-1] == "complex"


# ── should_escalate_tier ──────────────────────────────────────────────────────


class TestShouldEscalateTier:
    def test_should_escalate_logic_error(self):
        assert should_escalate_tier("[LOGIC_ERROR] 테스트 실패") is True

    def test_should_escalate_max_iter(self):
        assert should_escalate_tier("[MAX_ITER] 반복 횟수 초과") is True

    def test_should_escalate_changes_requested(self):
        # CHANGES_REQUESTED → classify_failure returns LOGIC_ERROR
        assert should_escalate_tier("[CHANGES_REQUESTED] 리뷰어 요청") is True

    def test_should_not_escalate_no_tests_collected(self):
        assert should_escalate_tier("[NO_TESTS_COLLECTED] 테스트 없음") is False

    def test_should_not_escalate_env_error(self):
        # classify_failure는 실제 에러 패턴으로 ENV_ERROR를 감지한다 ([ENV_ERROR] 접두사 아님)
        assert should_escalate_tier("ImportError: No module named 'foo'") is False

    def test_should_not_escalate_module_not_found(self):
        assert should_escalate_tier("ModuleNotFoundError: No module named 'bar'") is False

    def test_should_not_escalate_collection_error(self):
        assert should_escalate_tier("[COLLECTION_ERROR] pytest 수집 실패") is False

    def test_should_not_escalate_none(self):
        assert should_escalate_tier(None) is False

    def test_should_not_escalate_empty_string(self):
        assert should_escalate_tier("") is False
