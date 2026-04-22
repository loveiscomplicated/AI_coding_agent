"""
orchestrator/pipeline.py — TDD 파이프라인 상태 머신

TDDPipeline 은 단일 Task 를 받아 다음 순서로 실행한다:

  WRITING_TESTS  → TestWriter (ScopedReactLoop + Haiku)
  IMPLEMENTING   → Implementer (ScopedReactLoop + Haiku)
  RUNNING_TESTS  → DockerTestRunner  [실패 시 IMPLEMENTING 으로 회귀, max 3회]
  REVIEWING      → Reviewer (ScopedReactLoop + Haiku)
  COMMITTING     → 호출자(run.py)가 GitWorkflow 실행

사용 예:
    from orchestrator.pipeline import TDDPipeline, PipelineResult
    from orchestrator.workspace import WorkspaceManager

    pipeline = TDDPipeline(agent_llm=haiku_client, test_runner=DockerTestRunner())

    with WorkspaceManager(task, repo_path) as ws:
        result = pipeline.run(task, ws)

    if result.succeeded:
        print(result.review.verdict)
    else:
        print(result.failure_reason)
"""

from __future__ import annotations

import ast
import glob
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from agents.roles import (
    TEST_WRITER, IMPLEMENTER, REVIEWER,
    RoleModelConfig,
    resolve_model_for_role,
    ROLE_TEST_WRITER, ROLE_IMPLEMENTER, ROLE_REVIEWER,
    ROLE_ORCHESTRATOR, ROLE_INTERVENTION,
)
from agents.scoped_loop import ScopedReactLoop, ScopedResult
from docker.runner import DockerTestRunner, RunResult, _detect_runtime
from llm import LLMConfig, create_client
from llm.base import Message, StopReason
from orchestrator.quality_gate import (
    QGVerdict, RULES as QG_RULES,
    run_quality_gate, verdict_to_rule_results_dict,
)
from orchestrator.task import Task, TaskStatus, LANGUAGE_TEST_FRAMEWORK_MAP
from orchestrator.workspace import (
    WorkspaceManager, strip_src_prefix, is_skeleton_unchanged,
)
from tools.hotline_tools import register_workspace_context_dir, unregister_workspace_context_dir

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
MAX_REVIEW_RETRIES = 1   # Reviewer CHANGES_REQUESTED → Implementer 재시도 최대 횟수


class _StopRequested(BaseException):
    """
    사용자 즉시 중단 요청 시 on_iteration 훅에서 raise되는 sentinel.

    BaseException 을 상속하여 `except Exception` 블록에 잡히지 않고
    ScopedReactLoop → TDDPipeline.run() 까지 깔끔하게 전파된다.
    """


# ── 결과 데이터 클래스 ────────────────────────────────────────────────────────


VALID_VERDICTS = {
    "APPROVED",
    "APPROVED_WITH_SUGGESTIONS",
    "CHANGES_REQUESTED",
    "ERROR",
}


@dataclass
class ReviewResult:
    """Reviewer 에이전트 출력 파싱 결과."""

    verdict: str   # "APPROVED" | "APPROVED_WITH_SUGGESTIONS" | "CHANGES_REQUESTED" | "ERROR"
    summary: str
    details: str
    raw: str       # 원문 (PR body 에 그대로 포함)

    @property
    def approved(self) -> bool:
        """PR 생성 여부 — APPROVED 또는 APPROVED_WITH_SUGGESTIONS."""
        return self.verdict in ("APPROVED", "APPROVED_WITH_SUGGESTIONS")

    @property
    def has_suggestions(self) -> bool:
        """비-블로킹 개선 제안이 있는지 (PR body에 suggestions 섹션 추가용)."""
        return self.verdict == "APPROVED_WITH_SUGGESTIONS"

    @property
    def is_error(self) -> bool:
        """Reviewer LLM 실패 등 인프라 에러로 판정이 불가했던 경우."""
        return self.verdict == "ERROR"


@dataclass
class PipelineMetrics:
    """파이프라인 단계별 세부 메트릭. 변경 1·2 효과 측정 및 3번 결정에 사용."""

    quality_gate_rejections: int = 0        # 테스트 품질 게이트 reject 횟수 (assert False 등)
    quality_gate_reasons: list[str] = field(default_factory=list)
    quality_gate_verdict: str | None = None  # "PASS" | "WARNING" | "BLOCKED" | None
    quality_gate_rule_results: list[dict] = field(default_factory=list)
    test_red_to_green_first_try: bool = False  # 첫 구현 시도에서 테스트 통과 여부
    impl_retries: int = 0                   # Implementer 재시도 횟수
    review_retries: int = 0                 # Reviewer 피드백 후 재구현 횟수
    dep_files_injected: int = 0             # 선행 태스크에서 주입된 파일 수
    failed_stage: str = ""                  # 실패 시 단계: "test_writing" | "implementing" | "testing" | "reviewing"
    # 역할별 토큰 사용량 {role: (input_tokens, output_tokens, cached_read, cached_write)}
    token_usage: dict = field(default_factory=dict)
    call_logs: dict = field(default_factory=dict)  # {role: [call_log entries]}


@dataclass
class PipelineResult:
    """TDDPipeline.run() 의 최종 반환값."""

    task: Task
    succeeded: bool
    failure_reason: str = ""
    test_result: RunResult | None = None
    review: ReviewResult | None = None
    test_files: list[str] = field(default_factory=list)
    impl_files: list[str] = field(default_factory=list)
    metrics: PipelineMetrics = field(default_factory=PipelineMetrics)
    models_used: dict[str, str] = field(default_factory=dict)

    @classmethod
    def failed(
        cls,
        task: Task,
        reason: str,
        metrics: "PipelineMetrics | None" = None,
        test_result: RunResult | None = None,
    ) -> "PipelineResult":
        task.status = TaskStatus.FAILED
        task.failure_reason = reason
        return cls(
            task=task,
            succeeded=False,
            failure_reason=reason,
            test_result=test_result,
            metrics=metrics or PipelineMetrics(),
        )


# ── 파이프라인 ────────────────────────────────────────────────────────────────


