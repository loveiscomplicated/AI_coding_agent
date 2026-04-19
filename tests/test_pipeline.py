"""
tests/test_pipeline.py

orchestrator/pipeline.py 단위 테스트.
ScopedReactLoop.run() 과 DockerTestRunner.run() 을 모킹해
파이프라인 상태 머신 로직만 검증한다.

실행:
    pytest tests/test_pipeline.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents.scoped_loop import ScopedResult
from docker.runner import RunResult
from orchestrator.pipeline import (
    TDDPipeline,
    PipelineResult,
    ReviewResult,
    _parse_review,
    _build_test_writer_prompt,
    _build_implementer_prompt,
    _build_reviewer_prompt,
    _validate_testwriter_output,
)
from orchestrator.task import Task, TaskStatus
from orchestrator.workspace import WorkspaceManager


# ── 픽스처 ────────────────────────────────────────────────────────────────────


@pytest.fixture
def task():
    return Task(
        id="task-001",
        title="사용자 로그인 구현",
        description="이메일과 비밀번호로 로그인하는 함수를 구현한다.",
        acceptance_criteria=[
            "올바른 자격증명으로 True 반환",
            "잘못된 자격증명으로 False 반환",
        ],
        target_files=["src/auth.py"],
    )


@pytest.fixture
def workspace(tmp_path, task):
    ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
    ws.create()
    # 테스트 파일이 있는 것처럼 만들어 줌
    (ws.tests_dir / "test_auth.py").write_text("def test_login():\n    assert True\n")
    # target_file 가드 충족: 구현이 이미 이루어진 상태로 시뮬레이션
    # (MockLoop 가 실제로 쓰기를 수행하지 않으므로 선주입한 빈 스켈레톤을 채워둠).
    # _copy_target_files() 가 선행 'src/' 한 단계를 떼고 배치하므로 같은 규칙을 따른다.
    from orchestrator.workspace import strip_src_prefix
    for rel_path in task.target_files:
        (ws.src_dir / strip_src_prefix(rel_path)).write_text(
            "# placeholder impl for pipeline test\n"
        )
    return ws


def _ok_scoped(
    answer: str = "완료",
    write_file_count: int = 1,
    edit_file_count: int = 0,
    explored_paths: list[str] | None = None,
) -> ScopedResult:
    """성공 mock. TestWriter 가드를 통과하려면 write 호출이 최소 1회 있어야 한다 —
    실제 루프에서는 write_file 이 호출되어야 파일이 남으므로 기본값 1이 현실적이다.
    NO_WRITE 경로를 테스트하려면 write_file_count=0 으로 호출한다."""
    return ScopedResult(
        answer=answer, succeeded=True,
        write_file_count=write_file_count,
        edit_file_count=edit_file_count,
        explored_paths=explored_paths or [],
    )


def _fail_scoped(answer: str = "실패") -> ScopedResult:
    return ScopedResult(answer=answer, succeeded=False)


def _pass_run() -> RunResult:
    return RunResult(passed=True, returncode=0, stdout="", summary="2 passed in 0.1s")


def _fail_run(summary: str = "1 failed in 0.1s") -> RunResult:
    return RunResult(
        passed=False, returncode=1, stdout="AssertionError: ...", summary=summary
    )


def _write_dependency_artifact(workspace: WorkspaceManager, file_paths: list[str]) -> None:
    context_dir = workspace.path / "context"
    context_dir.mkdir(exist_ok=True)
    sections = [
        "# 선행 태스크 산출물",
        "",
        "## task-dep: dependency",
        f"**파일**: {', '.join(file_paths)}",
    ]
    (context_dir / "dependency_artifacts.md").write_text(
        "\n".join(sections), encoding="utf-8"
    )


# ── _parse_review ─────────────────────────────────────────────────────────────


class TestParseReview:
    def test_parses_approved(self):
        raw = "VERDICT: APPROVED\nSUMMARY: 잘 구현됨\nDETAILS:\n문제 없음"
        r = _parse_review(raw)
        assert r.verdict == "APPROVED"
        assert r.approved is True
        assert r.summary == "잘 구현됨"
        assert "문제 없음" in r.details

    def test_parses_changes_requested(self):
        raw = "VERDICT: CHANGES_REQUESTED\nSUMMARY: 수정 필요\nDETAILS:\n보안 문제 있음"
        r = _parse_review(raw)
        assert r.verdict == "CHANGES_REQUESTED"
        assert r.approved is False

    def test_missing_verdict_falls_back_to_changes_requested(self):
        # VERDICT / APPROVED / CHANGES_REQUESTED 키워드가 전혀 없고
        # 인프라 장애(empty/LLM_ERROR sentinel)도 아닌 경우는 CHANGES_REQUESTED 로
        # fallback 한다. ERROR 는 진짜 인프라 장애(빈 응답, LLM 호출 실패)에만 쓴다.
        r = _parse_review("아무 형식도 없는 텍스트")
        assert r.verdict == "CHANGES_REQUESTED"
        assert r.is_error is False
        assert r.approved is False

    def test_empty_output_yields_error(self):
        r = _parse_review("")
        assert r.verdict == "ERROR"
        assert r.is_error is True

    def test_llm_error_sentinel_yields_error(self):
        r = _parse_review("LLM 호출 중 오류가 발생했습니다: 400 INVALID_ARGUMENT")
        assert r.verdict == "ERROR"
        assert r.is_error is True

    def test_free_text_does_not_grant_approval(self):
        # 자유서술 안에 'APPROVED' 단어가 있어도 VERDICT 라인이 없으면
        # 승인으로 해석하지 않는다 (quote/예시 텍스트로 인한 control-flow
        # 변경 방지). 이전에는 keyword fallback 으로 승인 처리되던 문자열.
        r = _parse_review("코드가 완벽합니다. APPROVED.")
        assert r.verdict == "CHANGES_REQUESTED"
        assert r.approved is False

    def test_free_text_keyword_changes_requested_also_falls_back(self):
        r = _parse_review("리뷰 결과 CHANGES_REQUESTED 가 필요합니다.")
        assert r.verdict == "CHANGES_REQUESTED"

    def test_case_insensitive_verdict(self):
        raw = "verdict: approved\nsummary: ok\ndetails:\nfine"
        r = _parse_review(raw)
        assert r.verdict == "APPROVED"

    def test_details_multiline(self):
        raw = (
            "VERDICT: APPROVED\n"
            "SUMMARY: ok\n"
            "DETAILS:\n"
            "line one\n"
            "line two\n"
            "line three\n"
        )
        r = _parse_review(raw)
        assert "line one" in r.details
        assert "line two" in r.details
        assert "line three" in r.details

    def test_raw_preserved(self):
        raw = "VERDICT: APPROVED\nSUMMARY: ok\nDETAILS:\ndetail"
        r = _parse_review(raw)
        assert r.raw == raw


# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────


class TestPromptBuilders:
    def test_test_writer_prompt_contains_task_info(self, task, workspace):
        prompt = _build_test_writer_prompt(task, workspace)
        assert task.title in prompt
        assert task.description in prompt
        assert "올바른 자격증명" in prompt
        assert str(workspace.path) in prompt

    def test_implementer_prompt_without_error(self, task, workspace):
        task.last_error = ""
        prompt = _build_implementer_prompt(task, workspace)
        assert task.title in prompt
        assert "이전 시도" not in prompt

    def test_implementer_prompt_with_error(self, task, workspace):
        task.last_error = "AssertionError: expected True, got False"
        task.retry_count = 1
        prompt = _build_implementer_prompt(task, workspace)
        assert "AssertionError" in prompt
        assert "이전 시도" in prompt
        assert "1회차" in prompt

    def test_implementer_prompt_truncates_long_error(self, task, workspace):
        task.last_error = "x" * 5000
        prompt = _build_implementer_prompt(task, workspace)
        assert "이하 생략" in prompt

    def test_reviewer_prompt_contains_test_summary(self, task, workspace):
        run_result = _pass_run()
        prompt = _build_reviewer_prompt(task, workspace, run_result)
        assert "2 passed" in prompt
        assert task.title in prompt


# ── TDDPipeline — 행복한 경로 ────────────────────────────────────────────────


class TestTDDPipelineHappyPath:
    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_succeeds_on_first_try(self, MockLoop, task, workspace):
        # 모든 에이전트가 성공, 테스트도 통과
        MockLoop.return_value.run.return_value = _ok_scoped(
            "VERDICT: APPROVED\nSUMMARY: ok\nDETAILS:\nfine"
        )
        mock_runner = MagicMock()
        mock_runner.run.return_value = _pass_run()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, workspace)

        assert result.succeeded is True
        assert task.status == TaskStatus.COMMITTING
        assert result.review is not None

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_task_status_becomes_committing(self, MockLoop, task, workspace):
        MockLoop.return_value.run.return_value = _ok_scoped(
            "VERDICT: APPROVED\nSUMMARY: ok\nDETAILS:\nfine"
        )
        mock_runner = MagicMock()
        mock_runner.run.return_value = _pass_run()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        pipeline.run(task, workspace)

        assert task.status == TaskStatus.COMMITTING

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_review_changes_requested_returns_failed(self, MockLoop, task, workspace):
        """CHANGES_REQUESTED 시 피드백 반영 재구현 후에도 실패하면 succeeded=False."""
        MockLoop.return_value.run.return_value = _ok_scoped(
            "VERDICT: CHANGES_REQUESTED\nSUMMARY: 개선 필요\nDETAILS:\n수정 사항 있음"
        )
        mock_runner = MagicMock()
        mock_runner.run.return_value = _pass_run()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, workspace)

        assert result.succeeded is False
        assert result.review.verdict == "CHANGES_REQUESTED"
        assert "CHANGES_REQUESTED" in result.failure_reason

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_result_contains_file_lists(self, MockLoop, task, workspace):
        MockLoop.return_value.run.return_value = _ok_scoped(
            "VERDICT: APPROVED\nSUMMARY: ok\nDETAILS:\nfine"
        )
        mock_runner = MagicMock()
        mock_runner.run.return_value = _pass_run()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, workspace)

        assert isinstance(result.test_files, list)
        assert isinstance(result.impl_files, list)


# ── TDDPipeline — 실패 경로 ──────────────────────────────────────────────────


class TestTDDPipelineFailurePaths:
    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_fails_when_test_writer_fails(self, MockLoop, task, workspace):
        MockLoop.return_value.run.return_value = _fail_scoped("LLM 오류")
        mock_runner = MagicMock()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, workspace)

        assert result.succeeded is False
        assert "TestWriter" in result.failure_reason
        assert task.status == TaskStatus.FAILED
        mock_runner.run.assert_not_called()

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_fails_when_test_writer_leaves_skeleton(self, MockLoop, task, tmp_path):
        """TestWriter 가 선주입 스켈레톤을 건드리지 않고 끝나면 가드가 차단한다.

        스켈레톤 선주입 이후 "tests/ 가 비었다" 는 상태는 발생하지 않는다 —
        동일 의도의 회귀 가드는 [TEST_SKELETON_ONLY] 판정으로 이동했다.
        """
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        # tests/test_auth.py 는 skeleton 그대로 남아 있음

        MockLoop.return_value.run.return_value = _ok_scoped("완료했는데 파일 안 만듦")
        mock_runner = MagicMock()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, ws)
        ws.cleanup()

        assert result.succeeded is False
        assert "[TEST_SKELETON_ONLY]" in result.failure_reason
        mock_runner.run.assert_not_called()

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_fails_when_implementer_fails(self, MockLoop, task, workspace):
        # 호출 순서: TestWriter 성공, (품질 게이트 통과), Implementer 실패
        MockLoop.return_value.run.side_effect = [
            _ok_scoped("테스트 작성 완료"),  # TestWriter
            _fail_scoped("구현 불가"),       # Implementer
        ]
        mock_runner = MagicMock()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, workspace)

        assert result.succeeded is False
        assert "Implementer" in result.failure_reason or "실패" in result.failure_reason
        mock_runner.run.assert_not_called()

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_fails_when_implementer_skips_target_files(self, MockLoop, task, tmp_path):
        """Implementer 가 'succeeded=True' 로 끝나도 target_file 을 안 쓰면 가드에 걸린다."""
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        (ws.tests_dir / "test_auth.py").write_text("def test_login():\n    assert True\n")
        # 주의: target_file 을 채우지 않는다 → 빈 스켈레톤 그대로

        MockLoop.return_value.run.return_value = _ok_scoped("했다고 주장하지만 실제로 쓰지 않음")
        mock_runner = MagicMock()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner, max_retries=2)
        result = pipeline.run(task, ws)
        ws.cleanup()

        assert result.succeeded is False
        assert "[TARGET_MISSING]" in result.failure_reason
        # Docker 는 한 번도 실행되지 않아야 한다 (가드가 사전 차단)
        mock_runner.run.assert_not_called()

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_target_gate_retries_before_failing(self, MockLoop, task, tmp_path):
        """가드 실패 시 impl_retries 루프에서 재시도를 먼저 시도한다."""
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        (ws.tests_dir / "test_auth.py").write_text("def test_login():\n    assert True\n")

        MockLoop.return_value.run.return_value = _ok_scoped("주장만 함")
        mock_runner = MagicMock()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner, max_retries=3)
        pipeline.run(task, ws)
        ws.cleanup()

        # Implementer 가 max_retries 만큼 재호출되어야 한다
        # (TestWriter 1 + Implementer 3 = 총 4회 ScopedReactLoop 호출 이상)
        assert MockLoop.return_value.run.call_count >= 4

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_fails_after_max_retries(self, MockLoop, task, workspace):
        # TestWriter 성공, Implementer 는 항상 성공하지만 테스트는 계속 실패
        MockLoop.return_value.run.return_value = _ok_scoped("완료")
        mock_runner = MagicMock()
        mock_runner.run.return_value = _fail_run()

        pipeline = TDDPipeline(
            agent_llm=MagicMock(), test_runner=mock_runner, max_retries=3
        )
        result = pipeline.run(task, workspace)

        assert result.succeeded is False
        assert "3회" in result.failure_reason
        assert task.status == TaskStatus.FAILED
        assert mock_runner.run.call_count == 3


# ── TDDPipeline — 재시도 로직 ────────────────────────────────────────────────


class TestTDDPipelineRetry:
    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_retries_after_test_failure(self, MockLoop, task, workspace):
        """테스트 1회 실패 → 재시도 → 성공."""
        MockLoop.return_value.run.return_value = _ok_scoped(
            "VERDICT: APPROVED\nSUMMARY: ok\nDETAILS:\nfine"
        )
        mock_runner = MagicMock()
        mock_runner.run.side_effect = [_fail_run(), _pass_run()]

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, workspace)

        assert result.succeeded is True
        assert task.retry_count == 1
        assert mock_runner.run.call_count == 2

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_last_error_set_on_retry(self, MockLoop, task, workspace):
        """실패 시 task.last_error 에 stdout 이 저장된다."""
        fail = RunResult(
            passed=False, returncode=1,
            stdout="AssertionError: expected True", summary="1 failed"
        )
        MockLoop.return_value.run.return_value = _ok_scoped(
            "VERDICT: APPROVED\nSUMMARY: ok\nDETAILS:\nok"
        )
        mock_runner = MagicMock()
        mock_runner.run.side_effect = [fail, _pass_run()]

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        pipeline.run(task, workspace)

        assert "AssertionError" in task.last_error

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_retry_count_increments(self, MockLoop, task, workspace):
        MockLoop.return_value.run.return_value = _ok_scoped(
            "VERDICT: APPROVED\nSUMMARY: ok\nDETAILS:\nok"
        )
        mock_runner = MagicMock()
        mock_runner.run.side_effect = [_fail_run(), _fail_run(), _pass_run()]

        pipeline = TDDPipeline(
            agent_llm=MagicMock(), test_runner=mock_runner, max_retries=3
        )
        result = pipeline.run(task, workspace)

        assert result.succeeded is True
        assert task.retry_count == 2


# ── PipelineResult ────────────────────────────────────────────────────────────


class TestPipelineResult:
    def test_failed_sets_task_status(self, task):
        result = PipelineResult.failed(task, "원인")
        assert task.status == TaskStatus.FAILED
        assert task.failure_reason == "원인"
        assert result.succeeded is False
        assert result.failure_reason == "원인"


class TestDependencyInjectionMetrics:
    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_dep_files_injected_counts_only_non_target_injected_files(
        self, MockLoop, tmp_path
    ):
        task = Task(
            id="task-metrics",
            title="metric",
            description="d",
            acceptance_criteria=["c"],
            target_files=["src/foo.py"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            (ws.tests_dir / "test_foo.py").write_text(_real_test_content(), encoding="utf-8")
            (ws.src_dir / "foo.py").write_text("def current():\n    return 1\n", encoding="utf-8")
            (ws.src_dir / "models").mkdir(exist_ok=True)
            (ws.src_dir / "models" / "user.py").write_text(
                "class User:\n    pass\n", encoding="utf-8"
            )
            (ws.src_dir / "models" / "__init__.py").write_text("", encoding="utf-8")
            _write_dependency_artifact(ws, ["src/foo.py", "src/models/user.py"])

            MockLoop.return_value.run.return_value = _ok_scoped(
                "VERDICT: APPROVED\nSUMMARY: ok\nDETAILS:\nfine"
            )
            mock_runner = MagicMock()
            mock_runner.run.return_value = _pass_run()

            pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
            result = pipeline.run(task, ws)

            assert result.metrics.dep_files_injected == 1
        finally:
            ws.cleanup()

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_dep_files_injected_zero_when_no_dependency_artifact(
        self, MockLoop, tmp_path
    ):
        task = Task(
            id="task-no-artifact",
            title="metric",
            description="d",
            acceptance_criteria=["c"],
            target_files=["src/foo.py"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            (ws.tests_dir / "test_foo.py").write_text(_real_test_content(), encoding="utf-8")
            (ws.src_dir / "foo.py").write_text("def current():\n    return 1\n", encoding="utf-8")

            MockLoop.return_value.run.return_value = _ok_scoped(
                "VERDICT: APPROVED\nSUMMARY: ok\nDETAILS:\nfine"
            )
            mock_runner = MagicMock()
            mock_runner.run.return_value = _pass_run()

            pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
            result = pipeline.run(task, ws)

            assert result.metrics.dep_files_injected == 0
        finally:
            ws.cleanup()

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_dep_files_injected_ignores_current_task_targets_even_if_present_in_src(
        self, MockLoop, tmp_path
    ):
        task = Task(
            id="task-duplicate",
            title="metric",
            description="d",
            acceptance_criteria=["c"],
            target_files=["src/foo.py"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            (ws.tests_dir / "test_foo.py").write_text(_real_test_content(), encoding="utf-8")
            (ws.src_dir / "foo.py").write_text("def current():\n    return 1\n", encoding="utf-8")
            _write_dependency_artifact(ws, ["src/foo.py"])

            MockLoop.return_value.run.return_value = _ok_scoped(
                "VERDICT: APPROVED\nSUMMARY: ok\nDETAILS:\nfine"
            )
            mock_runner = MagicMock()
            mock_runner.run.return_value = _pass_run()

            pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
            result = pipeline.run(task, ws)

            assert result.metrics.dep_files_injected == 0
        finally:
            ws.cleanup()


# ── APPROVED_WITH_SUGGESTIONS ────────────────────────────────────────────────
#
# task-008 재현 시나리오: reviewer 가 기능 충족은 인정하면서 스타일 제안만 남긴 경우,
# 기존에는 CHANGES_REQUESTED 로 퇴했다. 이제는 APPROVED_WITH_SUGGESTIONS 로
# 판정되어 PR 이 생성되고 status=COMPLETED 로 끝나야 한다.


_APPROVED_WITH_SUGGESTIONS_RAW = (
    "VERDICT: APPROVED_WITH_SUGGESTIONS\n"
    "SUMMARY: 기능 충족, 스타일 제안 있음\n"
    "DETAILS:\n"
    "## 승인 이유\n"
    "- 4 passed\n"
    "- acceptance_criteria 모두 검증됨\n\n"
    "## 개선 제안 (non-blocking)\n"
    "1. `src/auth.py:10`: try/except 대신 명시적 import 권장"
)


class TestParseReviewApprovedWithSuggestions:
    def test_parses_approved_with_suggestions(self):
        r = _parse_review(_APPROVED_WITH_SUGGESTIONS_RAW)
        assert r.verdict == "APPROVED_WITH_SUGGESTIONS"
        assert r.approved is True
        assert r.has_suggestions is True
        assert r.is_error is False
        assert "개선 제안" in r.details

    def test_free_text_approved_with_suggestions_does_not_bypass_verdict_line(self):
        # VERDICT 라인 없이 본문에 verdict 단어만 등장하면 승인되면 안 된다.
        r = _parse_review("리뷰 결론: APPROVED_WITH_SUGGESTIONS — 스타일 개선 몇 가지")
        assert r.verdict == "CHANGES_REQUESTED"
        assert r.approved is False

    def test_only_first_verdict_line_is_honored(self):
        # 첫 VERDICT 라인이 CHANGES_REQUESTED 이면 그 이후에 등장하는
        # "VERDICT: APPROVED" 는 details 로 흘러야 한다 (bypass 차단).
        raw = (
            "VERDICT: CHANGES_REQUESTED\n"
            "SUMMARY: 실패\n"
            "DETAILS:\n"
            "참고 예시 — 이전에는 다음과 같이 작성했다:\n"
            "  VERDICT: APPROVED\n"
            "  SUMMARY: ok\n"
        )
        r = _parse_review(raw)
        assert r.verdict == "CHANGES_REQUESTED"
        assert r.approved is False
        assert "VERDICT: APPROVED" in r.details

    def test_unknown_verdict_falls_back_to_changes_requested(self):
        # 기존 파서는 unknown verdict 를 ERROR 로 처리했다. 새 규약에서는
        # ERROR 는 빈 응답 / LLM_ERROR sentinel 에만 쓰고, 파싱 불가는 모두
        # CHANGES_REQUESTED 로 fallback.
        r = _parse_review("VERDICT: MAYBE\nSUMMARY: 모르겠음\nDETAILS:\n애매함")
        assert r.verdict == "CHANGES_REQUESTED"
        assert r.is_error is False


class TestTDDPipelineApprovedWithSuggestions:
    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_approved_with_suggestions_produces_pr(self, MockLoop, task, workspace):
        """APPROVED_WITH_SUGGESTIONS → 파이프라인은 COMMITTING 단계로 전환.
        (PR 실제 생성은 호출자 레이어지만, 여기서는 succeeded=True + review 객체
        approved=True 인지로 'PR 이 생성될 것' 을 검증한다.)"""
        MockLoop.return_value.run.return_value = _ok_scoped(_APPROVED_WITH_SUGGESTIONS_RAW)
        mock_runner = MagicMock()
        mock_runner.run.return_value = _pass_run()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, workspace)

        assert result.succeeded is True
        assert result.review is not None
        assert result.review.verdict == "APPROVED_WITH_SUGGESTIONS"
        assert result.review.approved is True  # PR 생성 분기 기준

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_approved_with_suggestions_sets_status_completed(
        self, MockLoop, task, workspace
    ):
        """task status 가 COMMITTING (→ 호출자가 COMPLETED 로 확정) 이 되어야 한다."""
        MockLoop.return_value.run.return_value = _ok_scoped(_APPROVED_WITH_SUGGESTIONS_RAW)
        mock_runner = MagicMock()
        mock_runner.run.return_value = _pass_run()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        pipeline.run(task, workspace)

        assert task.status == TaskStatus.COMMITTING

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_approved_with_suggestions_preserves_feedback_in_pr_body(
        self, MockLoop, task, workspace
    ):
        """APPROVED_WITH_SUGGESTIONS 의 피드백이 _build_pr_body 결과에 'Reviewer Suggestions' 섹션으로 포함되어야 한다."""
        from orchestrator.git_workflow import _build_pr_body

        MockLoop.return_value.run.return_value = _ok_scoped(_APPROVED_WITH_SUGGESTIONS_RAW)
        mock_runner = MagicMock()
        mock_runner.run.return_value = _pass_run()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, workspace)

        body = _build_pr_body(task, result)
        assert "Reviewer Suggestions (non-blocking)" in body
        assert "acceptance_criteria를 모두 충족" in body
        # 원본 피드백 내용이 유지되는지 확인
        assert "명시적 import 권장" in body

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_plain_approved_has_no_suggestions_section(
        self, MockLoop, task, workspace
    ):
        """단순 APPROVED 는 suggestions 섹션이 없어야 한다 (기존 동작 보존)."""
        from orchestrator.git_workflow import _build_pr_body

        MockLoop.return_value.run.return_value = _ok_scoped(
            "VERDICT: APPROVED\nSUMMARY: ok\nDETAILS:\nfine"
        )
        mock_runner = MagicMock()
        mock_runner.run.return_value = _pass_run()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, workspace)

        body = _build_pr_body(task, result)
        assert "Reviewer Suggestions (non-blocking)" not in body

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_changes_requested_still_triggers_retry(self, MockLoop, task, workspace):
        """CHANGES_REQUESTED 는 여전히 재구현 루프를 유발해야 한다 (기존 동작 보존).
        max_review_retries + 1 = 2 번의 리뷰, 각 리뷰마다 Implementer 가 호출된다."""
        MockLoop.return_value.run.return_value = _ok_scoped(
            "VERDICT: CHANGES_REQUESTED\nSUMMARY: 결함 있음\nDETAILS:\n기능 미충족"
        )
        mock_runner = MagicMock()
        mock_runner.run.return_value = _pass_run()

        pipeline = TDDPipeline(
            agent_llm=MagicMock(), test_runner=mock_runner,
            max_retries=1, max_review_retries=1,
        )
        result = pipeline.run(task, workspace)

        assert result.succeeded is False
        assert result.review is not None
        assert result.review.verdict == "CHANGES_REQUESTED"
        # Reviewer 가 최소 2회 호출됐어야 한다 (초회 + 재시도 1회)
        call_args = [c for c in MockLoop.call_args_list]
        assert len(call_args) >= 3  # TestWriter + Implementer*N + Reviewer*2


# ── TestWriter 종료 가드 ─────────────────────────────────────────────────────
#
# _validate_testwriter_output 가 실패 사유를 올바르게 반환하는지, 그리고
# 파이프라인이 그 사유로 재시도 + 최종 실패 판정을 내리는지 검증한다.


def _real_test_content() -> str:
    return "def test_login_ok():\n    assert 1 == 1\n"


class TestValidateTestWriterOutput:
    def test_passes_when_files_have_real_tests(self, task, tmp_path):
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            (ws.tests_dir / "test_auth.py").write_text(_real_test_content())
            scoped = _ok_scoped(write_file_count=1)
            assert _validate_testwriter_output(ws, task, scoped) is None
        finally:
            ws.cleanup()

    def test_no_write_when_counters_zero(self, task, tmp_path):
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            scoped = ScopedResult(answer="완료", succeeded=True,
                                  write_file_count=0, edit_file_count=0)
            reason = _validate_testwriter_output(ws, task, scoped)
            assert reason is not None
            assert reason.startswith("[NO_WRITE]")
        finally:
            ws.cleanup()

    def test_test_missing_when_tests_dir_empty(self, task, tmp_path):
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            # 스켈레톤을 전부 제거
            for f in list(ws.tests_dir.rglob("*")):
                if f.is_file():
                    f.unlink()
            scoped = _ok_scoped(write_file_count=1)
            reason = _validate_testwriter_output(ws, task, scoped)
            assert reason is not None
            assert reason.startswith("[TEST_MISSING]")
        finally:
            ws.cleanup()

    def test_syntax_error_detected(self, task, tmp_path):
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            (ws.tests_dir / "test_auth.py").write_text("def test_x(:\n    pass\n")
            scoped = _ok_scoped(write_file_count=1)
            reason = _validate_testwriter_output(ws, task, scoped)
            assert reason is not None
            assert reason.startswith("[TEST_SYNTAX_ERROR]")
            assert "test_auth.py" in reason
        finally:
            ws.cleanup()

    def test_skeleton_unchanged_detected(self, task, tmp_path):
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            # 스켈레톤 그대로 (create() 결과 유지)
            scoped = _ok_scoped(write_file_count=1)
            reason = _validate_testwriter_output(ws, task, scoped)
            assert reason is not None
            assert reason.startswith("[TEST_SKELETON_ONLY]")
            assert "test_auth.py" in reason
        finally:
            ws.cleanup()

    def test_react_jsx_tsx_skeletons_visible_to_guard(self, tmp_path):
        """회귀 가드: JSX/TSX 는 스켈레톤 선주입 대상이므로 가드 수집기도 봐야 한다.

        이전에는 `_TEST_FILE_GLOBS` 가 `.js/.ts` 만 포함해, 정상 배치된 `.jsx/.tsx`
        스켈레톤도 `[TEST_MISSING]` 으로 오판됐다. skeleton unchanged 를 트리거해
        수집기가 파일을 보고 있음을 확인한다.
        """
        for ext in ("jsx", "tsx"):
            task_x = Task(
                id=f"task-react-{ext}", title="t", description="d",
                acceptance_criteria=["c"],
                target_files=[f"Component.{ext}"],
                language="javascript" if ext == "jsx" else "typescript",
            )
            ws = WorkspaceManager(task_x, tmp_path, base_dir=tmp_path / f"ws_{ext}")
            ws.create()
            try:
                # 스켈레톤이 실제로 선주입됐는지 먼저 확인
                skel = ws.tests_dir / f"test_Component.{ext}"
                assert skel.exists(), f"{ext} 스켈레톤 미생성: {list(ws.tests_dir.rglob('*'))}"

                # 가드는 스켈레톤 그대로 → TEST_SKELETON_ONLY 를 반환해야 한다
                # ([TEST_MISSING] 이 나오면 수집기가 파일을 못 보는 것이다).
                scoped = _ok_scoped(write_file_count=1)
                reason = _validate_testwriter_output(ws, task_x, scoped)
                assert reason is not None
                assert reason.startswith("[TEST_SKELETON_ONLY]"), (
                    f"{ext} 가드가 파일을 못 봄: {reason!r}"
                )
            finally:
                ws.cleanup()

    def test_no_test_functions_detected(self, task, tmp_path):
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            # TODO 마커는 제거했지만 test_ 함수가 없는 상태
            (ws.tests_dir / "test_auth.py").write_text(
                "import pytest\n\nx = 1\n"
            )
            scoped = _ok_scoped(write_file_count=1)
            reason = _validate_testwriter_output(ws, task, scoped)
            assert reason is not None
            assert reason.startswith("[NO_TEST_FUNCTIONS]")
        finally:
            ws.cleanup()


class TestTestWriterGuardIntegration:
    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_testwriter_fails_when_no_write_file_called(self, MockLoop, task, tmp_path):
        """NO_WRITE: 쓰기 카운터가 0 이면 가드가 차단한다.

        스켈레톤 선주입 상태라 파일은 존재하지만, write_file 을 한 번도
        호출하지 않으면 탐색 루프로 간주해 실패 처리.
        """
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()

        MockLoop.return_value.run.return_value = ScopedResult(
            answer="탐색만 함", succeeded=True, write_file_count=0, edit_file_count=0,
        )
        mock_runner = MagicMock()
        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, ws)
        ws.cleanup()

        assert result.succeeded is False
        assert "[NO_WRITE]" in result.failure_reason
        mock_runner.run.assert_not_called()

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_testwriter_fails_when_test_file_missing(self, MockLoop, task, tmp_path):
        """TEST_MISSING: TestWriter 가 tests/ 밖에 파일을 쓰거나 스켈레톤을 삭제한 경우."""
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()

        def _wipe_tests(*_a, **_k):
            for f in list(ws.tests_dir.rglob("*")):
                if f.is_file():
                    f.unlink()
            return _ok_scoped("tests 비움")

        MockLoop.return_value.run.side_effect = _wipe_tests

        mock_runner = MagicMock()
        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, ws)
        ws.cleanup()

        assert result.succeeded is False
        assert "[TEST_MISSING]" in result.failure_reason
        mock_runner.run.assert_not_called()

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_testwriter_fails_when_syntax_invalid(self, MockLoop, task, tmp_path):
        """TEST_SYNTAX_ERROR: 테스트 파일에 Python 문법 오류가 있으면 차단."""
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()

        def _write_broken(*_a, **_k):
            (ws.tests_dir / "test_auth.py").write_text("def test_x(:\n    pass\n")
            return _ok_scoped("깨진 문법")

        MockLoop.return_value.run.side_effect = _write_broken
        mock_runner = MagicMock()
        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, ws)
        ws.cleanup()

        assert result.succeeded is False
        assert "[TEST_SYNTAX_ERROR]" in result.failure_reason
        mock_runner.run.assert_not_called()

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_testwriter_fails_when_skeleton_unchanged(self, MockLoop, task, tmp_path):
        """TEST_SKELETON_ONLY: 선주입 스켈레톤을 그대로 두면 차단."""
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        # 스켈레톤 파일은 create() 로 이미 존재. 건드리지 않음.

        MockLoop.return_value.run.return_value = _ok_scoped("안 건드림")
        mock_runner = MagicMock()
        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, ws)
        ws.cleanup()

        assert result.succeeded is False
        assert "[TEST_SKELETON_ONLY]" in result.failure_reason
        mock_runner.run.assert_not_called()

        # 회귀 가드 (#4): 실패 사유가 TaskReport 집계용 metrics 에 누적돼야 한다.
        assert result.metrics.quality_gate_rejections >= 1
        assert any(
            "[TEST_SKELETON_ONLY]" in r for r in result.metrics.quality_gate_reasons
        ), f"quality_gate_reasons 누락: {result.metrics.quality_gate_reasons}"

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_testwriter_fails_when_no_test_functions(self, MockLoop, task, tmp_path):
        """NO_TEST_FUNCTIONS: test_* 함수가 하나도 없으면 차단."""
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()

        def _write_no_fns(*_a, **_k):
            (ws.tests_dir / "test_auth.py").write_text("import pytest\n\nx = 1\n")
            return _ok_scoped("함수 없음")

        MockLoop.return_value.run.side_effect = _write_no_fns

        mock_runner = MagicMock()
        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, ws)
        ws.cleanup()

        assert result.succeeded is False
        assert "[NO_TEST_FUNCTIONS]" in result.failure_reason
        mock_runner.run.assert_not_called()

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_testwriter_retry_prompt_includes_explored_paths(
        self, MockLoop, task, tmp_path,
    ):
        """가드 실패 시 재시도 프롬프트에 prior_explored_paths + failure_reason 이
        주입되는지 검증한다.

        시나리오:
          1회차 TestWriter : 스켈레톤 그대로 남김 + explored_paths 반환 → 가드가 [TEST_SKELETON_ONLY] 로 차단
          2회차 TestWriter : 재시도 — 이 호출의 user_message 가 검증 대상
        """
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()

        # 1st call: skeleton unchanged + explored_paths
        scoped_first = ScopedResult(
            answer="탐색만",
            succeeded=True,
            write_file_count=1,
            edit_file_count=0,
            explored_paths=[
                "/ws/src/auth.py",
                "/ws/tests/test_auth.py",
                "/ws/src/auth.py",  # 중복 — dedupe 대상
            ],
        )

        # 2회차 이후는 파이프라인을 끝까지 돌릴 필요 없다 — 재시도 프롬프트가
        # 주입되었는지만 확인하면 되므로, 2회차에서도 skeleton 을 그대로 두어
        # 파이프라인이 실패하도록 둔다 (assert 대상은 call_args_list).
        call_counter = {"n": 0}

        def _side_effect(*_a, **_k):
            call_counter["n"] += 1
            if call_counter["n"] == 1:
                return scoped_first
            # 2회차: 여전히 skeleton → 가드가 최종 실패를 낸다
            return ScopedResult(
                answer="여전히 skeleton",
                succeeded=True,
                write_file_count=1,
                edit_file_count=0,
                explored_paths=[],
            )

        MockLoop.return_value.run.side_effect = _side_effect

        mock_runner = MagicMock()
        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, ws)
        ws.cleanup()

        # 파이프라인은 TestWriter 가드 재시도 후에도 실패해야 한다
        assert result.succeeded is False
        assert "[TEST_SKELETON_ONLY]" in result.failure_reason

        # 재시도 프롬프트 검증: 2번째 호출의 user_message 에 경로·사유 포함
        call_args_list = MockLoop.return_value.run.call_args_list
        assert len(call_args_list) >= 2, f"호출이 2회 미만: {len(call_args_list)}"
        second_call = call_args_list[1]
        second_prompt = (
            second_call.args[0] if second_call.args
            else second_call.kwargs.get("user_message", "")
        )
        assert "[TEST_SKELETON_ONLY]" in second_prompt
        assert "직전 시도에서 탐색한 파일" in second_prompt
        # 중복 제거 확인
        assert second_prompt.count("/ws/src/auth.py") == 1
        # 두 개의 고유 경로가 모두 포함
        assert "/ws/src/auth.py" in second_prompt
        assert "/ws/tests/test_auth.py" in second_prompt

        # Docker 는 한 번도 실행되지 않아야 한다 (가드가 사전 차단)
        mock_runner.run.assert_not_called()
