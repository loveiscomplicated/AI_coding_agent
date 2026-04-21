"""
tests/test_complexity_integration.py

복잡도 기반 역할별 모델 라우팅이 파이프라인 전반(coder 역할 + intervention)에
일관되게 적용되는지 검증한다.
"""

from __future__ import annotations

import os
from unittest.mock import patch

if not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "test-key"

from agents.roles import (  # noqa: E402
    ROLE_IMPLEMENTER,
    ROLE_INTERVENTION,
    ROLE_MERGE_AGENT,
    ROLE_REVIEWER,
    ROLE_TEST_WRITER,
    RoleModelConfig,
    compose_role_override,
)
from orchestrator import intervention  # noqa: E402
from orchestrator import pipeline as pipeline_mod  # noqa: E402
from orchestrator.task import Task  # noqa: E402


DEFAULT_ROLE_MODELS = {
    "test_writer": {"provider": "claude", "model": "haiku"},
    "implementer": {"provider": "claude", "model": "haiku"},
    "reviewer": {"provider": "claude", "model": "haiku"},
    "merge_agent": {"provider": "claude", "model": "haiku"},
    "orchestrator": {"provider": "claude", "model": "opus"},
    "intervention": {"provider": "claude", "model": "opus"},
}

SAMPLE_MAP = {
    "simple": {
        "test_writer": {"provider": "openai", "model": "gpt-4.1-mini"},
        "implementer": {"provider": "openai", "model": "gpt-4.1-mini"},
        "reviewer": {"provider": "openai", "model": "gpt-4.1-mini"},
        "merge_agent": {"provider": "openai", "model": "gpt-4.1-mini"},
        "orchestrator": {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
        "intervention": {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
    },
    "standard": {
        "test_writer": {"provider": "openai", "model": "gpt-5-mini"},
        "implementer": {"provider": "openai", "model": "gpt-5-mini"},
        "reviewer": {"provider": "openai", "model": "gpt-5-mini"},
        "merge_agent": {"provider": "openai", "model": "gpt-5-mini"},
        "orchestrator": {"provider": "gemini", "model": "gemini-2.5-flash"},
        "intervention": {"provider": "gemini", "model": "gemini-2.5-flash"},
    },
    "complex": {
        "test_writer": {"provider": "openai", "model": "gpt-5"},
        "implementer": {"provider": "openai", "model": "gpt-5"},
        "reviewer": {"provider": "openai", "model": "gpt-5"},
        "merge_agent": {"provider": "openai", "model": "gpt-5"},
        "orchestrator": {"provider": "gemini", "model": "gemini-3-pro-preview"},
        "intervention": {"provider": "gemini", "model": "gemini-3-pro-preview"},
    },
}


def _make_task(task_id: str, complexity: str | None) -> Task:
    return Task(
        id=task_id,
        title="t",
        description="d",
        acceptance_criteria=["a"],
        target_files=["a.py"],
        complexity=complexity,  # type: ignore[arg-type]
    )


class TestTDDPipelineRouting:
    def test_role_uses_complexity_entry(self):
        captured: list[tuple[str, str]] = []
        with patch.object(
            pipeline_mod,
            "create_client",
            side_effect=lambda provider, cfg: captured.append((provider, cfg.model)) or f"{provider}/{cfg.model}",
        ):
            pipeline = pipeline_mod.TDDPipeline(
                agent_llm="FB",
                default_role_models=DEFAULT_ROLE_MODELS,
                auto_select_by_complexity=True,
                complexity_map=SAMPLE_MAP,
            )
            result = pipeline._llm_for_role(ROLE_IMPLEMENTER, "FB", task=_make_task("t-simple", "simple"))
        assert captured == [("openai", "gpt-4.1-mini")]
        assert result == "openai/gpt-4.1-mini"

    def test_role_override_beats_complexity(self):
        captured: list[tuple[str, str]] = []
        with patch.object(
            pipeline_mod,
            "create_client",
            side_effect=lambda provider, cfg: captured.append((provider, cfg.model)) or "client",
        ):
            pipeline = pipeline_mod.TDDPipeline(
                agent_llm="FB",
                default_role_models=DEFAULT_ROLE_MODELS,
                auto_select_by_complexity=True,
                complexity_map=SAMPLE_MAP,
                role_models={
                    ROLE_IMPLEMENTER: RoleModelConfig(provider="anthropic", model="claude-override"),
                },
            )
            pipeline._llm_for_role(ROLE_IMPLEMENTER, "FB", task=_make_task("t-complex", "complex"))
        assert captured == [("anthropic", "claude-override")]

    def test_toggle_off_uses_default_role_models(self):
        captured: list[tuple[str, str]] = []
        with patch.object(
            pipeline_mod,
            "create_client",
            side_effect=lambda provider, cfg: captured.append((provider, cfg.model)) or "client",
        ):
            pipeline = pipeline_mod.TDDPipeline(
                agent_llm="FB",
                default_role_models=DEFAULT_ROLE_MODELS,
                auto_select_by_complexity=False,
                complexity_map=SAMPLE_MAP,
            )
            pipeline._llm_for_role(ROLE_IMPLEMENTER, "FB", task=_make_task("t-simple", "simple"))
        assert captured == [("claude", "haiku")]

    def test_partial_override_preserves_base_model(self):
        captured: list[tuple[str, str]] = []
        with patch.object(
            pipeline_mod,
            "create_client",
            side_effect=lambda provider, cfg: captured.append((provider, cfg.model)) or "client",
        ):
            pipeline = pipeline_mod.TDDPipeline(
                agent_llm="FB",
                default_role_models=DEFAULT_ROLE_MODELS,
                auto_select_by_complexity=True,
                complexity_map=SAMPLE_MAP,
                role_models={
                    ROLE_IMPLEMENTER: RoleModelConfig(provider="anthropic", model=None),
                },
            )
            pipeline._llm_for_role(ROLE_IMPLEMENTER, "FB", task=_make_task("t-complex", "complex"))
        assert captured == [("anthropic", "gpt-5")]

    def test_resolver_matches_actual_client(self):
        captured: list[tuple[str, str]] = []
        pipeline = pipeline_mod.TDDPipeline(
            agent_llm="FB",
            default_role_models=DEFAULT_ROLE_MODELS,
            auto_select_by_complexity=True,
            complexity_map=SAMPLE_MAP,
            role_models={
                ROLE_REVIEWER: RoleModelConfig(provider=None, model="custom-reviewer"),
            },
        )
        task = _make_task("t-complex", "complex")
        with patch.object(
            pipeline_mod,
            "create_client",
            side_effect=lambda provider, cfg: captured.append((provider, cfg.model)) or "client",
        ):
            pipeline._llm_for_role(ROLE_REVIEWER, "FB", task=task)
        assert pipeline._resolve_provider_model(ROLE_REVIEWER, task) == captured[0]
        assert captured[0] == ("openai", "custom-reviewer")


class TestInterventionRouting:
    def setup_method(self):
        intervention.set_model_config(DEFAULT_ROLE_MODELS)
        intervention.set_complexity_routing(False, None)
        intervention._analyze_llm = None
        intervention._report_llm = None

    def _fake_chat_llm(self, text: str):
        from types import SimpleNamespace

        class _FakeLLM:
            def chat(self, _messages):
                return SimpleNamespace(
                    content=[{"type": "text", "text": text}],
                    stop_reason="end_turn",
                )

        return _FakeLLM()

    def test_auto_select_on_uses_task_complexity(self, monkeypatch):
        captured: list[tuple[str, str]] = []

        def fake_create_client(provider, cfg):
            captured.append((provider, cfg.model))
            return self._fake_chat_llm("RETRY: hint")

        monkeypatch.setattr(intervention, "create_client", fake_create_client)
        intervention.set_complexity_routing(True, SAMPLE_MAP)

        intervention.analyze(_make_task("t-complex", "complex"), failure_reason="fail", attempt=1)
        assert captured == [("gemini", "gemini-3-pro-preview")]

    def test_partial_override_composes_with_complexity(self, monkeypatch):
        captured: list[tuple[str, str]] = []

        def fake_create_client(provider, cfg):
            captured.append((provider, cfg.model))
            return self._fake_chat_llm("RETRY: hint")

        monkeypatch.setattr(intervention, "create_client", fake_create_client)
        intervention.set_complexity_routing(True, SAMPLE_MAP)

        intervention.analyze(
            _make_task("t-complex", "complex"),
            failure_reason="fail",
            attempt=1,
            role_models={ROLE_INTERVENTION: RoleModelConfig(provider="anthropic", model=None)},
        )
        assert captured == [("anthropic", "gemini-3-pro-preview")]

    def test_auto_select_off_uses_global_analyze_llm(self, monkeypatch):
        intervention._analyze_llm = self._fake_chat_llm("RETRY: hint")
        captured: list[tuple[str, str]] = []
        monkeypatch.setattr(
            intervention,
            "create_client",
            lambda provider, cfg: captured.append((provider, cfg.model)) or self._fake_chat_llm("RETRY: hint"),
        )

        intervention.analyze(_make_task("t-simple", "simple"), failure_reason="fail", attempt=1)
        assert captured == []

    def test_report_auto_select_uses_standard_fallback_for_none_complexity(self, monkeypatch):
        captured: list[tuple[str, str]] = []

        def fake_create_client(provider, cfg):
            captured.append((provider, cfg.model))
            return self._fake_chat_llm("report")

        monkeypatch.setattr(intervention, "create_client", fake_create_client)
        intervention.set_complexity_routing(True, SAMPLE_MAP)

        intervention.generate_report_with_metrics(
            _make_task("t-none", None),
            failure_reason="fail",
            attempts=2,
            hints_tried=[],
        )
        assert captured == [("gemini", "gemini-2.5-flash")]

    def test_report_auto_select_off_uses_global_report_llm(self, monkeypatch):
        intervention._report_llm = self._fake_chat_llm("report")
        captured: list[tuple[str, str]] = []
        monkeypatch.setattr(
            intervention,
            "create_client",
            lambda provider, cfg: captured.append((provider, cfg.model)) or self._fake_chat_llm("report"),
        )

        intervention.generate_report_with_metrics(
            _make_task("t-simple", "simple"),
            failure_reason="fail",
            attempts=2,
            hints_tried=[],
        )
        assert captured == []


class TestMergeAgentOverride:
    def test_merge_agent_full_override(self):
        role_cfg = RoleModelConfig(provider="anthropic", model="custom-merge")
        provider, model = compose_role_override(role_cfg, "openai", "gpt-5-mini")
        assert (provider, model) == ("anthropic", "custom-merge")

    def test_merge_agent_partial_provider_override(self):
        role_cfg = RoleModelConfig(provider="anthropic", model=None)
        provider, model = compose_role_override(role_cfg, "openai", "gpt-5-mini")
        assert (provider, model) == ("anthropic", "gpt-5-mini")

    def test_merge_agent_partial_model_override(self):
        role_cfg = RoleModelConfig(provider=None, model="custom-merge")
        provider, model = compose_role_override(role_cfg, "openai", "gpt-5-mini")
        assert (provider, model) == ("openai", "custom-merge")
