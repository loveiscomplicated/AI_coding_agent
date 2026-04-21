"""
tests/test_complexity_routing.py

complexity 기반 역할별 모델 라우팅 — 설정 매핑, 환경 변수 override, resolver 동작 검증.
"""

from __future__ import annotations

import importlib
import os

import pytest

if not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "test-key"

import backend.config as backend_config  # noqa: E402
from agents.roles import (  # noqa: E402
    MODEL_ROLE_KEYS,
    ROLE_IMPLEMENTER,
    ROLE_INTERVENTION,
    ROLE_ORCHESTRATOR,
    ROLE_REVIEWER,
    ROLE_TEST_WRITER,
    resolve_complexity_model,
)


class TestComplexityMapDefaults:
    def test_has_all_three_tiers(self) -> None:
        assert set(backend_config.COMPLEXITY_ROLE_MODEL_MAP.keys()) == {
            "simple", "standard", "complex"
        }

    def test_each_tier_has_all_role_keys(self) -> None:
        required = set(MODEL_ROLE_KEYS)
        for tier, bucket in backend_config.COMPLEXITY_ROLE_MODEL_MAP.items():
            assert required.issubset(bucket.keys()), (
                f"tier '{tier}'에 필수 role 누락: {required - set(bucket.keys())}"
            )

    def test_simple_default_uses_gpt_41_mini_for_implementer(self) -> None:
        assert backend_config.COMPLEXITY_ROLE_MODEL_MAP["simple"]["implementer"]["model"] == "gpt-4.1-mini"

    def test_complex_default_uses_gpt_5_for_test_writer(self) -> None:
        assert backend_config.COMPLEXITY_ROLE_MODEL_MAP["complex"]["test_writer"]["model"] == "gpt-5"


class TestComplexityEnvOverride:
    def test_env_override_replaces_entry(self, monkeypatch) -> None:
        monkeypatch.setenv("COMPLEXITY_SIMPLE_ROLE_TEST_WRITER", "openai:custom-model-xyz")
        monkeypatch.setenv("COMPLEXITY_COMPLEX_ROLE_ORCHESTRATOR", "claude:claude-test")
        reloaded = importlib.reload(backend_config)
        try:
            assert reloaded.COMPLEXITY_ROLE_MODEL_MAP["simple"]["test_writer"]["provider"] == "openai"
            assert reloaded.COMPLEXITY_ROLE_MODEL_MAP["simple"]["test_writer"]["model"] == "custom-model-xyz"
            assert reloaded.COMPLEXITY_ROLE_MODEL_MAP["complex"]["orchestrator"]["provider"] == "claude"
            assert reloaded.COMPLEXITY_ROLE_MODEL_MAP["complex"]["orchestrator"]["model"] == "claude-test"
        finally:
            monkeypatch.delenv("COMPLEXITY_SIMPLE_ROLE_TEST_WRITER", raising=False)
            monkeypatch.delenv("COMPLEXITY_COMPLEX_ROLE_ORCHESTRATOR", raising=False)
            importlib.reload(backend_config)

    def test_malformed_env_ignored(self, monkeypatch) -> None:
        monkeypatch.setenv("COMPLEXITY_STANDARD_ROLE_IMPLEMENTER", "no-colon-format")
        reloaded = importlib.reload(backend_config)
        try:
            assert reloaded.COMPLEXITY_ROLE_MODEL_MAP["standard"]["implementer"]["model"] == "gpt-5-mini"
        finally:
            monkeypatch.delenv("COMPLEXITY_STANDARD_ROLE_IMPLEMENTER", raising=False)
            importlib.reload(backend_config)

    def test_parse_model_ref_env_returns_none_for_malformed(self) -> None:
        assert backend_config._parse_model_ref_env("__NONEXISTENT_VAR__") is None


@pytest.fixture
def sample_map() -> dict[str, dict[str, dict[str, str]]]:
    return {
        "simple": {
            "test_writer": {"provider": "p1", "model": "m1-fast"},
            "implementer": {"provider": "p1", "model": "m1-fast"},
            "reviewer": {"provider": "p1", "model": "m1-fast"},
            "merge_agent": {"provider": "p1", "model": "m1-fast"},
            "orchestrator": {"provider": "p1c", "model": "m1-capable"},
            "intervention": {"provider": "p1c", "model": "m1-capable"},
        },
        "standard": {
            "test_writer": {"provider": "p2", "model": "m2-fast"},
            "implementer": {"provider": "p2", "model": "m2-fast"},
            "reviewer": {"provider": "p2", "model": "m2-fast"},
            "merge_agent": {"provider": "p2", "model": "m2-fast"},
            "orchestrator": {"provider": "p2c", "model": "m2-capable"},
            "intervention": {"provider": "p2c", "model": "m2-capable"},
        },
        "complex": {
            "test_writer": {"provider": "p3", "model": "m3-fast"},
            "implementer": {"provider": "p3", "model": "m3-fast"},
            "reviewer": {"provider": "p3", "model": "m3-fast"},
            "merge_agent": {"provider": "p3", "model": "m3-fast"},
            "orchestrator": {"provider": "p3c", "model": "m3-capable"},
            "intervention": {"provider": "p3c", "model": "m3-capable"},
        },
    }


class TestResolveComplexityModel:
    def test_simple_implementer_uses_role_entry(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_IMPLEMENTER, "simple", sample_map) == ("p1", "m1-fast")

    def test_standard_reviewer_uses_role_entry(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_REVIEWER, "standard", sample_map) == ("p2", "m2-fast")

    def test_complex_test_writer_uses_role_entry(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_TEST_WRITER, "complex", sample_map) == ("p3", "m3-fast")

    def test_orchestrator_uses_role_specific_model(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_ORCHESTRATOR, "complex", sample_map) == ("p3c", "m3-capable")

    def test_intervention_uses_role_specific_model(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_INTERVENTION, "simple", sample_map) == ("p1c", "m1-capable")

    def test_none_complexity_falls_back_to_standard(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_IMPLEMENTER, None, sample_map) == ("p2", "m2-fast")

    def test_unknown_complexity_falls_back_to_standard(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_IMPLEMENTER, "trivial", sample_map) == ("p2", "m2-fast")