class TDDPipeline:
    """
    단일 태스크를 TDD 흐름으로 실행하는 파이프라인.

    agent_llm       : TestWriter / Reviewer 용 LLM (Haiku 권장)
    implementer_llm : Implementer 전용 LLM. None이면 agent_llm 사용.
                      복잡한 구현 태스크는 Sonnet을 권장한다.
    test_runner     : DockerTestRunner 인스턴스 (주입 가능, 테스트 시 mock 용이)
    max_retries     : Implementer 재시도 최대 횟수
    """

    def __init__(
        self,
        agent_llm,
        implementer_llm=None,
        test_runner: DockerTestRunner | None = None,
        max_retries: int = MAX_RETRIES,
        max_review_retries: int = MAX_REVIEW_RETRIES,
        max_iterations: int = 20,
        reviewer_max_iterations: int = 8,
        implementer_write_deadline: int = 8,
        test_writer_write_deadline: int = 5,
        # 역할별 모델 오버라이드 (None이면 default_role_models 사용)
        role_models: dict[str, RoleModelConfig] | None = None,
        default_role_models: dict[str, RoleModelConfig | dict[str, str]] | None = None,
        role_compaction_tuning_enabled: bool = False,
        role_compaction_tuning_preset: str = "balanced",
        role_compaction_tuning_overrides: dict[str, str] | None = None,
        auto_select_by_complexity: bool = False,
        complexity_map: dict[str, dict[str, RoleModelConfig | dict[str, str]]] | None = None,
        skip_test_writer: bool = False,
    ):
        self.agent_llm = agent_llm
        self.implementer_llm = implementer_llm or agent_llm
        self.test_runner = test_runner or DockerTestRunner()
        self.max_retries = max_retries
        self.max_review_retries = max_review_retries
        self.max_iterations = max_iterations
        self.reviewer_max_iterations = reviewer_max_iterations
        self.implementer_write_deadline = implementer_write_deadline
        self.test_writer_write_deadline = test_writer_write_deadline
        self.role_models = role_models
        self.default_role_models = default_role_models or {}
        self.role_compaction_tuning_enabled = role_compaction_tuning_enabled
        self.role_compaction_tuning_preset = role_compaction_tuning_preset
        self.role_compaction_tuning_overrides = role_compaction_tuning_overrides or {}
        self.auto_select_by_complexity = auto_select_by_complexity
        self.complexity_map = complexity_map or {}
        self._skip_test_writer = skip_test_writer
        # escalation tier override: _run_pipeline이 run() 호출 시 설정, 종료 시 리셋.
        # _resolve_provider_model이 per-call tier보다 이 값을 우선하도록 설계됨.
        self._current_tier: str | None = None

    # ── 역할별 모델 해석 ─────────────────────────────────────────────────────

    def _resolve_provider_model(
        self, role: str, task: "Task | None", tier: str | None = None
    ) -> tuple[str, str] | None:
        """역할에 최종 적용될 (provider, model)을 반환한다.

        `_llm_for_role`과 `_run_pipeline`의 telemetry(`models_used`) 양쪽이 이 메서드를
        공유하여, 실제 호출 모델과 기록 모델이 절대 어긋나지 않게 한다.

        우선순위:
          1) role_models[role] 부분 override — compose_role_override로 base에 덮어씀
          2) tier 명시 (escalation 시 강제 tier) → 해당 tier 직접 사용
             또는 auto_select_by_complexity=True & task 있음 → task.complexity 기반 매핑
          3) default_role_models 기반 기본 해석
          4) model config 미주입 + 완전 override만 있음 → override 그대로 반환
          5) 그 외 → None (호출자가 fallback 처리)
        """
        from agents.roles import compose_role_override, resolve_complexity_model
        from agents.roles import _require_resolved_role_model

        # tier 파라미터가 없으면 현재 실행 중인 tier(_current_tier)를 사용
        effective_tier = tier if tier is not None else self._current_tier

        if self.auto_select_by_complexity and self.complexity_map:
            if effective_tier is not None and effective_tier in self.complexity_map:
                # escalation으로 tier가 명시된 경우 — task.complexity를 우회해 직접 사용
                base_p, base_m = _require_resolved_role_model(role, self.complexity_map[effective_tier])
            elif task is not None:
                # tier 미지정 — task.complexity 기반 (non-simple/simple → standard/simple tier로 fallback)
                base_p, base_m = resolve_complexity_model(role, task.complexity, self.complexity_map)
            else:
                base_p, base_m = resolve_complexity_model(role, None, self.complexity_map)
        elif self.default_role_models:
            base_p, base_m = resolve_model_for_role(
                role=role,
                role_models=None,  # override는 아래 compose에서 합성
                default_role_models=self.default_role_models,
            )
        else:
            role_cfg = (self.role_models or {}).get(role)
            if role_cfg and role_cfg.provider and role_cfg.model:
                return (role_cfg.provider, role_cfg.model)
            return None

        role_cfg = (self.role_models or {}).get(role)
        return compose_role_override(role_cfg, base_p, base_m)

    # ── 역할별 LLM 생성 ──────────────────────────────────────────────────────

    def _llm_for_role(self, role: str, fallback, task: "Task | None" = None, tier: str | None = None):
        """역할별 LLM 클라이언트를 생성한다. 해석은 `_resolve_provider_model`에 위임."""
        resolved = self._resolve_provider_model(role, task, tier=tier)
        if resolved is None:
            return fallback
        p, m = resolved
        return create_client(p, LLMConfig(model=m, max_tokens=8192))

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def run(
        self,
        task: Task,
        workspace: WorkspaceManager,
        on_progress=None,
        pause_ctrl=None,
        all_tasks: list[Task] | None = None,
        repo_path: str | None = None,
        tier: str | None = None,
    ) -> PipelineResult:
        """
        태스크 전체 파이프라인을 실행한다.
        workspace 는 이미 create() 된 상태여야 한다.

        on_progress : Callable[[dict], None] | None
            단계별 진행 상황을 전달받을 콜백. run.py 의 emit 을 래핑해서 넣는다.
            None 이면 아무것도 하지 않는다.
        pause_ctrl : PauseController | None
            중단 요청 감지용. is_stopped 가 True 이면 다음 LLM 응답 직후에
            _StopRequested 를 raise 해 파이프라인을 즉시 종료한다.
        all_tasks : list[Task] | None
            전체 태스크 목록 (선행 태스크 산출물 존재 확인용). None이면 pre-check 건너뜀.
        repo_path : str | None
            대상 레포 루트 경로 (선행 태스크 파일 존재 확인용). None이면 pre-check 건너뜀.
        """
        _p = on_progress or (lambda e: None)

        def _agent_p(e: dict) -> None:
            # 중단 요청이 있으면 on_iteration 콜백에서 _StopRequested 를 raise
            if pause_ctrl is not None and pause_ctrl.is_stopped:
                raise _StopRequested("사용자 즉시 중단 요청")
            merged = {**e, "task_id": task.id} if "task_id" not in e else e
            _p(merged)

        logger.info("[%s] 파이프라인 시작", task.id)

        # ── 선행 태스크 산출물 존재 확인 (pre-check) ──────────────────────────
        if all_tasks and repo_path and task.depends_on:
            missing = self._check_dependency_files(task, all_tasks, repo_path)
            if missing:
                failure_msg = (
                    f"[DEPENDENCY_MISSING] 선행 태스크의 산출물이 존재하지 않습니다:\n"
                    + "\n".join(f"  - {m}" for m in missing)
                    + "\n선행 태스크가 성공적으로 완료되고 머지되었는지 확인하세요."
                )
                logger.warning("[%s] %s", task.id, failure_msg)
                _p({"type": "step", "step": "dependency_check_failed",
                    "message": failure_msg})
                return PipelineResult.failed(task, failure_msg)

        # 선행 태스크 산출물 정보를 description에 추가 (원본 미수정)
        enriched_desc = self._enrich_description(task, all_tasks)

        # hotline decisions.md가 ask_user 확정 후 workspace에도 동기화되도록 등록
        register_workspace_context_dir(task.id, workspace.tests_dir.parent / "context")
        try:
            return self._run_pipeline(task, workspace, _p, _agent_p, pause_ctrl=pause_ctrl,
                                      enriched_desc=enriched_desc, tier=tier)
        finally:
            unregister_workspace_context_dir(task.id)

    @staticmethod
    def _enrich_description(task: Task, all_tasks: list[Task] | None) -> str:
        """선행 태스크의 target_files 정보를 description에 추가한다. 원본은 수정하지 않음."""
        if not all_tasks or not task.depends_on:
            return task.description
        dep_lines = []
        for dep_id in task.depends_on:
            dep = next((t for t in all_tasks if t.id == dep_id), None)
            if dep and dep.target_files:
                files = ", ".join(dep.target_files)
                dep_lines.append(f"- {dep_id} ({dep.title}): {files}")
        if not dep_lines:
            return task.description
        return (
            task.description
            + "\n\n## 선행 태스크 산출물 (workspace에 이미 존재하는 파일)\n"
            + "\n".join(dep_lines)
        )

    def _check_dependency_files(self, task: Task, all_tasks: list[Task], base_path: str) -> list[str]:
        """선행 태스크의 target_files가 존재하는지 확인. 없는 파일 경로 리스트 반환.

        완료된(DONE) 선행 태스크는 스킵한다 — 산출물이 git 브랜치에 존재하며,
        inject_dependency_context()가 git show로 읽어 workspace에 주입하기 때문이다.
        auto_merge 없이도 정상 동작해야 하므로 filesystem 존재 여부가 아닌
        태스크 상태를 기준으로 판단한다.
        """
        missing = []
        for dep_id in (task.depends_on or []):
            dep_task = next((t for t in all_tasks if t.id == dep_id), None)
            if dep_task is None:
                continue
            # 완료된 태스크는 브랜치에 산출물 존재 → workspace 주입으로 처리
            if dep_task.status == TaskStatus.DONE:
                continue
            # 미완료 태스크: filesystem에서 확인 (수동 추가 등 대비)
            for filepath in (dep_task.target_files or []):
                full_path = os.path.join(base_path, filepath)
                if not os.path.exists(full_path):
                    missing.append(f"{dep_id}:{filepath}")
        return missing

    def _run_pipeline(self, task, workspace, _p, _agent_p, pause_ctrl=None, enriched_desc=None, tier=None) -> "PipelineResult":
        """run()의 실제 파이프라인 로직. workspace 등록/해제는 run()이 담당한다."""
        # escalation tier를 인스턴스 변수로 저장 — _run_test_writer 등 내부 메서드가
        # _llm_for_role 호출 시 self._current_tier를 통해 참조한다.
        self._current_tier = tier
        try:
            # 역할별 실제 사용 모델을 미리 계산 — 성공/실패 양쪽 모두 TaskReport에 기록.
            # _llm_for_role과 동일한 _resolve_provider_model 경로를 사용하여
            # 실제 호출 모델과 기록 모델이 어긋나지 않음을 보장한다.
            models_used: dict[str, str] = {}
            for _role_key in [ROLE_TEST_WRITER, ROLE_IMPLEMENTER, ROLE_REVIEWER]:
                _resolved = self._resolve_provider_model(_role_key, task, tier=tier)
                if _resolved is not None:
                    _rp, _rm = _resolved
                    models_used[_role_key] = f"{_rp}/{_rm}"

            try:
                result = self.__run_pipeline_inner(task, workspace, _p, _agent_p, pause_ctrl=pause_ctrl,
                                                   enriched_desc=enriched_desc, tier=tier)
            except _StopRequested:
                logger.info("[%s] 즉시 중단 요청 — 파이프라인 종료", task.id)
                result = PipelineResult.failed(task, "[ABORTED] 사용자 즉시 중단 요청")

            # 실제 API 응답 모델명(버전 suffix 포함)으로 덮어쓴다.
            # call_log가 없는 역할(파이프라인이 해당 역할 전 실패)은 사전 계산값 유지.
            for _role_key in [ROLE_TEST_WRITER, ROLE_IMPLEMENTER, ROLE_REVIEWER]:
                _logs = result.metrics.call_logs.get(_role_key, [])
                if _logs:
                    _actual_model = _logs[-1].get("model", "")
                    if _actual_model and _role_key in models_used:
                        _provider = models_used[_role_key].split("/")[0]
                        models_used[_role_key] = f"{_provider}/{_actual_model}"

            result.models_used = models_used
            return result
        finally:
            self._current_tier = None

    def __run_pipeline_inner(self, task, workspace, _p, _agent_p, pause_ctrl=None, enriched_desc=None, tier=None) -> "PipelineResult":
        """_run_pipeline() 의 실제 구현. _StopRequested 는 _run_pipeline() 이 잡는다."""
        metrics = PipelineMetrics()

        # 선행 태스크 주입 파일 수 기록
        dep_artifact = workspace.path / "context" / "dependency_artifacts.md"
        if dep_artifact.exists():
            # dep_files_injected 는 "실제로 주입된 선행 파일" 수여야 한다.
            # workspace/src 전체를 세면 현재 태스크 target_files 나 __init__.py 같은
            # 보조 파일이 섞이므로, dependency_artifacts.md 에 기록된 파일만
            # 현재 태스크의 target_files 와 같은 규약으로 비교해 집계한다.
            original_targets = {strip_src_prefix(f) for f in task.target_files}
            injected_files = _parse_dependency_artifact_files(dep_artifact)
            metrics.dep_files_injected = len(injected_files - original_targets)

        # ── Step 1 & 1.5: 테스트 작성 및 품질 게이트 (skip_test_writer=True 시 건너뜀) ─
        _created_test_files: list[str] = []   # TestWriter가 신규 생성한 파일만 저장
        if not self._skip_test_writer:
            # TestWriter 실행 전 기존 테스트 파일 스냅샷 (새로 생성된 파일만 DockerTestRunner에 전달하기 위함)
            _ws_path = str(workspace.path)
            _test_files_before = set(glob.glob(os.path.join(_ws_path, "**", "test_*.py"), recursive=True))

            task.status = TaskStatus.WRITING_TESTS
            _p({"type": "step", "step": "test_writing", "message": "TestWriter: 테스트 작성 중…"})
            test_scoped = self._run_test_writer(task, workspace, on_progress=_agent_p, pause_ctrl=pause_ctrl, enriched_desc=enriched_desc)
            _accumulate_tokens(metrics, "test_writer", test_scoped)

            # TestWriter 종료 가드:
            #  - LLM 실패(succeeded=False)  → 기존과 동일하게 WRITE_LOOP 여부로 분기
            #  - LLM 성공(succeeded=True)   → 출력 가드 실행 → 실패 사유가 있으면 재시도
            # 두 경우 모두 최대 1회 재시도 (기존 동작 유지) 후에도 실패면 파이프라인 실패.
            _retry_scoped: ScopedResult | None = None
            _retry_reason: str | None = None
            _retry_label: str = ""

            if not test_scoped.succeeded:
                if _is_write_loop(test_scoped):
                    _retry_reason = (
                        "[WRITE_LOOP] 탐색만 하고 write_file 을 호출하지 않은 채 종료했습니다."
                    )
                    _retry_label = "탐색 루프 감지 — TestWriter 재시도…"
                else:
                    prefix = "[MAX_ITER] " if _is_max_iter(test_scoped) else ""
                    metrics.failed_stage = "test_writing"
                    return PipelineResult.failed(task, f"{prefix}TestWriter 실패: {test_scoped.answer}", metrics=metrics)
            else:
                _retry_reason = _validate_testwriter_output(workspace, task, test_scoped)
                if _retry_reason:
                    _retry_label = f"TestWriter 출력 가드 — {_retry_reason[:80]}"

            if _retry_reason:
                # TaskReport 집계용: 가드 reject 횟수·사유 누적
                metrics.quality_gate_rejections += 1
                metrics.quality_gate_reasons.append(f"test_writer: {_retry_reason}")

                logger.warning("[%s] TestWriter 가드 — 재시도: %s", task.id, _retry_reason)
                _p({"type": "step", "step": "test_writing_retry", "message": _retry_label})
                test_scoped = self._run_test_writer(
                    task, workspace, retry=True,
                    on_progress=_agent_p, pause_ctrl=pause_ctrl,
                    enriched_desc=enriched_desc,
                    prior_failure_reason=_retry_reason,
                    prior_explored_paths=list(test_scoped.explored_paths),
                )
                _accumulate_tokens(metrics, "test_writer", test_scoped)

                if not test_scoped.succeeded:
                    prefix = "[MAX_ITER] " if _is_max_iter(test_scoped) else ""
                    metrics.failed_stage = "test_writing"
                    return PipelineResult.failed(
                        task,
                        f"{prefix}TestWriter 실패 (가드 후 재시도도 실패): {test_scoped.answer}",
                        metrics=metrics,
                    )
                # 재시도 후에도 출력 가드 위반이면 파이프라인 실패 (기존 retry 한도 소진)
                _final_reason = _validate_testwriter_output(workspace, task, test_scoped)
                if _final_reason:
                    metrics.quality_gate_rejections += 1
                    metrics.quality_gate_reasons.append(f"test_writer (retry): {_final_reason}")
                    metrics.failed_stage = "test_writing"
                    return PipelineResult.failed(
                        task,
                        f"TestWriter 가드 재시도 후에도 실패: {_final_reason}",
                        metrics=metrics,
                    )

            test_files = workspace.list_test_files()
            # 가드가 통과했다면 반드시 파일이 있다 — 방어적으로 한 번 더 확인.
            if not test_files:
                metrics.failed_stage = "test_writing"
                return PipelineResult.failed(
                    task,
                    "[TEST_MISSING] TestWriter 가 tests/ 에 파일을 생성하지 않았습니다.",
                    metrics=metrics,
                )

            _p({"type": "step", "step": "test_written",
                "message": f"테스트 파일 생성: {', '.join(test_files)}"})
            logger.info("[%s] 테스트 파일 생성: %s", task.id, test_files)

            # ── Step 1.5: Quality Gate — TestWriter 종료 직후 1회 실행 ─────────────
            #
            # 형식적 유효성(syntax / assertion / test_* 존재 / placeholder 금지 /
            # import 가능성 / 커버리지 heuristic) 을 룰 기반으로 검증한다.
            # BLOCKED → TestWriter 재시도 1회; 재시도 후에도 BLOCKED 면 파이프라인 실패.
            # WARNING → 진행하고 PR body 기록.
            # 기능적 올바름은 Reviewer 담당이므로 QG 는 여기서 단일 콜만 수행.
            qg_verdict = run_quality_gate(workspace.tests_dir, task)
            metrics.quality_gate_verdict = qg_verdict.verdict
            metrics.quality_gate_rule_results = verdict_to_rule_results_dict(qg_verdict)

            if qg_verdict.verdict == "BLOCKED":
                blocking_msgs = [
                    f"{rid}: {r.message}" for rid, r in qg_verdict.failed_rules("BLOCKING")
                ]
                metrics.quality_gate_rejections += 1
                metrics.quality_gate_reasons.extend(blocking_msgs)
                logger.warning(
                    "[%s] Quality Gate BLOCKED — %s", task.id, "; ".join(blocking_msgs),
                )
                issues_summary = "; ".join(blocking_msgs)
                _p({"type": "step", "step": "quality_gate",
                    "message": f"품질 게이트 재시도 — {issues_summary[:120]}"})
                test_scoped = self._run_test_writer(
                    task, workspace,
                    static_issues=blocking_msgs,
                    on_progress=_agent_p,
                    pause_ctrl=pause_ctrl,
                    enriched_desc=enriched_desc,
                )
                _accumulate_tokens(metrics, "test_writer", test_scoped)
                if not test_scoped.succeeded:
                    prefix = "[MAX_ITER] " if _is_max_iter(test_scoped) else ""
                    metrics.failed_stage = "test_writing"
                    return PipelineResult.failed(
                        task, f"{prefix}TestWriter 품질 보완 실패: {test_scoped.answer}",
                        metrics=metrics,
                    )
                # 재시도 후 QG 재실행 — 단일 콜 원칙 유지 (TestWriter 재시도 → 1회 재검증)
                qg_verdict_retry = run_quality_gate(workspace.tests_dir, task)
                metrics.quality_gate_verdict = qg_verdict_retry.verdict
                metrics.quality_gate_rule_results = verdict_to_rule_results_dict(qg_verdict_retry)
                if qg_verdict_retry.verdict == "BLOCKED":
                    blocking_msgs_retry = [
                        f"{rid}: {r.message}"
                        for rid, r in qg_verdict_retry.failed_rules("BLOCKING")
                    ]
                    metrics.quality_gate_rejections += 1
                    metrics.quality_gate_reasons.extend(blocking_msgs_retry)
                    metrics.failed_stage = "test_writing"
                    return PipelineResult.failed(
                        task,
                        "Quality Gate 재시도 후에도 BLOCKED: " + "; ".join(blocking_msgs_retry),
                        metrics=metrics,
                    )
                test_files = workspace.list_test_files()
                if not test_files:
                    metrics.failed_stage = "test_writing"
                    return PipelineResult.failed(
                        task, "TestWriter 품질 게이트 후 파일 미생성", metrics=metrics,
                    )
                _p({"type": "step", "step": "quality_gate_ok",
                    "message": f"품질 게이트 통과 — {', '.join(test_files)}"})
                logger.info("[%s] 품질 게이트 통과 후 테스트 파일: %s", task.id, test_files)
                qg_verdict = qg_verdict_retry

            if qg_verdict.verdict == "WARNING":
                warning_msgs = [
                    f"{rid}: {r.message}" for rid, r in qg_verdict.failed_rules("WARNING")
                ]
                metrics.quality_gate_reasons.extend(warning_msgs)
                logger.info(
                    "[%s] Quality Gate WARNING (진행) — %s",
                    task.id, "; ".join(warning_msgs),
                )
                _p({"type": "step", "step": "quality_gate_warning",
                    "message": f"품질 게이트 WARNING — {('; '.join(warning_msgs))[:120]}"})

            # TestWriter가 새로 생성한 테스트 파일만 DockerTestRunner에 전달
            _test_files_after = set(glob.glob(os.path.join(_ws_path, "**", "test_*.py"), recursive=True))
            _new_test_files = sorted(_test_files_after - _test_files_before)
            if _new_test_files:
                _new_test_relative = [os.path.relpath(f, _ws_path) for f in _new_test_files]
                logger.info("[%s] TestWriter 신규 테스트 파일: %s", task.id, _new_test_relative)
                _created_test_files = list(_new_test_relative)
            else:
                _new_test_relative = None  # 전체 실행 (기존 동작)
                # 신규 생성 파일 없음 — 기존 테스트만 존재 (edge case)
        else:
            # NO_TDD 모드: TestWriter / Quality Gate 건너뜀
            test_files = []
            _new_test_relative = None

        # ── 단계 사이 중단 체크 ───────────────────────────────────────────────
        if pause_ctrl is not None and pause_ctrl.is_stopped:
            raise _StopRequested("단계 사이 중단 요청 감지")

        # ── Step 2–3: 구현 → 테스트 → 리뷰 (리뷰 피드백 반영 재시도 포함) ────
        docker_result: RunResult | None = None
        review: ReviewResult | None = None
        reviewer_feedback: str = ""

        for review_attempt in range(self.max_review_retries + 1):
            # 리뷰 재시도 시 이전 테스트 에러 초기화 (새 구현 시도가 기준)
            if review_attempt > 0:
                task.last_error = ""
                task.retry_count = 0
                _p({"type": "step", "step": "review_retry",
                    "message": f"Reviewer 피드백 반영 — 재구현 시작 (리뷰 재시도 {review_attempt}/{self.max_review_retries})"})

            # ── Step 2: 구현 + 테스트 실행 ───────────────────────────────────
            docker_result = None
            for attempt in range(self.max_retries):
                if pause_ctrl is not None and pause_ctrl.is_stopped:
                    raise _StopRequested("구현 루프 중 중단 요청 감지")
                task.status = TaskStatus.IMPLEMENTING
                label = (
                    f"Implementer: 재구현 중… (Reviewer 피드백 반영, 시도 {attempt + 1}/{self.max_retries})"
                    if reviewer_feedback
                    else f"Implementer: 구현 중… (시도 {attempt + 1}/{self.max_retries})"
                )
                _p({"type": "step", "step": "implementing", "message": label})
                impl_scoped = self._run_implementer(
                    task, workspace, reviewer_feedback=reviewer_feedback,
                    on_progress=_agent_p, pause_ctrl=pause_ctrl,
                    enriched_desc=enriched_desc,
                )
                _accumulate_tokens(metrics, "implementer", impl_scoped)
                if not impl_scoped.succeeded:
                    if _is_write_loop(impl_scoped) and attempt < self.max_retries - 1:
                        # 탐색 루프 감지 — 에러 컨텍스트를 남기고 다음 retry로 넘긴다
                        task.last_error = impl_scoped.answer
                        task.retry_count = attempt + 1
                        logger.warning(
                            "[%s] Implementer WRITE_LOOP — retry %d/%d",
                            task.id, task.retry_count, self.max_retries,
                        )
                        _p({"type": "step", "step": "write_loop_retry",
                            "message": (
                                f"탐색 루프 감지 — 재시도 {task.retry_count}/{self.max_retries}"
                            )})
                        continue
                    prefix = "[MAX_ITER] " if _is_max_iter(impl_scoped) else ""
                    metrics.failed_stage = "implementing"
                    metrics.impl_retries = attempt
                    return PipelineResult.failed(task, f"{prefix}Implementer 실패: {impl_scoped.answer}", metrics=metrics)

                # ── Step 2.5: target_file 엄격 가드 ───────────────────────────
                # Implementer 가 "성공" 으로 끝났더라도 실제로 target_files 를
                # 채우지 않았을 수 있다 (빈 스켈레톤 그대로, 또는 삭제).
                # 후속 태스크가 이 파일 존재를 전제로 실행되므로 강제 체크한다.
                missing_targets = workspace.missing_or_empty_target_files()
                if missing_targets:
                    if attempt < self.max_retries - 1:
                        task.retry_count = attempt + 1
                        target_list = ", ".join(missing_targets)
                        task.last_error = (
                            f"[TARGET_MISSING] 다음 target_file 들이 누락되거나 "
                            f"빈 파일 상태입니다: {target_list}\n"
                            f"write_file 로 해당 경로에 실제 구현을 작성하세요. "
                            f"탐색(list/read) 은 최소화하고 즉시 쓰기 도구를 호출하세요."
                        )
                        logger.warning(
                            "[%s] target_file 미작성 — retry %d/%d: %s",
                            task.id, task.retry_count, self.max_retries, target_list,
                        )
                        _p({"type": "step", "step": "target_missing_retry",
                            "message": (
                                f"target_file 미작성 ({target_list}) — "
                                f"재시도 {task.retry_count}/{self.max_retries}"
                            )})
                        continue
                    # 마지막 시도였다면 즉시 실패
                    metrics.failed_stage = "implementing"
                    metrics.impl_retries = attempt
                    return PipelineResult.failed(
                        task,
                        f"[TARGET_MISSING] Implementer 가 target_files 를 작성하지 않았습니다: "
                        f"{', '.join(missing_targets)}",
                        metrics=metrics,
                    )

                if self._skip_test_writer:
                    # NO_TDD 모드: 테스트 파일 없으므로 DockerTest 건너뜀
                    docker_result = None
                    metrics.impl_retries = attempt
                    break

                task.status = TaskStatus.RUNNING_TESTS
                _p({"type": "step", "step": "docker_running", "message": "Docker 테스트 실행 중…"})
                docker_result = self.test_runner.run(
                    workspace.path, task.target_files, task.test_framework,
                    language=task.language, test_files=_new_test_relative,
                )
                logger.info(
                    "[%s] 테스트 실행 (시도 %d/%d): %s",
                    task.id, attempt + 1, self.max_retries, docker_result.summary,
                )

                if docker_result.passed:
                    task.retry_count = attempt
                    if attempt == 0 and review_attempt == 0:
                        metrics.test_red_to_green_first_try = True
                    metrics.impl_retries = attempt
                    _p({"type": "step", "step": "docker_pass",
                        "message": f"테스트 통과 — {docker_result.summary}"})
                    break

                # ── 수집 실패(재시도 무의미) 조기 분기 ─────────────────────────
                # [COLLECTION_ERROR] / [NO_TESTS_COLLECTED] 는 Implementer 재시도로
                # 해결되지 않는다. intervention.classify_failure 가 태그+test_stdout
                # 을 보고 적절한 상위 분기(TestWriter 재실행 등)를 선택하도록
                # test_result 도 함께 전달한다.
                if docker_result.failure_reason in ("[COLLECTION_ERROR]", "[NO_TESTS_COLLECTED]"):
                    metrics.failed_stage = "testing"
                    metrics.impl_retries = attempt
                    return PipelineResult.failed(
                        task,
                        f"{docker_result.failure_reason} {docker_result.summary}",
                        metrics=metrics,
                        test_result=docker_result,
                    )

                task.retry_count = attempt + 1
                task.last_error = docker_result.stdout
                logger.warning("[%s] 테스트 실패, 재시도 %d회", task.id, task.retry_count)
                _p({"type": "step", "step": "docker_fail",
                    "message": f"테스트 실패 — {docker_result.summary}"
                               + (f" (재시도 {task.retry_count}/{self.max_retries})"
                                  if task.retry_count < self.max_retries else "")})

                if attempt == self.max_retries - 1:
                    metrics.failed_stage = "testing"
                    metrics.impl_retries = attempt
                    _fr_prefix = (
                        f"{docker_result.failure_reason} "
                        if docker_result.failure_reason else ""
                    )
                    return PipelineResult.failed(
                        task,
                        f"{_fr_prefix}테스트가 {self.max_retries}회 모두 실패했습니다.\n"
                        f"마지막 오류:\n{docker_result.summary}",
                        metrics=metrics,
                        test_result=docker_result,
                    )

            # ── Step 3: 코드 리뷰 ─────────────────────────────────────────────
            if pause_ctrl is not None and pause_ctrl.is_stopped:
                raise _StopRequested("Docker 테스트 완료 후 중단 요청 감지")
            task.status = TaskStatus.REVIEWING
            _p({"type": "step", "step": "reviewing", "message": "Reviewer: 코드 검토 중…"})
            review_scoped = self._run_reviewer(task, workspace, docker_result, on_progress=_agent_p, pause_ctrl=pause_ctrl)
            _accumulate_tokens(metrics, "reviewer", review_scoped)
            # 종료 원인별 전처리:
            #   - MAX_ITER  → CHANGES_REQUESTED 로 교체 (정상적 판정 실패)
            #   - LLM_ERROR → 파싱 건너뛰고 ERROR verdict 로 직접 생성
            review: ReviewResult | None = None
            if review_scoped.loop_result and not review_scoped.loop_result.succeeded:
                from llm.base import StopReason as _SR
                if review_scoped.loop_result.stop_reason == _SR.MAX_ITER:
                    review_scoped = type(review_scoped)(
                        answer=f"VERDICT: CHANGES_REQUESTED\nSUMMARY: 리뷰어가 반복 한도({self.reviewer_max_iterations}회)를 초과했습니다.\nDETAILS:\n리뷰어가 파일 탐색 중 반복 한도를 초과하여 판정을 완료하지 못했습니다. 구현을 수동으로 확인하거나 재시도하세요.",
                        succeeded=False,
                        workspace_files=review_scoped.workspace_files,
                        loop_result=review_scoped.loop_result,
                    )
                elif review_scoped.loop_result.stop_reason == _SR.LLM_ERROR:
                    review = ReviewResult(
                        verdict="ERROR",
                        summary="Reviewer LLM 호출 실패",
                        details=(review_scoped.answer or "(응답 없음)")[:1000],
                        raw=review_scoped.answer or "",
                    )
            if review is None:
                review = _parse_review(review_scoped.answer)
            logger.info("[%s] 리뷰 결과: %s — %s", task.id, review.verdict, review.summary)

            # ── Reviewer 인프라 장애: 재시도 없이 즉시 실패 ─────────────────────
            if review.is_error:
                err_detail = review.details or review.summary or "(상세 없음)"
                logger.error(
                    "[%s] Reviewer 인프라 장애 — Implementer 재실행 없이 실패 처리\n  %s",
                    task.id, err_detail[:300],
                )
                _p({"type": "step", "step": "review_error",
                    "message": f"Reviewer 호출 실패 — {err_detail[:200]}"})
                metrics.review_retries = review_attempt
                metrics.failed_stage = "reviewing"
                return PipelineResult.failed(
                    task,
                    f"[REVIEWER_INFRA_ERROR] Reviewer 실행 실패: {err_detail[:500]}",
                    metrics=metrics,
                )

            if review.approved:
                _p({"type": "step", "step": "review_approved",
                    "message": f"Reviewer APPROVED — {review.summary}"})
                metrics.review_retries = review_attempt
                break

            _reviewer_fb = review.details or review.summary or "(Reviewer 피드백 없음)"
            _p({"type": "step", "step": "review_rejected",
                "message": f"Reviewer CHANGES_REQUESTED — {_reviewer_fb}"})

            if review_attempt < self.max_review_retries:
                # CHANGES_REQUESTED → 피드백을 Implementer에 전달하고 재시도
                reviewer_feedback = _reviewer_fb
                logger.warning(
                    "[%s] Reviewer CHANGES_REQUESTED (리뷰 시도 %d/%d) — 피드백 반영 재구현\n  %s",
                    task.id, review_attempt + 1, self.max_review_retries,
                    reviewer_feedback[:200],
                )
            # 마지막 review_attempt이면 루프 자연 종료 → 아래 succeeded=False 처리

        metrics.review_retries = review_attempt if not review.approved else metrics.review_retries
        if not review.approved:
            metrics.failed_stage = "reviewing"

        task.status = TaskStatus.COMMITTING
        failure_reason = (
            "" if review.approved
            else f"Reviewer CHANGES_REQUESTED: {review.details or review.summary or '(피드백 없음)'}"
        )
        return PipelineResult(
            task=task,
            succeeded=review.approved,
            failure_reason=failure_reason,
            test_result=docker_result,
            review=review,
            test_files=_created_test_files,
            impl_files=workspace.list_src_files(),
            metrics=metrics,
        )

    # ── 개별 에이전트 실행 ────────────────────────────────────────────────────

    def _run_test_writer(
        self,
        task: Task,
        workspace: WorkspaceManager,
        retry: bool = False,
        static_issues: list[str] | None = None,
        missing_criteria: list[str] | None = None,
        on_progress=None,
        pause_ctrl=None,
        enriched_desc: str | None = None,
        prior_failure_reason: str | None = None,
        prior_explored_paths: list[str] | None = None,
    ) -> ScopedResult:
        stop_check = (lambda: pause_ctrl.is_stopped) if pause_ctrl else None
        _framework = LANGUAGE_TEST_FRAMEWORK_MAP.get(task.language, task.test_framework)
        loop = ScopedReactLoop(
            llm=self._llm_for_role(ROLE_TEST_WRITER, self.agent_llm, task=task),
            role=TEST_WRITER.render(task.language, _framework),
            workspace_dir=workspace.path,
            max_iterations=self.max_iterations,
            on_progress=on_progress,
            write_deadline=self.test_writer_write_deadline,
            stop_check=stop_check,
            role_compaction_tuning_enabled=self.role_compaction_tuning_enabled,
            role_compaction_tuning_preset=self.role_compaction_tuning_preset,
            role_compaction_tuning_overrides=self.role_compaction_tuning_overrides,
        )
        prompt = _build_test_writer_prompt(
            task, workspace,
            retry=retry,
            static_issues=static_issues,
            missing_criteria=missing_criteria,
            description_override=enriched_desc,
            prior_failure_reason=prior_failure_reason,
            prior_explored_paths=prior_explored_paths,
        )
        logger.debug(
            "[%s] TestWriter 시작 (retry=%s, static_issues=%d, missing=%d, prior_reason=%r)",
            task.id, retry,
            len(static_issues or []),
            len(missing_criteria or []),
            (prior_failure_reason or "")[:60],
        )
        return loop.run(prompt)

    def _run_implementer(
        self,
        task: Task,
        workspace: WorkspaceManager,
        reviewer_feedback: str = "",
        on_progress=None,
        pause_ctrl=None,
        enriched_desc: str | None = None,
    ) -> ScopedResult:
        stop_check = (lambda: pause_ctrl.is_stopped) if pause_ctrl else None
        _framework = LANGUAGE_TEST_FRAMEWORK_MAP.get(task.language, task.test_framework)
        loop = ScopedReactLoop(
            llm=self._llm_for_role(ROLE_IMPLEMENTER, self.implementer_llm, task=task),
            role=IMPLEMENTER.render(task.language, _framework),
            workspace_dir=workspace.path,
            max_iterations=self.max_iterations,
            on_progress=on_progress,
            write_deadline=self.implementer_write_deadline,
            stop_check=stop_check,
            role_compaction_tuning_enabled=self.role_compaction_tuning_enabled,
            role_compaction_tuning_preset=self.role_compaction_tuning_preset,
            role_compaction_tuning_overrides=self.role_compaction_tuning_overrides,
        )
        prompt = _build_implementer_prompt(task, workspace, reviewer_feedback=reviewer_feedback,
                                           description_override=enriched_desc)
        logger.debug(
            "[%s] Implementer 시작 (retry=%d, reviewer_feedback=%s)",
            task.id, task.retry_count, bool(reviewer_feedback),
        )
        return loop.run(prompt)

    def _run_reviewer(
        self,
        task: Task,
        workspace: WorkspaceManager,
        docker_result: RunResult | None,
        on_progress=None,
        pause_ctrl=None,
    ) -> ScopedResult:
        stop_check = (lambda: pause_ctrl.is_stopped) if pause_ctrl else None
        _framework = LANGUAGE_TEST_FRAMEWORK_MAP.get(task.language, task.test_framework)
        loop = ScopedReactLoop(
            llm=self._llm_for_role(ROLE_REVIEWER, self.agent_llm, task=task),
            role=REVIEWER.render(task.language, _framework),
            workspace_dir=workspace.path,
            max_iterations=self.reviewer_max_iterations,
            on_progress=on_progress,
            stop_check=stop_check,
            role_compaction_tuning_enabled=self.role_compaction_tuning_enabled,
            role_compaction_tuning_preset=self.role_compaction_tuning_preset,
            role_compaction_tuning_overrides=self.role_compaction_tuning_overrides,
        )
        prompt = _build_reviewer_prompt(task, workspace, docker_result)
        logger.debug("[%s] Reviewer 시작", task.id)
        return loop.run(prompt)


# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────


def _context_hint(workspace: WorkspaceManager) -> str:
    """context/ 디렉토리가 있으면 참조 안내 문자열을 반환한다."""
    context_dir = workspace.path / "context"
    if not context_dir.exists():
        return ""
    docs = sorted(f.name for f in context_dir.iterdir() if f.is_file())
    if not docs:
        return ""
    doc_list = ", ".join(f"`context/{d}`" for d in docs)
    return f"\n상세 스펙·아키텍처 문서: {doc_list} — 구현 전에 참조하세요.\n"


def _python_import_path(f: str) -> str:
    """target_file 경로에서 Python import 경로를 생성한다.

    src/auth.py        → auth          (workspace src_dir 가 PYTHONPATH 이므로 'src/' 제거)
    src/models/user.py → models.user
    user.py            → user
    """
    p = Path(strip_src_prefix(f))
    parts = [part.replace("-", "_") for part in p.with_suffix("").parts]
    return ".".join(parts)


def _node_require_path(f: str) -> str:
    """target_file 경로에서 Node.js require 경로를 생성한다.

    src/foo.js         → ../src/foo
    models/user.js     → ../src/models/user
    user.js            → ../src/user
    """
    return f"../src/{Path(strip_src_prefix(f)).with_suffix('')}"


def _test_lang_rules(task: Task) -> str:
    """
    target_files 기반으로 TestWriter에 주입할 언어·import·출력 규약 섹션을 생성한다.
    """
    runtime = _detect_runtime(task.target_files)
    impl_files = [
        f for f in task.target_files
        if not Path(f).name.startswith("test_") and not Path(f).stem.endswith(".test")
    ]

    if runtime == "node":
        import_examples = "\n".join(
            f"  const ... = require('{_node_require_path(f)}');  // {f}"
            for f in impl_files
        )
        return f"""## 테스트 작성 규칙

**언어**: JavaScript (Node.js) — target_files에 .js 파일이 있으므로

**테스트 파일 위치**: `tests/` 디렉토리에 `test_*.js` 이름으로 생성

**import 경로** — target_files 파일명에서 직접 유도, 절대 다른 경로를 만들지 마세요:
{import_examples}

**출력 규약** — 테스트 파일 마지막에 반드시 아래 형식으로 출력하고 process.exit() 호출:
```javascript
// 성공 시
console.log(`OK: ${{passed}} passed, 0 failed`);
process.exit(0);

// 실패 시
console.log(`FAIL: ${{passed}} passed, ${{failed}} failed`);
failures.forEach(f => console.log(`- ${{f}}`));
process.exit(1);
```

**구조 패턴**:
```javascript
const failures = [];
let passed = 0;

// 각 테스트
try {{
  // ... assertion
  passed++;
}} catch (e) {{
  failures.push(`test_name: ${{e.message}}`);
}}
```
"""
    else:
        import_examples = "\n".join(
            f"  from {_python_import_path(f)} import ...  # src/{strip_src_prefix(f)}"
            for f in impl_files
        )
        return f"""## 테스트 작성 규칙

**언어**: Python — target_files에 .py 파일이 있으므로 (또는 HTML/CSS여서 Python으로 검증)

**테스트 파일 위치**: `tests/` 디렉토리에 `test_*.py` 이름으로 생성

**import 경로** — target_files 파일명에서 직접 유도, 절대 다른 경로를 만들지 마세요:
  (PYTHONPATH에 /workspace/src 가 포함되어 있어 src/ 모듈을 직접 import할 수 있습니다)
{import_examples}

**출력 규약** — 테스트 파일 마지막에 반드시 아래 형식으로 출력하고 sys.exit() 호출:
```python
import sys
# 성공 시
print(f"OK: {{passed}} passed, 0 failed")
sys.exit(0)

# 실패 시
print(f"FAIL: {{passed}} passed, {{failed}} failed")
for f in failures:
    print(f"- {{f}}")
sys.exit(1)
```

**구조 패턴**:
```python
import sys
failures = []
passed = 0

# 각 테스트
try:
    # ... assertion
    passed += 1
except Exception as e:
    failures.append(f"test_name: {{e}}")
```
"""


