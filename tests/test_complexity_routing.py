"""
tests/test_complexity_routing.py

complexity 기반 모델 라우팅 — 설정 매핑, 환경 변수 override, resolver 동작 검증.
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
    ROLE_IMPLEMENTER,
    ROLE_ORCHESTRATOR,
    ROLE_REVIEWER,
    ROLE_TEST_WRITER,
    ROLE_INTERVENTION,
    resolve_complexity_model,
)


# ── COMPLEXITY_MODEL_MAP 기본값 ──────────────────────────────────────────────


class TestComplexityMapDefaults:
    def test_has_all_three_tiers(self) -> None:
        assert set(backend_config.COMPLEXITY_MODEL_MAP.keys()) == {
            "simple", "standard", "complex"
        }

    def test_each_tier_has_required_keys(self) -> None:
        required = {"provider_fast", "model_fast", "provider_capable", "model_capable"}
        for tier, bucket in backend_config.COMPLEXITY_MODEL_MAP.items():
            assert required.issubset(bucket.keys()), (
                f"tier '{tier}'에 필수 키 누락: {required - set(bucket.keys())}"
            )

    def test_simple_default_uses_gpt_41_mini(self) -> None:
        assert backend_config.COMPLEXITY_MODEL_MAP["simple"]["model_fast"] == "gpt-4.1-mini"

    def test_complex_default_uses_gpt_5(self) -> None:
        assert backend_config.COMPLEXITY_MODEL_MAP["complex"]["model_fast"] == "gpt-5"


# ── 환경 변수 override ────────────────────────────────────────────────────────


class TestComplexityEnvOverride:
    def test_env_override_replaces_entry(self, monkeypatch) -> None:
        monkeypatch.setenv("COMPLEXITY_SIMPLE_FAST", "openai:custom-model-xyz")
        monkeypatch.setenv("COMPLEXITY_COMPLEX_CAPABLE", "claude:claude-test")
        # 모듈 reload로 env 재파싱
        reloaded = importlib.reload(backend_config)
        try:
            assert reloaded.COMPLEXITY_MODEL_MAP["simple"]["provider_fast"] == "openai"
            assert reloaded.COMPLEXITY_MODEL_MAP["simple"]["model_fast"] == "custom-model-xyz"
            assert reloaded.COMPLEXITY_MODEL_MAP["complex"]["provider_capable"] == "claude"
            assert reloaded.COMPLEXITY_MODEL_MAP["complex"]["model_capable"] == "claude-test"
        finally:
            # 다른 테스트에 영향 주지 않도록 원복
            monkeypatch.delenv("COMPLEXITY_SIMPLE_FAST", raising=False)
            monkeypatch.delenv("COMPLEXITY_COMPLEX_CAPABLE", raising=False)
            importlib.reload(backend_config)

    def test_malformed_env_ignored(self, monkeypatch) -> None:
        monkeypatch.setenv("COMPLEXITY_STANDARD_FAST", "no-colon-format")
        reloaded = importlib.reload(backend_config)
        try:
            # 기본값 유지
            assert reloaded.COMPLEXITY_MODEL_MAP["standard"]["model_fast"] == "gpt-5-mini"
        finally:
            monkeypatch.delenv("COMPLEXITY_STANDARD_FAST", raising=False)
            importlib.reload(backend_config)

    def test_parse_complexity_env_returns_none_for_malformed(self) -> None:
        assert backend_config._parse_complexity_env("__NONEXISTENT_VAR__") is None


# ── resolve_complexity_model ──────────────────────────────────────────────────


@pytest.fixture
def sample_map() -> dict[str, dict[str, str]]:
    return {
        "simple": {
            "provider_fast": "p1", "model_fast": "m1-fast",
            "provider_capable": "p1c", "model_capable": "m1-capable",
        },
        "standard": {
            "provider_fast": "p2", "model_fast": "m2-fast",
            "provider_capable": "p2c", "model_capable": "m2-capable",
        },
        "complex": {
            "provider_fast": "p3", "model_fast": "m3-fast",
            "provider_capable": "p3c", "model_capable": "m3-capable",
        },
    }


class TestResolveComplexityModel:
    def test_simple_implementer_uses_fast(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_IMPLEMENTER, "simple", sample_map) == ("p1", "m1-fast")

    def test_standard_reviewer_uses_fast(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_REVIEWER, "standard", sample_map) == ("p2", "m2-fast")

    def test_complex_test_writer_uses_fast(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_TEST_WRITER, "complex", sample_map) == ("p3", "m3-fast")

    def test_orchestrator_uses_capable(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_ORCHESTRATOR, "complex", sample_map) == ("p3c", "m3-capable")

    def test_intervention_uses_capable(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_INTERVENTION, "simple", sample_map) == ("p1c", "m1-capable")

    def test_none_complexity_falls_back_to_standard(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_IMPLEMENTER, None, sample_map) == ("p2", "m2-fast")

    def test_unknown_complexity_falls_back_to_standard(self, sample_map) -> None:
        assert resolve_complexity_model(ROLE_IMPLEMENTER, "trivial", sample_map) == ("p2", "m2-fast")
