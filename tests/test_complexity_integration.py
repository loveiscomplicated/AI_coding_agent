"""
tests/test_complexity_integration.py

복잡도 기반 모델 라우팅이 파이프라인 전반(coder 역할 + intervention)에
일관되게 적용되는지 검증한다.

- TDDPipeline._llm_for_role: role_models override > complexity mapping > 기본 해석
- intervention.analyze: 동일 우선순위로 per-task capable 모델 생성
- 프롬프트 rubric이 정량 판정을 보장하는지 (숫자 기준 언급 검증)
"""

from __future__ import annotations

import os

if not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "test-key"

from unittest.mock import patch

from agents.roles import (
    ROLE_IMPLEMENTER,
    ROLE_INTERVENTION,
    ROLE_MERGE_AGENT,
    ROLE_REVIEWER,
    ROLE_TEST_WRITER,
    RoleModelConfig,
    compose_role_override,
)
from orchestrator import intervention
from orchestrator import pipeline as pipeline_mod
from orchestrator.task import Task


# ── 공통 픽스처 ──────────────────────────────────────────────────────────────


SAMPLE_MAP: dict[str, dict[str, str]] = {
    "simple": {
        "provider_fast": "openai", "model_fast": "gpt-4.1-mini",
        "provider_capable": "gemini", "model_capable": "gemini-2.5-flash-lite",
    },
    "standard": {
        "provider_fast": "openai", "model_fast": "gpt-5-mini",
        "provider_capable": "gemini", "model_capable": "gemini-2.5-flash",
    },
    "complex": {
        "provider_fast": "openai", "model_fast": "gpt-5",
        "provider_capable": "gemini", "model_capable": "gemini-3-pro-preview",
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


# ── TDDPipeline._llm_for_role 우선순위 ──────────────────────────────────────


class TestTDDPipelineRouting:
    def test_coder_role_uses_complexity_fast_model(self):
        captured: list[tuple[str, str]] = []
        with patch.object(pipeline_mod, "create_client",
                          side_effect=lambda p, cfg: captured.append((p, cfg.model)) or f"{p}/{cfg.model}"):
            p = pipeline_mod.TDDPipeline(
                agent_llm="FB", auto_select_by_complexity=True, complexity_map=SAMPLE_MAP,
            )
            t = _make_task("t-simple", "simple")
            result = p._llm_for_role(ROLE_IMPLEMENTER, "FB", task=t)
        assert captured == [("openai", "gpt-4.1-mini")]
        assert result == "openai/gpt-4.1-mini"

    def test_complex_task_routes_to_complex_tier(self):
        captured: list[tuple[str, str]] = []
        with patch.object(pipeline_mod, "create_client",
                          side_effect=lambda p, cfg: captured.append((p, cfg.model)) or f"{p}/{cfg.model}"):
            p = pipeline_mod.TDDPipeline(
                agent_llm="FB", auto_select_by_complexity=True, complexity_map=SAMPLE_MAP,
            )
            t = _make_task("t-complex", "complex")
            p._llm_for_role(ROLE_TEST_WRITER, "FB", task=t)
        assert captured == [("openai", "gpt-5")]

    def test_none_complexity_falls_back_to_standard(self):
        captured: list[tuple[str, str]] = []
        with patch.object(pipeline_mod, "create_client",
                          side_effect=lambda p, cfg: captured.append((p, cfg.model)) or "c"):
            p = pipeline_mod.TDDPipeline(
                agent_llm="FB", auto_select_by_complexity=True, complexity_map=SAMPLE_MAP,
            )
            t = _make_task("t-none", None)
            p._llm_for_role(ROLE_IMPLEMENTER, "FB", task=t)
        assert captured == [("openai", "gpt-5-mini")]

    def test_role_models_override_beats_complexity(self):
        """role_models에 명시된 역할은 complexity mapping보다 우선."""
        captured: list[tuple[str, str]] = []
        with patch.object(pipeline_mod, "create_client",
                          side_effect=lambda p, cfg: captured.append((p, cfg.model)) or "c"):
            p = pipeline_mod.TDDPipeline(
                agent_llm="FB",
                auto_select_by_complexity=True,
                complexity_map=SAMPLE_MAP,
                role_models={
                    ROLE_IMPLEMENTER: RoleModelConfig(provider="anthropic", model="claude-override"),
                },
            )
            t = _make_task("t-simple", "simple")  # 원래는 gpt-4.1-mini
            p._llm_for_role(ROLE_IMPLEMENTER, "FB", task=t)
        # override 승리
        assert captured == [("anthropic", "claude-override")]

    def test_role_models_not_set_falls_through_to_complexity(self):
        """role_models에 없는 역할은 complexity mapping 적용."""
        captured: list[tuple[str, str]] = []
        with patch.object(pipeline_mod, "create_client",
                          side_effect=lambda p, cfg: captured.append((p, cfg.model)) or "c"):
            p = pipeline_mod.TDDPipeline(
                agent_llm="FB",
                auto_select_by_complexity=True,
                complexity_map=SAMPLE_MAP,
                role_models={
                    ROLE_INTERVENTION: RoleModelConfig(provider="anthropic", model="claude"),
                },
            )
            t = _make_task("t-complex", "complex")
            p._llm_for_role(ROLE_TEST_WRITER, "FB", task=t)  # intervention override 는 이 역할에 영향 없음
        assert captured == [("openai", "gpt-5")]

    def test_toggle_off_uses_legacy_resolution(self):
        """auto_select=False면 기존 resolve_model_for_role 경로 유지."""
        captured: list[tuple[str, str]] = []
        with patch.object(pipeline_mod, "create_client",
                          side_effect=lambda p, cfg: captured.append((p, cfg.model)) or "c"):
            p = pipeline_mod.TDDPipeline(
                agent_llm="FB",
                auto_select_by_complexity=False,
                complexity_map=SAMPLE_MAP,
                provider="claude",
                model_fast="haiku",
                model_capable="opus",
            )
            t = _make_task("t-simple", "simple")
            p._llm_for_role(ROLE_IMPLEMENTER, "FB", task=t)
        # complexity mapping 무시, model_fast 사용
        assert captured == [("claude", "haiku")]

    def test_partial_provider_override_preserves_complexity_model(self):
        """role_models에 provider만 지정되면 model은 complexity mapping 값을 유지한다."""
        captured: list[tuple[str, str]] = []
        with patch.object(pipeline_mod, "create_client",
                          side_effect=lambda p, cfg: captured.append((p, cfg.model)) or "c"):
            p = pipeline_mod.TDDPipeline(
                agent_llm="FB",
                auto_select_by_complexity=True,
                complexity_map=SAMPLE_MAP,
                role_models={
                    ROLE_IMPLEMENTER: RoleModelConfig(provider="anthropic", model=None),
                },
            )
            t = _make_task("t-complex", "complex")  # complex tier fast: openai/gpt-5
            p._llm_for_role(ROLE_IMPLEMENTER, "FB", task=t)
        # provider는 override, model은 complexity default (gpt-5) 유지
        assert captured == [("anthropic", "gpt-5")]

    def test_partial_model_override_preserves_complexity_provider(self):
        """model만 지정되면 provider는 complexity mapping 값을 유지한다."""
        captured: list[tuple[str, str]] = []
        with patch.object(pipeline_mod, "create_client",
                          side_effect=lambda p, cfg: captured.append((p, cfg.model)) or "c"):
            p = pipeline_mod.TDDPipeline(
                agent_llm="FB",
                auto_select_by_complexity=True,
                complexity_map=SAMPLE_MAP,
                role_models={
                    ROLE_TEST_WRITER: RoleModelConfig(provider=None, model="custom-model"),
                },
            )
            t = _make_task("t-simple", "simple")  # simple fast provider: openai
            p._llm_for_role(ROLE_TEST_WRITER, "FB", task=t)
        assert captured == [("openai", "custom-model")]


class TestModelsUsedConsistency:
    """_llm_for_role과 _run_pipeline의 models_used 기록이 어긋나지 않는지 검증.

    핵심 불변식: `_resolve_provider_model(role, task)` 결과 == 실제 호출 모델.
    """

    def _make_pipeline(self, **kwargs) -> pipeline_mod.TDDPipeline:
        return pipeline_mod.TDDPipeline(
            agent_llm="FB",
            auto_select_by_complexity=kwargs.get("auto_select", False),
            complexity_map=SAMPLE_MAP if kwargs.get("auto_select") else None,
            role_models=kwargs.get("role_models"),
            provider=kwargs.get("provider"),
            model_fast=kwargs.get("model_fast"),
            model_capable=kwargs.get("model_capable"),
            provider_fast=kwargs.get("provider_fast"),
            provider_capable=kwargs.get("provider_capable"),
        )

    def test_resolver_matches_actual_client_under_complexity(self):
        """auto_select ON + complexity 매핑만 → resolver와 실제 호출 일치."""
        captured: list[tuple[str, str]] = []
        p = self._make_pipeline(auto_select=True)
        t = _make_task("t1", "complex")
        with patch.object(pipeline_mod, "create_client",
                          side_effect=lambda pv, cfg: captured.append((pv, cfg.model)) or "c"):
            p._llm_for_role(ROLE_IMPLEMENTER, "FB", task=t)
        resolved = p._resolve_provider_model(ROLE_IMPLEMENTER, t)
        assert captured[0] == resolved, f"captured={captured[0]}, resolved={resolved}"

    def test_resolver_matches_actual_client_with_partial_override(self):
        """auto_select + role_models partial override에서도 resolver == 실제 호출."""
        captured: list[tuple[str, str]] = []
        p = self._make_pipeline(
            auto_select=True,
            role_models={ROLE_IMPLEMENTER: RoleModelConfig(provider="anthropic", model=None)},
        )
        t = _make_task("t1", "complex")
        with patch.object(pipeline_mod, "create_client",
                          side_effect=lambda pv, cfg: captured.append((pv, cfg.model)) or "c"):
            p._llm_for_role(ROLE_IMPLEMENTER, "FB", task=t)
        resolved = p._resolve_provider_model(ROLE_IMPLEMENTER, t)
        assert captured[0] == resolved
        # provider는 override, model은 complexity default 그대로
        assert captured[0] == ("anthropic", "gpt-5")

    def test_models_used_reflects_partial_override(self):
        """_run_pipeline 초반의 models_used 계산이 partial override를 반영한다."""
        p = self._make_pipeline(
            auto_select=True,
            role_models={
                ROLE_IMPLEMENTER: RoleModelConfig(provider="anthropic", model=None),
                ROLE_REVIEWER:    RoleModelConfig(provider=None, model="custom-reviewer"),
            },
        )
        t = _make_task("t1", "complex")

        # _run_pipeline 본체는 복잡하므로 models_used 계산 로직만 직접 재현
        # (실제 파이프라인 실행 경로와 동일한 _resolve_provider_model 호출)
        models_used: dict[str, str] = {}
        for role in (ROLE_TEST_WRITER, ROLE_IMPLEMENTER, ROLE_REVIEWER):
            resolved = p._resolve_provider_model(role, t)
            if resolved:
                models_used[role] = f"{resolved[0]}/{resolved[1]}"

        # test_writer: 매핑 그대로 (openai/gpt-5)
        assert models_used[ROLE_TEST_WRITER] == "openai/gpt-5"
        # implementer: provider override, model=complexity (anthropic/gpt-5)
        assert models_used[ROLE_IMPLEMENTER] == "anthropic/gpt-5"
        # reviewer: provider=complexity, model override (openai/custom-reviewer)
        assert models_used[ROLE_REVIEWER] == "openai/custom-reviewer"

    def test_legacy_path_models_used_reflects_role_models(self):
        """auto_select=OFF + role_models override에서도 models_used 일관."""
        p = self._make_pipeline(
            auto_select=False,
            provider="claude",
            model_fast="haiku",
            model_capable="opus",
            role_models={
                ROLE_TEST_WRITER: RoleModelConfig(provider="anthropic", model="custom-tw"),
            },
        )
        t = _make_task("t1", None)

        resolved_tw = p._resolve_provider_model(ROLE_TEST_WRITER, t)
        resolved_impl = p._resolve_provider_model(ROLE_IMPLEMENTER, t)

        assert resolved_tw == ("anthropic", "custom-tw")
        # implementer: role_models 없음 → 기본 fast 해석
        assert resolved_impl == ("claude", "haiku")


# ── intervention.analyze 복잡도 라우팅 ───────────────────────────────────────


class TestInterventionRouting:
    def _reset_intervention_globals(self):
        intervention.set_model_config(
            provider="claude", model_fast="", model_capable="capable-default",
        )
        intervention.set_complexity_routing(False, None)
        intervention._analyze_llm = None
        intervention._report_llm = None

    def test_auto_select_on_uses_task_complexity(self, monkeypatch):
        self._reset_intervention_globals()

        captured: list[tuple[str, str]] = []
        def fake_create_client(provider, cfg):
            captured.append((provider, cfg.model))
            class _FakeLLM:
                def chat(self, _msgs):
                    from types import SimpleNamespace
                    return SimpleNamespace(
                        content=[{"type": "text", "text": "RETRY\nhint: do thing"}],
                        stop_reason="end_turn",
                    )
            return _FakeLLM()
        monkeypatch.setattr(intervention, "create_client", fake_create_client)

        intervention.set_complexity_routing(True, SAMPLE_MAP)

        task = _make_task("t-complex", "complex")
        intervention.analyze(task, failure_reason="test", attempt=1)

        assert captured == [("gemini", "gemini-3-pro-preview")], captured

    def test_role_models_override_beats_complexity_in_intervention(self, monkeypatch):
        self._reset_intervention_globals()
        captured: list[tuple[str, str]] = []
        def fake_create_client(provider, cfg):
            captured.append((provider, cfg.model))
            class _FakeLLM:
                def chat(self, _msgs):
                    from types import SimpleNamespace
                    return SimpleNamespace(
                        content=[{"type": "text", "text": "RETRY\nhint: x"}],
                        stop_reason="end_turn",
                    )
            return _FakeLLM()
        monkeypatch.setattr(intervention, "create_client", fake_create_client)

        intervention.set_complexity_routing(True, SAMPLE_MAP)

        task = _make_task("t-complex", "complex")
        intervention.analyze(
            task, failure_reason="test", attempt=1,
            role_models={
                ROLE_INTERVENTION: RoleModelConfig(provider="anthropic", model="claude-override"),
            },
        )
        assert captured == [("anthropic", "claude-override")]

    def test_auto_select_off_uses_global_llm(self, monkeypatch):
        """auto_select=False이고 role_models 없으면 전역 _analyze_llm 사용(새 client 생성 없음)."""
        self._reset_intervention_globals()

        class _FakeGlobalLLM:
            def chat(self, _msgs):
                from types import SimpleNamespace
                return SimpleNamespace(
                    content=[{"type": "text", "text": "RETRY\nhint: y"}],
                    stop_reason="end_turn",
                )
        intervention._analyze_llm = _FakeGlobalLLM()

        captured: list[tuple[str, str]] = []
        monkeypatch.setattr(intervention, "create_client",
                            lambda p, cfg: captured.append((p, cfg.model)) or _FakeGlobalLLM())

        task = _make_task("t-complex", "complex")
        intervention.analyze(task, failure_reason="test", attempt=1)

        # create_client 는 호출되지 않아야 한다 (전역 LLM 재사용)
        assert captured == []

    def test_partial_override_composes_with_complexity(self, monkeypatch):
        """role_models['intervention']에 provider만 지정 시 model은 complexity capable 유지."""
        self._reset_intervention_globals()
        captured: list[tuple[str, str]] = []
        def fake_create_client(provider, cfg):
            captured.append((provider, cfg.model))
            from types import SimpleNamespace
            class _FakeLLM:
                def chat(self, _):
                    return SimpleNamespace(
                        content=[{"type": "text", "text": "RETRY\nhint: x"}],
                        stop_reason="end_turn",
                    )
            return _FakeLLM()
        monkeypatch.setattr(intervention, "create_client", fake_create_client)

        intervention.set_complexity_routing(True, SAMPLE_MAP)

        task = _make_task("t-complex", "complex")
        # complex capable = gemini/gemini-3-pro-preview
        intervention.analyze(
            task, failure_reason="test", attempt=1,
            role_models={
                # provider만 override, model은 복잡도 기본값 유지되어야 함
                ROLE_INTERVENTION: RoleModelConfig(provider="anthropic", model=None),
            },
        )
        assert captured == [("anthropic", "gemini-3-pro-preview")]

    def test_partial_override_without_auto_select_uses_global_capable(self, monkeypatch):
        """auto_select=OFF일 때 partial override는 글로벌 capable을 base로 사용."""
        self._reset_intervention_globals()
        # auto_select OFF (default), model_capable = "capable-default" 사용
        captured: list[tuple[str, str]] = []
        def fake_create_client(provider, cfg):
            captured.append((provider, cfg.model))
            from types import SimpleNamespace
            class _FakeLLM:
                def chat(self, _):
                    return SimpleNamespace(
                        content=[{"type": "text", "text": "RETRY\nhint: y"}],
                        stop_reason="end_turn",
                    )
            return _FakeLLM()
        monkeypatch.setattr(intervention, "create_client", fake_create_client)

        task = _make_task("t-simple", "simple")
        intervention.analyze(
            task, failure_reason="test", attempt=1,
            role_models={
                ROLE_INTERVENTION: RoleModelConfig(provider="anthropic", model=None),
            },
        )
        # provider override + model은 글로벌 capable-default
        assert captured == [("anthropic", "capable-default")]


# ── intervention.generate_report_with_metrics 복잡도 라우팅 ──────────────────


class TestInterventionReportRouting:
    def _reset(self):
        intervention.set_model_config(
            provider="claude", model_fast="", model_capable="capable-default",
        )
        intervention.set_complexity_routing(False, None)
        intervention._analyze_llm = None
        intervention._report_llm = None

    def _fake_llm_returning(self, text: str):
        from types import SimpleNamespace
        class _FakeLLM:
            def chat(self, _msgs):
                return SimpleNamespace(
                    content=[{"type": "text", "text": text}],
                    stop_reason="end_turn",
                )
        return _FakeLLM()

    def test_report_auto_select_uses_task_complexity(self, monkeypatch):
        """보고서 생성도 auto_select ON 시 task.complexity로 모델을 선택한다."""
        self._reset()
        captured: list[tuple[str, str]] = []
        def fake_create_client(provider, cfg):
            captured.append((provider, cfg.model))
            return self._fake_llm_returning("보고서 본문")
        monkeypatch.setattr(intervention, "create_client", fake_create_client)

        intervention.set_complexity_routing(True, SAMPLE_MAP)

        task = _make_task("t-complex", "complex")
        intervention.generate_report_with_metrics(
            task, failure_reason="fail", attempts=3, hints_tried=[],
        )
        assert captured == [("gemini", "gemini-3-pro-preview")], captured

    def test_report_role_models_override_beats_complexity(self, monkeypatch):
        self._reset()
        captured: list[tuple[str, str]] = []
        def fake_create_client(provider, cfg):
            captured.append((provider, cfg.model))
            return self._fake_llm_returning("rpt")
        monkeypatch.setattr(intervention, "create_client", fake_create_client)

        intervention.set_complexity_routing(True, SAMPLE_MAP)

        task = _make_task("t-simple", "simple")
        intervention.generate_report_with_metrics(
            task, failure_reason="fail", attempts=2, hints_tried=[],
            role_models={
                ROLE_INTERVENTION: RoleModelConfig(provider="anthropic", model="claude-rpt"),
            },
        )
        assert captured == [("anthropic", "claude-rpt")]

    def test_report_auto_select_off_uses_global_report_llm(self, monkeypatch):
        """auto=OFF 이고 role_models 없으면 전역 _report_llm 재사용 (새 client 생성 없음)."""
        self._reset()
        intervention._report_llm = self._fake_llm_returning("rpt body")

        captured: list[tuple[str, str]] = []
        monkeypatch.setattr(intervention, "create_client",
                            lambda p, cfg: captured.append((p, cfg.model)) or self._fake_llm_returning("x"))

        task = _make_task("t-complex", "complex")
        intervention.generate_report_with_metrics(
            task, failure_reason="fail", attempts=2, hints_tried=[],
        )
        assert captured == []

    def test_report_none_complexity_falls_back_to_standard_capable(self, monkeypatch):
        self._reset()
        captured: list[tuple[str, str]] = []
        def fake_create_client(provider, cfg):
            captured.append((provider, cfg.model))
            return self._fake_llm_returning("rpt")
        monkeypatch.setattr(intervention, "create_client", fake_create_client)

        intervention.set_complexity_routing(True, SAMPLE_MAP)

        task = _make_task("t-nocx", None)
        intervention.generate_report_with_metrics(
            task, failure_reason="fail", attempts=2, hints_tried=[],
        )
        # None complexity → standard tier → gemini-2.5-flash (capable)
        assert captured == [("gemini", "gemini-2.5-flash")]

    def test_report_partial_override_composes_with_complexity(self, monkeypatch):
        """보고서 경로도 partial override를 complexity default와 합성한다."""
        self._reset()
        captured: list[tuple[str, str]] = []
        def fake_create_client(provider, cfg):
            captured.append((provider, cfg.model))
            return self._fake_llm_returning("rpt body")
        monkeypatch.setattr(intervention, "create_client", fake_create_client)

        intervention.set_complexity_routing(True, SAMPLE_MAP)

        task = _make_task("t-complex", "complex")
        intervention.generate_report_with_metrics(
            task, failure_reason="fail", attempts=3, hints_tried=[],
            role_models={
                # model만 override, provider는 complexity default (gemini) 유지
                ROLE_INTERVENTION: RoleModelConfig(provider=None, model="custom-report-model"),
            },
        )
        assert captured == [("gemini", "custom-report-model")]


# ── merge_agent override under auto_select ──────────────────────────────────


class TestMergeAgentOverride:
    """run.py의 merge_llm 생성 경로는 직접 호출하기 어려우므로,
    compose_role_override 헬퍼와 RoleModelConfig 조합만 단위 레벨로 검증한다."""

    def test_merge_agent_full_override(self):
        role_cfg = RoleModelConfig(provider="anthropic", model="custom-merge")
        p, m = compose_role_override(role_cfg, "openai", "gpt-5-mini")
        assert (p, m) == ("anthropic", "custom-merge")

    def test_merge_agent_partial_provider_override(self):
        role_cfg = RoleModelConfig(provider="anthropic", model=None)
        p, m = compose_role_override(role_cfg, "openai", "gpt-5-mini")
        assert (p, m) == ("anthropic", "gpt-5-mini")

    def test_merge_agent_partial_model_override(self):
        role_cfg = RoleModelConfig(provider=None, model="custom-merge")
        p, m = compose_role_override(role_cfg, "openai", "gpt-5-mini")
        assert (p, m) == ("openai", "custom-merge")

    def test_merge_agent_no_override(self):
        p, m = compose_role_override(None, "openai", "gpt-5-mini")
        assert (p, m) == ("openai", "gpt-5-mini")


# ── 프롬프트 rubric 정량성 검증 ──────────────────────────────────────────────


class TestRubricQuantification:
    def test_rubric_has_quantitative_table(self):
        from backend.routers.tasks import _DRAFT_SYSTEM_PROMPT
        # 수치 기준 표 존재
        assert "target_files" in _DRAFT_SYSTEM_PROMPT
        assert "depends_on" in _DRAFT_SYSTEM_PROMPT
        assert "acceptance_criteria" in _DRAFT_SYSTEM_PROMPT

    def test_rubric_describes_tier_resolution_rule(self):
        from backend.routers.tasks import _DRAFT_SYSTEM_PROMPT
        # "가장 높은 tier" 또는 "max" 같은 tie-breaking 규칙 명시
        assert ("가장 높은 tier" in _DRAFT_SYSTEM_PROMPT) or ("max" in _DRAFT_SYSTEM_PROMPT)

    def test_rubric_has_three_stage_procedure(self):
        from backend.routers.tasks import _DRAFT_SYSTEM_PROMPT
        assert "1단계" in _DRAFT_SYSTEM_PROMPT
        assert "2단계" in _DRAFT_SYSTEM_PROMPT
        assert "3단계" in _DRAFT_SYSTEM_PROMPT

    def test_rubric_specifies_auxiliary_threshold(self):
        """보조 지표는 '2개 이상'에서 승격한다는 규칙이 명시되어야 한다."""
        from backend.routers.tasks import _DRAFT_SYSTEM_PROMPT
        assert "2개 이상" in _DRAFT_SYSTEM_PROMPT