def _dedupe_explored_paths(paths: list[str], limit: int = 20) -> list[str]:
    """탐색 경로 목록을 삽입 순서 기준으로 중복 제거하고 상위 limit 개만 반환한다."""
    seen: set[str] = set()
    ordered: list[str] = []
    for p in paths:
        if not p or p in seen:
            continue
        seen.add(p)
        ordered.append(p)
        if len(ordered) >= limit:
            break
    return ordered


def _build_test_writer_prompt(
    task: Task,
    workspace: WorkspaceManager,
    retry: bool = False,
    static_issues: list[str] | None = None,
    missing_criteria: list[str] | None = None,
    description_override: str | None = None,
    prior_failure_reason: str | None = None,
    prior_explored_paths: list[str] | None = None,
) -> str:
    structure_hint = ""
    if (workspace.path / "PROJECT_STRUCTURE.md").exists():
        structure_hint = "\n`PROJECT_STRUCTURE.md` 로 전체 코드베이스 구조를 먼저 파악하세요.\n"

    feedback_blocks: list[str] = []

    if retry:
        feedback_blocks.append(
            "⚠️ **이전 시도에서 tests/ 에 파일을 생성하지 않았습니다.**\n"
            "반드시 `write_file` 도구를 호출하여 `tests/` 디렉토리에 테스트 파일을 작성하세요.\n"
            "설명만 하고 도구를 호출하지 않으면 실패로 처리됩니다."
        )

    if prior_failure_reason:
        feedback_blocks.append(
            "## 직전 시도 실패 이유\n"
            f"{prior_failure_reason.strip()}\n"
            "같은 사유로 다시 실패하지 않도록 곧바로 write_file 을 호출해 테스트 본문을 채우세요."
        )

    if prior_explored_paths:
        dedup = _dedupe_explored_paths(prior_explored_paths, limit=20)
        if dedup:
            path_list = "\n".join(f"  - {p}" for p in dedup)
            feedback_blocks.append(
                "## 직전 시도에서 탐색한 파일\n"
                "다음 파일들은 직전 시도에서 이미 열어봤습니다. "
                "같은 파일을 다시 열지 말고 바로 write_file 을 호출하세요:\n"
                f"{path_list}"
            )

    if static_issues:
        issue_list = "\n".join(f"  - {i}" for i in static_issues)
        feedback_blocks.append(
            f"⚠️ **정적 검증 실패 — 아래 문제를 반드시 수정하세요:**\n{issue_list}\n"
            "각 테스트 함수에 실제 assertion (`assert`, `assertEqual`, `expect(...)` 등)이 있어야 합니다."
        )

    if missing_criteria:
        crit_list = "\n".join(f"  - {c}" for c in missing_criteria)
        feedback_blocks.append(
            f"⚠️ **수락 기준 미커버 — 아래 항목을 커버하는 테스트를 추가하세요:**\n{crit_list}"
        )

    feedback_section = ""
    if feedback_blocks:
        feedback_section = "\n## 이전 시도 피드백\n\n" + "\n\n".join(feedback_blocks) + "\n"

    lang_rules = _test_lang_rules(task)

    criteria_text = task.acceptance_criteria_text()
    if criteria_text.strip():
        criteria_section = criteria_text
    else:
        criteria_section = (
            "(수락 기준이 별도 명시되지 않았습니다. "
            "위 태스크 설명에서 무엇을 구현해야 하는지 직접 추론하여 테스트를 작성하세요. "
            "ask_user를 호출하지 마세요.)"
        )

    desc = description_override if description_override is not None else task.description

    return f"""## 태스크

**{task.title}**

{desc}

## 수락 기준

{criteria_section}

## 워크스페이스 경로

`{workspace.path}`
{structure_hint}{_context_hint(workspace)}{feedback_section}
{lang_rules}
`src/` 에 있는 기존 코드를 먼저 확인하고,
`tests/` 에 위 규칙대로 테스트를 작성하세요.
구현이 없으므로 테스트는 실행 시 실패해야 합니다 (Red 단계).
"""


