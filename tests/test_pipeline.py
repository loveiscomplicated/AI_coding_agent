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
    (ws.tests_dir / "test_auth.py").write_text("def test_login(): pass\n")
    return ws


def _ok_scoped(answer: str = "완료") -> ScopedResult:
    return ScopedResult(answer=answer, succeeded=True)


def _fail_scoped(answer: str = "실패") -> ScopedResult:
    return ScopedResult(answer=answer, succeeded=False)


def _pass_run() -> RunResult:
    return RunResult(passed=True, returncode=0, stdout="", summary="2 passed in 0.1s")


def _fail_run(summary: str = "1 failed in 0.1s") -> RunResult:
    return RunResult(
        passed=False, returncode=1, stdout="AssertionError: ...", summary=summary
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

    def test_defaults_to_changes_requested_on_missing_verdict(self):
        r = _parse_review("아무 형식도 없는 텍스트")
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
    def test_review_changes_requested_still_succeeds(self, MockLoop, task, workspace):
        """CHANGES_REQUESTED 여도 파이프라인은 succeeded=True 반환."""
        MockLoop.return_value.run.return_value = _ok_scoped(
            "VERDICT: CHANGES_REQUESTED\nSUMMARY: 개선 필요\nDETAILS:\n수정 사항 있음"
        )
        mock_runner = MagicMock()
        mock_runner.run.return_value = _pass_run()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, workspace)

        assert result.succeeded is True
        assert result.review.verdict == "CHANGES_REQUESTED"

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
    def test_fails_when_no_test_files_created(self, MockLoop, task, tmp_path):
        # workspace 에 tests/ 파일 없음
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        # tests/ 디렉토리는 비어 있는 상태

        MockLoop.return_value.run.return_value = _ok_scoped("완료했는데 파일 안 만듦")
        mock_runner = MagicMock()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, ws)
        ws.cleanup()

        assert result.succeeded is False
        assert "tests/" in result.failure_reason

    @patch("orchestrator.pipeline.ScopedReactLoop")
    def test_fails_when_implementer_fails(self, MockLoop, task, workspace):
        # 호출 순서: TestWriter 성공, Implementer 실패
        MockLoop.return_value.run.side_effect = [
            _ok_scoped("테스트 작성 완료"),  # TestWriter
            _fail_scoped("구현 불가"),       # Implementer
        ]
        mock_runner = MagicMock()

        pipeline = TDDPipeline(agent_llm=MagicMock(), test_runner=mock_runner)
        result = pipeline.run(task, workspace)

        assert result.succeeded is False
        assert "Implementer" in result.failure_reason
        mock_runner.run.assert_not_called()

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
