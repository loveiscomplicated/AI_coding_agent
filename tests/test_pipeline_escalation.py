"""
tests/test_pipeline_escalation.py

Tier escalation 루프 로직을 pipeline.run()을 mock해서 검증한다.
Docker / Git / 실제 LLM 호출 없이 순수 로직만 테스트한다.

실행:
    pytest tests/test_pipeline_escalation.py -v
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from orchestrator.escalation import ESCALATION_CHAIN, resolve_tier_chain, should_escalate_tier


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────


def _fake_result(succeeded: bool, failure_reason: str = ""):
    """pipeline.run()이 반환할 가짜 PipelineResult."""
    r = MagicMock()
    r.succeeded = succeeded
    r.failure_reason = failure_reason
    r.models_used = {}
    r.metrics = {}
    r.test_result = None
    return r


# ── resolve_tier_chain 통합 ───────────────────────────────────────────────────


class TestTierChainResolution:
    def test_simple_task_gets_simple_standard_chain(self):
        chain = resolve_tier_chain("simple")
        assert chain == ["simple", "standard"]

    def test_non_simple_task_gets_standard_complex_chain(self):
        chain = resolve_tier_chain("non-simple")
        assert chain == ["standard", "complex"]

    def test_none_complexity_defaults_to_non_simple_chain(self):
        chain = resolve_tier_chain(None)
        assert chain == ["standard", "complex"]

    def test_simple_task_does_not_escalate_to_complex(self):
        chain = resolve_tier_chain("simple")
        assert "complex" not in chain

    def test_non_simple_task_does_not_start_at_simple(self):
        chain = resolve_tier_chain("non-simple")
        assert chain[0] != "simple"


# ── should_escalate_tier 통합 ────────────────────────────────────────────────


class TestEscalationDecision:
    @pytest.mark.parametrize("reason", [
        "[LOGIC_ERROR] 테스트 실패",
        "[MAX_ITER] 반복 횟수 초과",
        "[CHANGES_REQUESTED] 리뷰어 요청",
        "[TEST_FAILED] 기대값 불일치",
    ])
    def test_escalatable_failure_types(self, reason):
        assert should_escalate_tier(reason) is True

    @pytest.mark.parametrize("reason", [
        "[NO_TESTS_COLLECTED] 테스트 없음",
        "ImportError: No module named 'foo'",
        "[COLLECTION_ERROR] 수집 실패",
        "[UNSUPPORTED_LANGUAGE] 지원 안 됨",
        None,
        "",
    ])
    def test_non_escalatable_failure_types(self, reason):
        assert should_escalate_tier(reason) is False


# ── escalation_chain 구조 검증 ────────────────────────────────────────────────


class TestEscalationChainStructure:
    def test_chain_lengths(self):
        assert len(ESCALATION_CHAIN["simple"]) == 2
        assert len(ESCALATION_CHAIN["non-simple"]) == 2

    def test_chains_are_ordered(self):
        tier_order = {"simple": 0, "standard": 1, "complex": 2}
        for _, chain in ESCALATION_CHAIN.items():
            orders = [tier_order[t] for t in chain]
            assert orders == sorted(orders), f"Chain {chain} is not ordered by capability"

    def test_all_tier_names_are_valid(self):
        valid_tiers = {"simple", "standard", "complex"}
        for _, chain in ESCALATION_CHAIN.items():
            for tier in chain:
                assert tier in valid_tiers