def _format_target_files(target_files: list[str]) -> str:
    """target_files 목록을 워크스페이스 루트 기준 경로로 포맷한다.

    workspace 의 ``src_dir`` 자체가 'src/' 코드 루트이므로, target_file 의 선행
    'src/' 한 단계는 ``src_dir`` 가 흡수한다. 따라서 프롬프트에는
    ``src/`` 를 한 번만 붙여 워크스페이스 루트 기준 실제 파일 위치를 노출한다.

      'src/foo.py'           → 'src/foo.py'
      'models/user.py'       → 'src/models/user.py'
      'app/src/main/foo.kt'  → 'src/app/src/main/foo.kt'
    """
    if not target_files:
        return "(target_files 없음 — tests/ 를 참고해 적절한 위치에 생성하세요)"
    return "\n".join(f"- `src/{strip_src_prefix(f)}`" for f in target_files)


def _build_implementer_prompt(
    task: Task,
    workspace: WorkspaceManager,
    reviewer_feedback: str = "",
    description_override: str | None = None,
) -> str:
    structure_hint = ""
    if (workspace.path / "PROJECT_STRUCTURE.md").exists():
        structure_hint = "\n`PROJECT_STRUCTURE.md` 로 전체 코드베이스 구조를 먼저 파악하고, 재사용 가능한 모듈이 있는지 확인하세요.\n"

    desc = description_override if description_override is not None else task.description

    design_notes_path = workspace.path / "context" / "test_design_notes.md"
    design_notes_section = ""
    if design_notes_path.exists():
        content = design_notes_path.read_text(encoding="utf-8")
        if len(content) > 4000:
            content = content[:4000] + "\n...(truncated)"
        design_notes_section = f"""
## 테스트 설계 노트 (TestWriter 작성)

{content}

위 노트를 먼저 읽고 의도를 파악한 뒤 구현하세요.
테스트 파일(`tests/`)은 노트만으로 부족할 때 직접 `read_file` 로 확인하세요.
"""

    base = f"""## 태스크

**{task.title}**

{desc}

## 수락 기준

{task.acceptance_criteria_text()}

## 워크스페이스 경로

`{workspace.path}`
{structure_hint}{_context_hint(workspace)}
## 생성할 파일 목록

다음 경로에 구현 파일을 작성하세요 (workspace `src/` 기준):

{_format_target_files(task.target_files)}

- 위 경로들은 **이미 빈 파일로 존재**합니다 (신규 생성 태스크의 경우). 파일 존재 여부를 확인하기 위해 `list_directory`/`read_file` 을 반복 호출하지 말고, **곧바로 `write_file` 로 내용을 작성**하세요.
- 기존 내용이 있는 파일을 수정하는 태스크라면 `read_file` 로 한 번만 확인 후 `edit_file` 로 수정하세요.
{design_notes_section}
`tests/` 에 있는 테스트를 먼저 읽고,
`src/` 에 테스트를 **모두** 통과하는 구현을 작성하세요.
"""
    if reviewer_feedback:
        base += f"""
## Reviewer 피드백 (이전 구현에서 지적된 사항)

```
{reviewer_feedback[:2000]}
```

위 피드백을 반드시 반영하세요. 테스트를 통과하는 것뿐 아니라 코드 품질 문제도 함께 수정해야 합니다.
"""

    if task.last_error:
        truncated = task.last_error[:2000]
        if len(task.last_error) > 2000:
            truncated += "\n... (이하 생략)"
        base += f"""
## 이전 시도 실패 로그 (시도 {task.retry_count}회차)

```
{truncated}
```

위 오류를 분석해서 원인을 파악한 뒤 수정하세요.
같은 방식으로 재시도하지 마세요.
"""
    return base


