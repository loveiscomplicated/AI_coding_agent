"""
cli/instant_runner.py — CLI 전용 단일 태스크 파이프라인 러너

자연어 한 줄 → 미니 회의(TaskConverter) → 확인(PipelineConfirmManager) →
TDDPipeline 실행 → 재시도(RetryPrompt) → GitWorkflow 로컬 커밋 → 결과 출력.

진입점: `InstantRunner.run(user_input)` (async)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from agents.roles import ROLE_INTERVENTION, ROLE_TEST_WRITER, ROLE_IMPLEMENTER, resolve_model_for_role
from cli.interface import print_pipeline_result, print_task_summary
from cli.pipeline_confirm import ConfirmType, PipelineConfirmManager
from cli.retry_prompt import RetryDecision, RetryPrompt
from cli.task_converter import ConversionError, TaskConverter
from llm import LLMConfig, create_client
from orchestrator.escalation import TIER_INTERNAL_MAX_RETRIES, resolve_tier_chain, should_escalate_tier
from orchestrator.git_workflow import GitWorkflow
from orchestrator.intervention import create_intervention_llms, set_complexity_routing, set_llm, set_model_config, classify_and_analyze
from orchestrator.pipeline import TDDPipeline, PipelineResult
from orchestrator.task import Task
from orchestrator.workspace import WorkspaceManager

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# pipeline.py가 실패 결과로 돌려주는 인프라 장애 접두어
_INFRA_PREFIXES = (
    "[REVIEWER_INFRA_ERROR]",
    "[DEPENDENCY_MISSING]",
)


class RunMode(Enum):
    FULL_TDD = "tdd"    # TestWriter → DockerTest → Quality Gate → Implementer → DockerTest → Reviewer
    NO_TDD = "no_tdd"   # Implementer → Reviewer


@dataclass
class InstantRunResult:
    task: Task | None                   # 미니 회의 중단 시 None
    success: bool
    pipeline_result: PipelineResult | None
    user_aborted: bool                  # 사용자가 Esc / Q / Ctrl+C로 중단
    retry_count: int                    # 사용자 수동 재시도 횟수
    total_auto_retries: int             # 자동 재시도 총 횟수


class _StopController:
    """
    CLI용 최소 중단 컨트롤러 — PauseController 덕타입.

    start_q_listener()를 호출하면 백그라운드 스레드가 stdin에서 Q/q를
    감지해 stop()을 호출한다. stdin이 TTY가 아니면(테스트 등) 즉시 반환.
    """

    def __init__(self) -> None:
        self._stopped = False
        self._paused = threading.Event()
        self._yielded = threading.Event()
        self._thread: threading.Thread | None = None

    def start_q_listener(self) -> None:
        """백그라운드에서 Q 키 입력을 감시한다 (TTY에서만 활성화)."""
        import sys
        if not sys.stdin.isatty():
            return
        from cli.interrupt import register_stdin_reader
        register_stdin_reader(self)
        self._thread = threading.Thread(target=self._listen_q, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        from cli.interrupt import unregister_stdin_reader
        unregister_stdin_reader(self)
        self._stopped = True

    def pause(self) -> None:
        """백그라운드 스레드가 터미널을 반환할 때까지 블록하며 일시정지한다."""
        self._yielded.clear()
        self._paused.set()
        self._yielded.wait(timeout=0.3)

    def resume(self) -> None:
        """일시정지를 해제한다."""
        self._paused.clear()

    def _listen_q(self) -> None:
        import os
        import sys
        import select
        import time
        import tty
        import termios

        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
            tty.setcbreak(fd, termios.TCSANOW)
            try:
                while not self._stopped:
                    if self._paused.is_set():
                        termios.tcsetattr(fd, termios.TCSANOW, old)
                        self._yielded.set()
                        while self._paused.is_set() and not self._stopped:
                            time.sleep(0.05)
                        if self._stopped:
                            return
                        tty.setcbreak(fd, termios.TCSANOW)
                        continue
                    readable, _, _ = select.select([fd], [], [], 0.2)
                    if readable:
                        ch = os.read(fd, 1)
                        if ch in (b"q", b"Q"):   # Q로 중단 (Ctrl+C는 SIGINT로 처리)
                            self._stopped = True
                            break
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass

    @property
    def is_stopped(self) -> bool:
        return self._stopped

    @property
    def is_paused(self) -> bool:
        return False

    def wait_if_paused(self) -> bool:
        return False


class InstantRunner:
    """
    CLI에서 단일 태스크를 TDD 파이프라인으로 실행한다.

    전체 흐름:
      1. TaskConverter로 미니 회의 → Task 생성 (Esc 중단 가능)
      2. PipelineConfirmManager로 태스크 확인 (TASK_REVIEW, 스킵 가능)
      3. 태스크 크기 초과 검사 (target_files >= 5)
      4. TDDPipeline 실행 (mode에 따라 skip_test_writer 결정)
      5. 파이프라인 내부 자동 재시도 소진
      6. 여전히 실패 → RetryPrompt로 사용자 판단 요청
      7. 성공 → 스킵 불가능 확인 트리거
      8. GitWorkflow 실행 (로컬 커밋)
      9. print_pipeline_result()로 결과 출력
    """

    def __init__(
        self,
        repo_path: str,
        converter: TaskConverter,
        confirm: PipelineConfirmManager,
        retry: RetryPrompt,
        default_role_models: dict[str, dict[str, str]],
        complexity_role_models: dict[str, dict[str, dict[str, str]]],
        auto_select_by_complexity: bool = True,
        mode: RunMode = RunMode.FULL_TDD,
    ):
        self.repo_path = repo_path
        self.converter = converter
        self.confirm = confirm
        self.retry = retry
        self.default_role_models = default_role_models
        self.complexity_role_models = complexity_role_models
        self.auto_select_by_complexity = auto_select_by_complexity
        self.mode = mode

    async def run(self, user_input: str) -> InstantRunResult:
        """자연어 입력을 받아 전체 흐름을 실행한다."""
        # ── 1. 미니 회의 ─────────────────────────────────────────────────────
        try:
            conversion = await self.converter.convert(user_input)
        except ConversionError as exc:
            logger.warning("미니 회의 오류: %s", exc)
            decision = self.retry.ask_on_pipeline_error(str(exc))
            if decision.action == "retry":
                return await self.run(user_input)
            return InstantRunResult(
                task=None, success=False, pipeline_result=None,
                user_aborted=True, retry_count=0, total_auto_retries=0,
            )

        if conversion.aborted:
            return InstantRunResult(
                task=None, success=False, pipeline_result=None,
                user_aborted=True, retry_count=0, total_auto_retries=0,
            )

        task = conversion.task
        assert task is not None

        print_task_summary(task, warnings=conversion.warnings or [])

        # ── 2. TASK_REVIEW 확인 (스킵 가능) ──────────────────────────────────
        if not self.confirm.confirm(
            ConfirmType.TASK_REVIEW,
            "태스크를 확인하세요.",
            detail=f"대상 파일: {', '.join(task.target_files) or '(없음)'}",
        ):
            return InstantRunResult(
                task=task, success=False, pipeline_result=None,
                user_aborted=True, retry_count=0, total_auto_retries=0,
            )

        # ── 2.5. 태스크 크기 초과 검사 ───────────────────────────────────────
        if len(task.target_files) >= 5:
            if not self.confirm.confirm(
                ConfirmType.TASK_TOO_LARGE,
                f"⚠️  target_files가 {len(task.target_files)}개입니다. "
                "이 작업은 멀티 에이전트 파이프라인이 더 적합할 수 있습니다.",
                detail="그래도 단일 태스크로 진행하시겠습니까?",
            ):
                return InstantRunResult(
                    task=task, success=False, pipeline_result=None,
                    user_aborted=False, retry_count=0, total_auto_retries=0,
                )

        self._configure_intervention_llms()
        pipeline = self._build_pipeline()
        manual_retry_count = 0
        total_auto_retries = 0
        last_result: PipelineResult | None = None

        # ── 3–6. 파이프라인 + 사용자 재시도 루프 ────────────────────────────
        while True:
            succeeded = False
            user_aborted = False
            manual_restart = False
            hints_tried: list[str] = []
            tier_chain = (
                resolve_tier_chain(task.complexity)
                if self.auto_select_by_complexity
                else [None]
            )

            for tier_idx, current_tier in enumerate(tier_chain):
                orch_attempt = 0
                while True:
                    try:
                        result = self._run_pipeline_once(task, pipeline, tier=current_tier)
                    except KeyboardInterrupt:
                        user_aborted = True
                        break
                    except Exception as exc:
                        logger.exception("파이프라인 실행 중 예외 발생: %s", exc)
                        decision = self.retry.ask_on_pipeline_error(str(exc))
                        if decision.action == "retry":
                            manual_retry_count += 1
                            manual_restart = True
                            break
                        return InstantRunResult(
                            task=task, success=False, pipeline_result=last_result,
                            user_aborted=True, retry_count=manual_retry_count,
                            total_auto_retries=total_auto_retries,
                        )

                    last_result = result
                    total_auto_retries += result.metrics.impl_retries
                    if result.succeeded:
                        succeeded = True
                        break

                    failure_reason = result.failure_reason or "(알 수 없는 오류)"
                    is_last_tier_attempt = orch_attempt >= TIER_INTERNAL_MAX_RETRIES
                    if not is_last_tier_attempt:
                        analysis = classify_and_analyze(
                            task,
                            failure_reason,
                            orch_attempt + 1,
                            test_stdout=result.test_result.stdout if result.test_result else "",
                            previous_hints=hints_tried,
                            role_models=None,
                            tier=current_tier,
                        )
                        if analysis.should_retry:
                            hints_tried.append(analysis.hint)
                            task.last_error = (
                                f"[Intervention 힌트 #{orch_attempt + 1}]\n{analysis.hint}\n\n"
                                f"이전 오류:\n{task.last_error or ''}"
                            )
                            task.failure_reason = ""
                            orch_attempt += 1
                            continue
                        is_last_tier_attempt = True

                    if (
                        self.auto_select_by_complexity
                        and tier_idx < len(tier_chain) - 1
                        and should_escalate_tier(failure_reason)
                    ):
                        task.failure_reason = ""
                        task.last_error = ""
                        break

                    break

                if manual_restart or user_aborted or succeeded:
                    break
                if last_result and last_result.succeeded:
                    break
                if (
                    last_result is not None
                    and last_result.failure_reason
                    and self.auto_select_by_complexity
                    and tier_idx < len(tier_chain) - 1
                    and should_escalate_tier(last_result.failure_reason)
                ):
                    continue
                break

            if user_aborted:
                return InstantRunResult(
                    task=task, success=False, pipeline_result=last_result,
                    user_aborted=True, retry_count=manual_retry_count,
                    total_auto_retries=total_auto_retries,
                )
            if manual_restart:
                continue
            if last_result is not None and last_result.succeeded:
                break

            # 예외 없는 경로 — 성공이면 루프 탈출
            if last_result is not None and last_result.succeeded:
                break

            # [ABORTED]: Q 키 / stop_ctrl 중단 요청 → 즉시 반환
            if last_result is not None and last_result.failure_reason.startswith("[ABORTED]"):
                return InstantRunResult(
                    task=task, success=False, pipeline_result=last_result,
                    user_aborted=True, retry_count=manual_retry_count,
                    total_auto_retries=total_auto_retries,
                )

            # 인프라 장애(LLM API 오류 등) vs 일반 테스트 실패 분기
            failure = (
                (last_result.failure_reason if last_result else None)
                or "(알 수 없는 오류)"
            )
            if any(failure.startswith(p) for p in _INFRA_PREFIXES):
                decision = self.retry.ask_on_pipeline_error(failure)
            else:
                decision = self.retry.ask_on_test_failure(
                    failure, auto_retry_count=total_auto_retries,
                )

            if decision.action == "retry":
                manual_retry_count += 1
                continue
            elif decision.action == "retry_with_hint":
                task.description += f"\n\n### 사용자 힌트\n{decision.hint}"
                manual_retry_count += 1
                continue
            elif decision.action == "ignore":
                break
            else:  # quit
                return InstantRunResult(
                    task=task, success=False, pipeline_result=last_result,
                    user_aborted=True, retry_count=manual_retry_count,
                    total_auto_retries=total_auto_retries,
                )

        # ── 9. 결과 출력 ──────────────────────────────────────────────────────
        if last_result is not None:
            print_pipeline_result(last_result)

        return InstantRunResult(
            task=task,
            success=last_result.succeeded if last_result else False,
            pipeline_result=last_result,
            user_aborted=False,
            retry_count=manual_retry_count,
            total_auto_retries=total_auto_retries,
        )

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────────

    def _build_pipeline(self) -> TDDPipeline:
        from docker.runner import DockerTestRunner

        skip = self.mode == RunMode.NO_TDD
        tw_provider, tw_model = resolve_model_for_role(
            role=ROLE_TEST_WRITER,
            role_models=None,
            default_role_models=self.default_role_models,
        )
        impl_provider, impl_model = resolve_model_for_role(
            role=ROLE_IMPLEMENTER,
            role_models=None,
            default_role_models=self.default_role_models,
        )
        agent_llm = create_client(tw_provider, LLMConfig(model=tw_model, max_tokens=8192))
        impl_llm = create_client(impl_provider, LLMConfig(model=impl_model, max_tokens=8192))
        return TDDPipeline(
            agent_llm=agent_llm,
            implementer_llm=impl_llm,
            test_runner=DockerTestRunner(),
            default_role_models=self.default_role_models,
            auto_select_by_complexity=self.auto_select_by_complexity,
            complexity_map=self.complexity_role_models,
            skip_test_writer=skip,
        )

    def _configure_intervention_llms(self) -> None:
        provider, model = resolve_model_for_role(
            role=ROLE_INTERVENTION,
            role_models=None,
            default_role_models=self.default_role_models,
        )
        analyze_llm, report_llm = create_intervention_llms(provider, model)
        set_llm(analyze_llm, report_llm)
        set_model_config(self.default_role_models)
        # CLI TDD에서는 intervention 자체는 항상 capable 계열을 유지한다.
        set_complexity_routing(False, None)

    def _run_pipeline_once(
        self,
        task: Task,
        pipeline: TDDPipeline,
        *,
        tier: str | None,
    ) -> PipelineResult:
        stop_ctrl = _StopController()
        _exc: BaseException | None = None
        last_result: PipelineResult | None = None

        with WorkspaceManager(task, self.repo_path) as workspace:
            initial_src_snapshot = workspace.snapshot_src_files()
            stop_ctrl.start_q_listener()
            try:
                result = pipeline.run(task, workspace, pause_ctrl=stop_ctrl, tier=tier)
                last_result = result
            except (Exception, KeyboardInterrupt) as exc:
                _exc = exc
                workspace.cleanup()
            else:
                if result.succeeded:
                    ok = self._check_post_pipeline(
                        task, workspace, result, initial_src_snapshot
                    )
                    if ok:
                        git = GitWorkflow(self.repo_path)
                        git.run(task, workspace, result, no_push=True)
            finally:
                stop_ctrl.stop()

        if _exc is not None:
            raise _exc
        assert last_result is not None
        return last_result

    def _check_post_pipeline(
        self,
        task: Task,
        workspace: WorkspaceManager,
        pipeline_result: PipelineResult,
        initial_src_snapshot: dict[str, str],
    ) -> bool:
        """
        파이프라인 성공 후 스킵 불가능 확인들을 순서대로 검사한다.
        하나라도 False이면 False 반환 (커밋 안 함).
        """
        current_src_snapshot = workspace.snapshot_src_files()

        # ── 1. target_files 밖 수정 감지 ─────────────────────────────────────
        # 초기 스냅샷 대비 신규 생성/내용 변경된 비대상 파일만 감지한다.
        out_of_scope = self._get_out_of_scope_files(
            task,
            initial_src_snapshot,
            current_src_snapshot,
        )
        if out_of_scope:
            if not self.confirm.confirm(
                ConfirmType.OUT_OF_SCOPE_FILE,
                "⚠️  태스크 범위 밖 파일이 수정되었습니다.",
                detail="\n".join(f"  - {f}" for f in out_of_scope),
            ):
                return False

        # ── 2. 파일 삭제 감지 (workspace diff 기준) ──────────────────────────
        deleted_files = self._get_deleted_files(initial_src_snapshot, current_src_snapshot)
        if deleted_files:
            if not self.confirm.confirm(
                ConfirmType.FILE_DELETION,
                "⚠️  기존 파일이 삭제되었습니다.",
                detail="\n".join(f"  - {f}" for f in deleted_files),
            ):
                return False

        # ── 3. 기존 테스트 깨짐 감지 ─────────────────────────────────────────
        if self._has_broken_existing_tests(pipeline_result, workspace):
            if not self.confirm.confirm(
                ConfirmType.EXISTING_TEST_BROKEN,
                "⚠️  기존 테스트가 깨진 것으로 감지되었습니다.",
                detail="구현이 기존 테스트 파일을 수정했거나 기존 테스트를 실패시킵니다.",
            ):
                return False

        # ── 4. Reviewer CHANGES_REQUESTED ────────────────────────────────────
        verdict = pipeline_result.review.verdict if pipeline_result.review else None
        if verdict == "CHANGES_REQUESTED":
            if not self.confirm.confirm(
                ConfirmType.COMMIT_CHANGES_REQUESTED,
                "⚠️  Reviewer가 변경을 요청했습니다. 그래도 커밋하시겠습니까?",
                detail=pipeline_result.review.details if pipeline_result.review else None,
            ):
                return False

        # ── 5. APPROVED 후 커밋 확인 (스킵 가능) ─────────────────────────────
        if verdict in ("APPROVED", "APPROVED_WITH_SUGGESTIONS"):
            if not self.confirm.confirm(
                ConfirmType.COMMIT_APPROVED,
                "✅ Reviewer 승인. 커밋하시겠습니까?",
            ):
                return False

        return True

    def _get_out_of_scope_files(
        self,
        task: Task,
        initial_src_snapshot: dict[str, str],
        current_src_snapshot: dict[str, str],
    ) -> list[str]:
        """
        초기 스냅샷 대비 신규 생성되었거나 내용이 바뀐 비대상 파일을 반환한다.
        삭제는 별도 _get_deleted_files() 에서 처리한다.
        """
        expected = {_norm(f) for f in task.target_files}
        changed = {
            _norm(rel_path)
            for rel_path, digest in current_src_snapshot.items()
            if initial_src_snapshot.get(rel_path) != digest
        }
        return sorted(changed - expected)

    def _get_deleted_files(
        self,
        initial_src_snapshot: dict[str, str],
        current_src_snapshot: dict[str, str],
    ) -> list[str]:
        """초기 workspace에 있던 파일 중 pipeline 실행 후 사라진 파일 목록 (diff 기준)."""
        current = {_norm(f) for f in current_src_snapshot}
        initial_normed = {_norm(f) for f in initial_src_snapshot}
        return sorted(initial_normed - current)

    def _has_broken_existing_tests(
        self,
        pipeline_result: PipelineResult,
        workspace: WorkspaceManager,
    ) -> bool:
        """기존(TestWriter 미생성) 테스트 파일이 수정된 경우 True를 반환한다."""
        new_test_files = {_norm(f) for f in pipeline_result.test_files}
        all_test_files = {_norm(f) for f in workspace.list_test_files()}
        existing_test_files = all_test_files - new_test_files
        return bool(existing_test_files)


def _norm(path: str) -> str:
    """src/ prefix를 제거한 정규화 경로를 반환한다."""
    return path.removeprefix("src/")