def _build_reviewer_prompt(
    task: Task, workspace: WorkspaceManager, docker_result: RunResult | None
) -> str:
    test_summary = docker_result.summary if docker_result is not None else "(NO_TDD 모드 — 테스트 실행 없음)"
    return f"""## 검토 요청

**{task.title}**

## 수락 기준

{task.acceptance_criteria_text()}

## 테스트 실행 결과

{test_summary}

## 워크스페이스 경로

`{workspace.path}`

## 생성된 파일 (target_files 기준)

{_format_target_files(task.target_files)}

`src/` 와 `tests/` 를 읽고 코드를 검토한 뒤,
지시받은 형식대로 VERDICT 를 반환하세요.
"""


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────


# ── 테스트 품질 게이트 ────────────────────────────────────────────────────────


def _validate_tests_static(test_files: list[str], workspace: WorkspaceManager) -> list[str]:
    """
    테스트 파일을 정적 분석해 품질 문제를 반환한다. 빈 리스트 = 이상 없음.

    Python : ast 모듈로 test_ 함수에 assertion 유무 확인 + 플레이스홀더 감지
    JS/TS  : 정규식으로 expect() / assert() 유무 확인
    """
    issues: list[str] = []
    for fname in test_files:
        path = workspace.path / fname
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        suffix = path.suffix.lower()
        if suffix == ".py":
            issues.extend(_validate_python_test(src, fname))
        elif suffix in (".js", ".ts", ".jsx", ".tsx"):
            issues.extend(_validate_js_test(src, fname))
    return issues


def _collect_direct_call_names(stmts: list[ast.stmt]) -> set[str]:
    """statement 리스트에서 직접 호출된 이름(Name / dotted Attribute) 집합.

    의도:
      - `obj = Foo(...)`        → {"Foo"}
      - `Foo(...).method(...)`  → {"Foo", "Foo.method"} 중 Name 인 "Foo" 수집
      - `m.bar()`               → {"m.bar"}  (Attribute 체인 전체 문자열)
      - 내장 호출(print 등)도 포함되지만 `str`, `int`, `len`, `print`,
        `isinstance`, `repr` 같이 fallback 과 무관한 내장은 제외한다.

    try 본문과 except 본문에서 **같은 이름** 이 겹치면 "재호출 fallback" 으로
    본다. Name 과 Attribute 를 문자열로 정규화해 비교한다.
    """
    _BUILTIN_SAFE = {
        "str", "int", "float", "bool", "len", "repr", "print", "isinstance",
        "list", "tuple", "dict", "set",
        # 오류 메시지 검증에 흔히 쓰임 — fallback 으로 오판하면 안 됨
        "pytest", "re", "assert_",
    }

    def _name_of(expr) -> str | None:
        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            base = _name_of(expr.value)
            if base is None:
                return None
            return f"{base}.{expr.attr}"
        return None

    collected: set[str] = set()
    for stmt in stmts:
        for sub in ast.walk(stmt):
            if not isinstance(sub, ast.Call):
                continue
            name = _name_of(sub.func)
            if not name:
                continue
            # 내장 안전 호출 스킵
            root = name.split(".", 1)[0]
            if root in _BUILTIN_SAFE:
                continue
            collected.add(name)
    return collected


def _handler_catches(handler: ast.ExceptHandler, exc_name: str) -> bool:
    exc_type = handler.type
    if isinstance(exc_type, ast.Name) and exc_type.id == exc_name:
        return True
    if isinstance(exc_type, ast.Tuple) and any(
        isinstance(elt, ast.Name) and elt.id == exc_name for elt in exc_type.elts
    ):
        return True
    return False


def _handler_is_only_reraise(handler: ast.ExceptHandler) -> bool:
    body = handler.body
    return (
        len(body) == 1
        and isinstance(body[0], ast.Raise)
        and body[0].exc is None
    )


def _expr_uses_hasattr(expr: ast.expr) -> bool:
    """표현식 트리 안 어딘가에 hasattr(...) 호출이 있으면 True."""
    for sub in ast.walk(expr):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "hasattr"
        ):
            return True
    return False


def _detect_task008_antipatterns(tree: ast.Module, fname: str) -> list[str]:
    """task-008 에서 관찰된 방어적 패턴을 AST 수준에서 감지한다.

    감지 대상 (모두 test_writer.md "금지 패턴" 섹션과 1:1 매칭):
      A. 동적 import + `pytest.mark.skipif(<Name> is None, ...)`
         — `try: import ... except ImportError: Name = None` 모듈 레벨 블록
           또는 `skipif(<Name> is None, ...)` 데코레이터
      B. test_ 함수 내 `except TypeError:` 블록 (생성자 시그니처 추측 패턴)
      C. `hasattr(...)` 호출 (스펙 메서드의 런타임 존재 확인 우회)
    """
    issues: list[str] = []

    # ── A1: try: import ... except ImportError: X = None 블록 ────────────
    import_failure_names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Try):
            continue
        has_import_in_try = any(
            isinstance(c, (ast.Import, ast.ImportFrom)) for c in node.body
        )
        if not has_import_in_try:
            continue
        for handler in node.handlers:
            exc_type = handler.type
            is_import_error = (
                isinstance(exc_type, ast.Name) and exc_type.id == "ImportError"
            ) or (
                isinstance(exc_type, ast.Tuple)
                and any(
                    isinstance(elt, ast.Name) and elt.id == "ImportError"
                    for elt in exc_type.elts
                )
            )
            if not is_import_error:
                continue
            # handler 본문에서 Name = None 할당된 식별자 수집
            for stmt in handler.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant) and stmt.value.value is None:
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name):
                            import_failure_names.add(tgt.id)
    if import_failure_names:
        issues.append(
            f"{fname}: 동적 import + `= None` 패턴 — "
            f"`try: import X / except ImportError: X = None` 은 금지 (대상: "
            f"{sorted(import_failure_names)}). "
            "대신 일반 import 를 쓰고 모듈이 없으면 수집 단계에서 실패하도록 두세요. "
            "시그니처가 불확실하면 `context/dependency_artifacts.md` 조회 또는 `ask_user` 사용."
        )

    # ── A2: @pytest.mark.skipif(<Name> is None, ...) 데코레이터 ───────────
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            is_skipif = (
                isinstance(func, ast.Attribute) and func.attr == "skipif"
            ) or (
                isinstance(func, ast.Name) and func.id == "skipif"
            )
            if not is_skipif or not dec.args:
                continue
            cond = dec.args[0]
            # pattern: `X is None` 또는 `X is None or ...`
            def _is_none_check(expr) -> bool:
                return (
                    isinstance(expr, ast.Compare)
                    and len(expr.ops) == 1
                    and isinstance(expr.ops[0], ast.Is)
                    and isinstance(expr.comparators[0], ast.Constant)
                    and expr.comparators[0].value is None
                )
            flagged = False
            if _is_none_check(cond):
                flagged = True
            elif isinstance(cond, ast.BoolOp):
                flagged = any(_is_none_check(v) for v in cond.values)
            if flagged:
                issues.append(
                    f"{fname}::{node.name} — `skipif(<X> is None, ...)` 패턴 금지. "
                    "클래스/함수 존재 여부를 런타임에 확인하지 말고 "
                    "명시적 `pytest.skip(reason=...)` 또는 `ask_user` 를 사용하세요."
                )

    # ── B: test_ 함수 내 `except TypeError:` — 단, **생성자 fallback 형태**만
    #   flag 한다. 구체적으로 try 본문에서 호출된 Name 이 except 본문에서 동일한
    #   Name 으로 재호출(시그니처 추측) 되는 경우만 차단.
    #   → `except TypeError as e: assert 'x' in str(e)` 처럼 예외 메시지 검증을
    #     하는 정당한 패턴은 통과시킨다.
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test")):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Try):
                continue
            try_call_names = _collect_direct_call_names(sub.body)
            for handler in sub.handlers:
                if not _handler_catches(handler, "TypeError"):
                    continue
                if _handler_is_only_reraise(handler):
                    continue
                except_call_names = _collect_direct_call_names(handler.body)
                overlap = try_call_names & except_call_names
                if not overlap:
                    # 정당한 예외 메시지 검증/보조 정리 등 → 통과
                    continue
                issues.append(
                    f"{fname}::{node.name} — `try: {sorted(overlap)[0]}(...) / "
                    "except TypeError: {동일 호출} (...)` fallback 패턴 금지. "
                    "생성자/함수 시그니처를 try/except 로 추측하지 말고 "
                    "`context/dependency_artifacts.md` 에서 확인하거나 `ask_user` 를 사용하세요. "
                    "TypeError 자체를 검증하려면 `pytest.raises(TypeError)` 또는 "
                    "`except TypeError as e: assert ... in str(e)` 를 사용하세요."
                )
                break  # 함수당 1회만 보고

    # ── C: test_ 함수 내 `if hasattr(...)` **게이트 패턴** 만 금지 ────────
    #   `assert hasattr(obj, "version")` 같은 속성 계약 검증은 허용한다.
    #   핵심은 "스펙에 있어야 할 메서드의 존재를 런타임에 확인해 호출을 우회"
    #   하는 패턴이다. 따라서 다음 두 형태만 flag:
    #     (1) `if hasattr(...):`  — ast.If 의 test 표현에 hasattr 이 포함
    #     (2) `x if hasattr(...) else y`  — 삼항식 test 에 포함
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test")):
            continue
        flagged = False
        for sub in ast.walk(node):
            # (1) if hasattr(...):
            if isinstance(sub, ast.If) and _expr_uses_hasattr(sub.test):
                flagged = True
                break
            # (2) <a> if hasattr(...) else <b>
            if isinstance(sub, ast.IfExp) and _expr_uses_hasattr(sub.test):
                flagged = True
                break
        if flagged:
            issues.append(
                f"{fname}::{node.name} — `if hasattr(...)` 게이트로 호출을 우회하는 "
                "패턴 금지. 스펙에 명시된 메서드/속성은 그대로 호출하세요. "
                "불확실하면 테스트 대상이 아니므로 `pytest.skip(reason=...)` 로 명시 skip. "
                "(속성 존재 자체를 검증하려는 의도라면 `assert hasattr(obj, '...')` 는 허용됩니다.)"
            )

    return issues


def _validate_python_test(src: str, fname: str) -> list[str]:
    issues: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"{fname}: 문법 오류 — {e}"]

    # ── 금지 패턴(task-008 회귀 가드): 동적 import + skipif / try-except TypeError /
    #    hasattr 우회. 프롬프트로도 금지하고 있지만 여기서 정적으로 한 번 더 차단한다.
    issues.extend(_detect_task008_antipatterns(tree, fname))

    # ── 플레이스홀더 감지: src/ import 없음 ───────────────────────────────
    # 커스텀 Python 테스트(pytest 미사용)는 test_ 함수 없이 모듈 레벨에서 동작한다.
    # src.* 또는 workspace 코드를 전혀 import하지 않으면 플레이스홀더로 간주한다.
    # 단, 파일시스템/환경 검증 테스트(os, shutil, pathlib, importlib)는 예외다.
    _FILESYSTEM_MODULES = {"os", "shutil", "pathlib", "importlib", "subprocess"}
    has_src_import = False
    has_filesystem_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module.startswith("src.")
                or node.module.startswith("src")
                and node.level == 0
            ):
                has_src_import = True
                break
            if node.module and node.module.split(".")[0] in _FILESYSTEM_MODULES:
                has_filesystem_import = True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src"):
                    has_src_import = True
                    break
                if alias.name.split(".")[0] in _FILESYSTEM_MODULES:
                    has_filesystem_import = True
        if has_src_import:
            break

    test_funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test")
    ]
    # test_ 함수도 없고 src import도 없고 파일시스템 import도 없으면 → 플레이스홀더
    if not test_funcs and not has_src_import and not has_filesystem_import:
        issues.append(
            f"{fname}: src/ import 없음 — 태스크와 무관한 플레이스홀더 테스트로 의심됨"
        )
        return issues

    # ── 플레이스홀더 감지: assert False / assert 0 패턴 (함수별) ──────────
    # TDD Red 단계를 오해하고 `assert False`만 넣는 에이전트 패턴을 차단한다.
    # 파일 전체가 아닌 개별 test_ 함수 기준으로 검사 — 다른 real test가 있어도 잡힌다.
    def _is_placeholder_assert(node: ast.Assert) -> bool:
        t = node.test
        if not isinstance(t, ast.Constant):
            return False
        return (not t.value) or (t.value is True)

    # 모듈 레벨 assert 검사 (커스텀 테스트 형식)
    module_placeholder = 0
    module_real = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        # test_ 함수 안에 있는 건 함수별 검사에서 처리
        in_test_func = any(
            isinstance(parent, ast.FunctionDef) and parent.name.startswith("test")
            for parent in ast.walk(tree)
            if node in ast.walk(parent)
        )
        if not in_test_func:
            if _is_placeholder_assert(node):
                module_placeholder += 1
            else:
                module_real += 1
    if module_placeholder > 0 and module_real == 0 and not test_funcs:
        issues.append(
            f"{fname}: assert False/True만 사용 — 실제 검증 로직 없는 플레이스홀더 테스트. "
            f"Red 단계는 '아직 존재하지 않는 기능을 호출하여 실패하는 테스트'여야 합니다."
        )
        return issues

    # ── pytest 스타일: test_ 함수별 assertion 확인 ────────────────────────
    _ASSERT_ATTRS = {
        "assertEqual", "assertNotEqual", "assertTrue", "assertFalse",
        "assertIs", "assertIsNot", "assertIsNone", "assertIsNotNone",
        "assertIn", "assertNotIn", "assertRaises", "assertRaisesRegex",
        "assertGreater", "assertGreaterEqual", "assertLess", "assertLessEqual",
        "assertAlmostEqual", "assertRegex", "assertCountEqual",
        "assert_called", "assert_called_once", "assert_called_with",
        "assert_called_once_with", "assert_any_call", "assert_not_called",
    }

    for node in test_funcs:
        has_real_assertion = False
        only_placeholder = True
        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                if _is_placeholder_assert(child):
                    # assert False/True — placeholder 후보
                    pass
                else:
                    has_real_assertion = True
                    only_placeholder = False
                    break
            elif isinstance(child, ast.Call):
                func = child.func
                is_assert_call = (
                    (isinstance(func, ast.Name) and func.id in _ASSERT_ATTRS)
                    or (isinstance(func, ast.Attribute) and func.attr in _ASSERT_ATTRS)
                    or (
                        isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "pytest"
                    )
                )
                if is_assert_call:
                    has_real_assertion = True
                    only_placeholder = False
                    break

        # assert 자체가 없으면 빈 테스트
        has_any_assert = any(isinstance(c, ast.Assert) for c in ast.walk(node))
        if not has_any_assert and not has_real_assertion:
            issues.append(f"{fname}::{node.name} — assertion 없음 (빈 테스트 또는 pass만 있음)")
        elif has_any_assert and only_placeholder:
            issues.append(
                f"{fname}::{node.name} — assert False/True만 사용 — "
                "실제 검증 로직 없는 플레이스홀더. "
                "아직 존재하지 않는 기능을 import하여 실패하는 테스트를 작성하세요."
            )
    return issues


def _validate_js_test(src: str, fname: str) -> list[str]:
    issues: list[str] = []
    # test('name', () => { ... }) 또는 it('name', async () => { ... })
    for m in re.finditer(
        r'(?:test|it)\s*\(\s*["\'](.+?)["\']\s*,\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{',
        src,
    ):
        name = m.group(1)
        # 본문 추출: 여는 { 이후 중첩 괄호를 따라 닫는 } 찾기
        start = m.end() - 1  # points at '{'
        depth = 0
        body = ""
        for i in range(start, len(src)):
            ch = src[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    body = src[start + 1:i]
                    break
        has_assertion = bool(
            re.search(r"expect\s*\(|assert\s*\(|should\.|\.toBe|\.toEqual|\.toThrow|\.toHaveBeenCalled", body)
        )
        if not has_assertion:
            issues.append(f"{fname}::'{name}' — assertion 없음")
    return issues


def _check_criteria_coverage(
    task: Task,
    test_files: list[str],
    workspace: WorkspaceManager,
    llm,
) -> list[str]:
    """
    수락 기준 각 항목이 테스트에서 커버되는지 경량 LLM 으로 확인한다.
    미커버 항목 목록을 반환한다. 빈 리스트 = 모두 커버됨.
    LLM 호출 실패 시 [] 를 반환해 파이프라인을 차단하지 않는다.
    """
    test_parts: list[str] = []
    for fname in test_files:
        path = workspace.path / fname
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace")[:3000]
            test_parts.append(f"### {fname}\n```\n{content}\n```")
    if not test_parts:
        return []

    prompt = (
        "다음 수락 기준 각 항목이 아래 테스트 코드에서 커버되는지 확인하세요.\n\n"
        f"## 수락 기준\n\n{task.acceptance_criteria_text()}\n\n"
        f"## 테스트 코드\n\n{''.join(test_parts)}\n\n"
        "## 지시\n\n"
        "각 수락 기준 항목을 검토한 뒤, **커버되지 않은 항목만** 다음 형식으로 출력하세요:\n"
        "MISSING: <항목 설명>\n\n"
        "모든 항목이 커버되면: MISSING: 없음\n"
        "불확실한 경우 커버된 것으로 간주하세요."
    )
    try:
        response = llm.chat([Message(role="user", content=prompt)])
        if isinstance(response.content, str):
            raw = response.content
        else:
            raw = " ".join(
                b.get("text", "") for b in response.content if b.get("type") == "text"
            )
    except Exception:
        logger.warning("[pipeline] criteria coverage 확인 실패 — 건너뜀")
        return []

    missing: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("MISSING:"):
            item = stripped.split(":", 1)[1].strip()
            if item and item.lower() not in ("없음", "none", "없음.", "n/a"):
                missing.append(item)
    return missing


def _is_max_iter(scoped: ScopedResult) -> bool:
    """ScopedResult 가 MAX_ITER 로 종료되었는지 확인한다."""
    return (
        scoped.loop_result is not None
        and scoped.loop_result.stop_reason == StopReason.MAX_ITER
    )


def _is_write_loop(scoped: ScopedResult) -> bool:
    """ScopedResult 가 WRITE_LOOP (탐색만 하고 write 미호출) 로 종료되었는지 확인한다."""
    return (
        scoped.loop_result is not None
        and scoped.loop_result.stop_reason == StopReason.WRITE_LOOP
    )


# ── TestWriter 종료 가드 ──────────────────────────────────────────────────────
#
# TestWriter 가 "succeeded=True" 로 끝났어도 workspace 에 실제로 유효한 테스트가
# 남아있지 않은 패턴을 차단한다. 실패 사유는 이후 재시도 프롬프트에 그대로 주입되어
# 동일 실패의 반복을 줄인다.

_TEST_FILE_GLOBS: list[str] = [
    "test_*.py", "*_test.py",
    "test_*.kt", "*Test.kt",
    "*_test.go",
    "test_*.js",  "*.test.js",  "*.spec.js",
    "test_*.ts",  "*.test.ts",  "*.spec.ts",
    # React 계열 — 스켈레톤은 .jsx/.tsx 로 선주입되므로 가드도 이를 수집해야 한다.
    # (회귀 가드: 누락 시 React 태스크가 정상 스켈레톤에도 [TEST_MISSING] 오판.)
    "test_*.jsx", "*.test.jsx", "*.spec.jsx",
    "test_*.tsx", "*.test.tsx", "*.spec.tsx",
]


def _collect_test_files(tests_dir: Path) -> list[Path]:
    """tests/ 디렉토리에서 언어별 테스트 파일을 수집한다 (중복 제거, 정렬)."""
    if not tests_dir.exists():
        return []
    seen: dict[str, Path] = {}
    for pattern in _TEST_FILE_GLOBS:
        for f in tests_dir.rglob(pattern):
            if f.is_file():
                seen[str(f)] = f
    return sorted(seen.values())


def _validate_testwriter_output(
    workspace: WorkspaceManager,
    task: Task,
    loop: ScopedResult,
) -> str | None:
    """
    TestWriter 실행 결과가 파이프라인 다음 단계에 진입할 만큼 유효한지 검증한다.

    통과 시 ``None``. 실패 시 재시도 프롬프트에 주입할 short failure_reason 문자열.

    반환되는 reason 은 **단일 라인 prefix** 로 시작한다 (로그·UI 집계 용이):
        [NO_WRITE]             write_file/edit_file 호출 0회
        [TEST_MISSING]         tests/ 에 테스트 파일 없음
        [TEST_SYNTAX_ERROR]    Python 파일 파싱 실패
        [TEST_SKELETON_ONLY]   스켈레톤(TODO 마커)이 그대로 남음
        [NO_TEST_FUNCTIONS]    test_* 함수 0개 (Python 기준)
    """
    # 1) 쓰기 호출 카운터 — 한 번도 쓰기를 시도하지 않았다면 나머지는 볼 필요가 없다.
    #    스켈레톤은 workspace 생성 시 선주입되므로 파일 자체는 존재할 수 있다.
    if loop.write_file_count == 0 and loop.edit_file_count == 0:
        return (
            "[NO_WRITE] TestWriter 가 write_file/edit_file 을 한 번도 호출하지 않았습니다. "
            "탐색만 하지 말고 반드시 write_file 로 tests/ 에 실제 테스트를 작성하세요."
        )

    # 2) 테스트 파일 존재 확인 — 스켈레톤은 _inject_test_skeletons() 로 이미 생성되므로
    #    이 단계에서 파일이 없다면 TestWriter 가 삭제하거나 tests/ 밖으로 쓴 경우다.
    test_files = _collect_test_files(workspace.tests_dir)
    if not test_files:
        return (
            "[TEST_MISSING] workspace/tests/ 에 테스트 파일이 없습니다. "
            "선주입된 스켈레톤 파일을 덮어쓰지 말고 그 위에 실제 테스트를 작성하세요."
        )

    # 3) Syntax error 체크 (Python)
    for f in test_files:
        if f.suffix == ".py":
            try:
                ast.parse(f.read_text(encoding="utf-8"))
            except SyntaxError as e:
                return f"[TEST_SYNTAX_ERROR] {f.name}: {e}"

    # 4) 스켈레톤 미변경 감지 (TODO 마커 + 테스트 함수 없음)
    for f in test_files:
        try:
            content = f.read_text(encoding="utf-8")
        except OSError as e:
            return f"[TEST_MISSING] {f.name}: 읽기 실패 — {e}"
        if is_skeleton_unchanged(content, task.id):
            return (
                f"[TEST_SKELETON_ONLY] {f.name} 이 선주입된 스켈레톤 그대로입니다. "
                f"`# TODO: tests for task {task.id}` 주석을 실제 테스트로 교체하세요."
            )

    # 5) Python: 최소 1개의 test_* 함수 존재
    for f in test_files:
        if f.suffix != ".py":
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            # 3) 에서 이미 처리되지만 방어적으로 한 번 더 건너뜀
            continue
        has_test_fn = any(
            isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
            for n in ast.walk(tree)
        )
        if not has_test_fn:
            return (
                f"[NO_TEST_FUNCTIONS] {f.name} 에 test_* 함수가 없습니다. "
                "pytest 가 수집할 수 있도록 `def test_...():` 형태로 작성하세요."
            )

    return None


def _accumulate_tokens(metrics: PipelineMetrics, role: str, scoped: ScopedResult) -> None:
    """ScopedResult 의 토큰 사용량을 metrics.token_usage 에 누적한다."""
    if scoped.loop_result is None:
        return
    prev = metrics.token_usage.get(role, (0, 0, 0, 0))
    # 하위 호환: 기존 2-tuple 데이터 처리
    if len(prev) == 2:
        prev = (*prev, 0, 0)
    lr = scoped.loop_result
    metrics.token_usage[role] = (
        prev[0] + (lr.total_input_tokens or 0),
        prev[1] + (lr.total_output_tokens or 0),
        prev[2] + (lr.total_cached_read_tokens or 0),
        prev[3] + (lr.total_cached_write_tokens or 0),
    )
    if lr.call_log:
        metrics.call_logs.setdefault(role, []).extend(lr.call_log)


def _parse_dependency_artifact_files(dep_artifact: Path) -> set[str]:
    """dependency_artifacts.md 에 기록된 실제 주입 파일 목록을 파싱한다."""
    try:
        content = dep_artifact.read_text(encoding="utf-8")
    except OSError:
        return set()

    injected: set[str] = set()
    for line in content.splitlines():
        match = re.match(r"^\*\*파일\*\*:\s*(.+)$", line.strip())
        if not match:
            continue
        for raw_path in match.group(1).split(","):
            rel_path = raw_path.strip().strip("`")
            if rel_path:
                injected.add(strip_src_prefix(rel_path))
    return injected


# ── Invariant 체크 ────────────────────────────────────────────────────────────


def _assert_invariants(report) -> None:
    """TaskReport 가 불가능한 상태 조합을 담고 있지 않은지 검증한다.

    `assert` 가 아닌 explicit ``raise RuntimeError`` 를 사용해 Python ``-O``
    플래그에서도 비활성화되지 않도록 한다.

    검증 대상 (status == "COMPLETED" 일 때만):
      - quality_gate_verdict 가 "BLOCKED" 이면 안 됨 (task-009 회귀 방지)
      - test_count 가 0 이면 안 됨 (테스트 없이 COMPLETED 불가)
      - reviewer_verdict 가 APPROVED / APPROVED_WITH_SUGGESTIONS / "" 이 아닌 경우
        (예: CHANGES_REQUESTED, ERROR) COMPLETED 와 양립 불가

    FAILED 상태나 기타 상태는 모든 조합이 합법이므로 체크하지 않는다.
    """
    if getattr(report, "status", None) != "COMPLETED":
        return

    task_id = getattr(report, "task_id", "<unknown>")
    qg = getattr(report, "quality_gate_verdict", None)
    if qg == "BLOCKED":
        raise RuntimeError(
            f"INVARIANT: task {task_id} is COMPLETED but "
            f"quality_gate_verdict is BLOCKED"
        )

    test_count = getattr(report, "test_count", 0)
    if test_count == 0:
        raise RuntimeError(
            f"INVARIANT: task {task_id} is COMPLETED but "
            f"test_count is 0 (task-009 regression)"
        )

    rv = getattr(report, "reviewer_verdict", "")
    if rv not in ("APPROVED", "APPROVED_WITH_SUGGESTIONS", ""):
        raise RuntimeError(
            f"INVARIANT: task {task_id} is COMPLETED but "
            f"reviewer_verdict is {rv}"
        )


# ── 리뷰 파싱 ─────────────────────────────────────────────────────────────────


_LLM_ERROR_PREFIX = "LLM 호출 중 오류가 발생했습니다"


def _parse_review(raw: str) -> ReviewResult:
    """
    Reviewer 에이전트 출력에서 VERDICT / SUMMARY / DETAILS 를 추출한다.

    기대 형식:
        VERDICT: APPROVED
        SUMMARY: 전반적으로 잘 구현되었음
        DETAILS:
        ...

    파싱 규칙 (strict — 본문/예시 텍스트가 control flow를 바꾸지 못하도록):
        - `VERDICT:` 라인은 **첫 번째 매칭만** 채택한다. 이후 VERDICT 라인은
          details 로 흘려 보내 quote/예시 안의 "VERDICT: ..." 가 verdict 를
          덮어쓸 수 없게 한다. 동일 원칙이 `SUMMARY:` 에도 적용된다.
        - 자유서술 텍스트의 키워드("...APPROVED..." 같은) 로는 verdict 를 추론하지
          않는다. VERDICT 라인이 없으면 CHANGES_REQUESTED 로 fallback 한다.
        - 빈 출력이나 LLM_ERROR sentinel 은 즉시 `verdict="ERROR"` 로 분류한다
          (인프라 장애와 코드 반려를 분리).
        - 알 수 없는 verdict (예: `VERDICT: MAYBE`) 도 CHANGES_REQUESTED 로
          fallback 한다. ERROR 로 퇴하지 않는 이유: 파서 혼란을 LLM 인프라
          장애로 오인해 Implementer 재실행을 건너뛰면 안 되기 때문.
    """
    verdict = ""
    summary = ""
    details_lines: list[str] = []
    in_details = False

    stripped_raw = (raw or "").strip()

    # 빈 출력 또는 LLM 호출 실패 sentinel → 즉시 ERROR
    if not stripped_raw or stripped_raw.startswith(_LLM_ERROR_PREFIX):
        logger.warning("Reviewer 출력이 비었거나 LLM 호출 실패 — verdict=ERROR")
        return ReviewResult(
            verdict="ERROR",
            summary="Reviewer LLM 호출 실패",
            details=stripped_raw or "(응답 없음)",
            raw=raw,
        )

    for line in raw.splitlines():
        stripped = line.strip()
        # 마크다운 볼드(**VERDICT**: ...) 또는 헤더(## VERDICT: ...) 제거 후 파싱
        normalized = re.sub(r"^\*{1,3}|^#{1,6}\s*|\*{1,3}(?=:)", "", stripped).strip()
        upper = normalized.upper()

        # VERDICT 는 첫 매칭만 채택. 이후 VERDICT 라인은 details (또는 스킵) 처리.
        if not verdict and upper.startswith("VERDICT:"):
            value = normalized.split(":", 1)[1].strip().upper()
            if value in VALID_VERDICTS:
                verdict = value
            elif value:
                logger.warning(
                    "Unknown verdict %r, fallback to CHANGES_REQUESTED", value
                )
                verdict = "CHANGES_REQUESTED"
            continue

        # SUMMARY 도 첫 매칭만. in_details=True 이후 나오는 "SUMMARY:" 는 details 일부.
        if not summary and not in_details and upper.startswith("SUMMARY:"):
            summary = normalized.split(":", 1)[1].strip()
            continue

        if not in_details and upper.startswith("DETAILS:"):
            in_details = True
            inline = normalized.split(":", 1)[1].strip()
            if inline:
                details_lines.append(inline)
            continue

        if in_details:
            details_lines.append(line)

    # VERDICT 라인을 못 찾았다 = 형식 미준수 → CHANGES_REQUESTED fallback
    # (키워드 추론은 하지 않는다 — quote/예시 텍스트가 승인으로 흐르는 것을 막는다)
    if not verdict:
        logger.warning(
            "Reviewer 출력에 VERDICT 라인이 없음 — CHANGES_REQUESTED fallback"
        )
        verdict = "CHANGES_REQUESTED"
        if not summary:
            summary = "Reviewer 응답에 VERDICT 라인이 없음"
        if not details_lines:
            details_lines = [stripped_raw[:500]]

    return ReviewResult(
        verdict=verdict,
        summary=summary,
        details="\n".join(details_lines).strip(),
        raw=raw,
    )
