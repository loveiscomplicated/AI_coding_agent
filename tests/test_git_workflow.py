"""
tests/test_git_workflow.py

orchestrator/git_workflow.py 단위 테스트.
실제 git / gh 명령어를 실행하지 않고 subprocess.run 을 모킹해
GitWorkflow 로직만 검증한다.

실행:
    pytest tests/test_git_workflow.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from docker.runner import RunResult
from orchestrator.git_workflow import (
    GitWorkflow,
    GitWorkflowError,
    _build_pr_body,
    check_prerequisites,
)
from orchestrator.pipeline import PipelineResult, ReviewResult
from orchestrator.task import Task, TaskStatus
from orchestrator.workspace import WorkspaceManager


# ── 픽스처 ────────────────────────────────────────────────────────────────────


@pytest.fixture
def task():
    return Task(
        id="task-001",
        title="사용자 로그인 구현",
        description="이메일과 비밀번호로 로그인하는 함수를 구현한다.",
        acceptance_criteria=["올바른 자격증명으로 True 반환", "잘못된 자격증명으로 False 반환"],
        target_files=["src/auth.py"],
    )


@pytest.fixture
def workspace(tmp_path, task):
    ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
    ws.create()
    (ws.src_dir / "auth.py").write_text("def login(email, password): pass\n")
    (ws.tests_dir / "test_auth.py").write_text("def test_login(): assert True\n")
    return ws


@pytest.fixture
def pipeline_result(task, workspace):
    run_result = RunResult(
        passed=True, returncode=0, stdout="", summary="2 passed in 0.1s"
    )
    review = ReviewResult(
        verdict="APPROVED",
        summary="잘 구현됨",
        details="문제 없음",
        raw="VERDICT: APPROVED\nSUMMARY: 잘 구현됨\nDETAILS:\n문제 없음",
    )
    return PipelineResult(
        task=task,
        succeeded=True,
        test_result=run_result,
        review=review,
        test_files=["tests/test_auth.py"],
        impl_files=["src/auth.py"],
    )


def _make_proc(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """subprocess.CompletedProcess 목 객체 생성 헬퍼."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ── GitWorkflow.run() — 행복한 경로 ──────────────────────────────────────────


class TestGitWorkflowRun:
    @patch("orchestrator.git_workflow.subprocess.run")
    @patch.object(GitWorkflow, "_copy_workspace_to_repo")
    def test_run_returns_pr_url(self, mock_copy, mock_run, task, workspace, pipeline_result):
        """전체 워크플로우가 성공하면 PR URL 을 반환한다."""
        mock_copy.return_value = [Path("src/auth.py"), Path("tests/test_auth.py")]
        mock_run.side_effect = [
            _make_proc(stdout="main"),          # rev-parse
            _make_proc(stdout=""),              # branch --list (없음)
            _make_proc(stdout=""),              # checkout -b
            _make_proc(stdout=""),              # add
            _make_proc(stdout=""),              # commit
            _make_proc(stdout=""),              # push
            _make_proc(stdout="main"),          # checkout (복귀)
            _make_proc(stdout="https://github.com/user/repo/pull/1"),  # gh pr create
        ]

        git = GitWorkflow(workspace.path, base_branch="dev")
        url = git.run(task, workspace, pipeline_result)

        assert url == "https://github.com/user/repo/pull/1"

    @patch("orchestrator.git_workflow.subprocess.run")
    @patch.object(GitWorkflow, "_copy_workspace_to_repo")
    def test_run_calls_git_in_order(self, mock_copy, mock_run, task, workspace, pipeline_result):
        """git 명령어가 올바른 순서로 호출된다."""
        mock_copy.return_value = [Path("src/auth.py")]
        mock_run.side_effect = [
            _make_proc(stdout="main"),
            _make_proc(stdout=""),
            _make_proc(stdout=""),
            _make_proc(stdout=""),
            _make_proc(stdout=""),
            _make_proc(stdout=""),
            _make_proc(stdout="main"),
            _make_proc(stdout="https://github.com/user/repo/pull/1"),
        ]

        git = GitWorkflow(workspace.path, base_branch="dev")
        git.run(task, workspace, pipeline_result)

        calls = mock_run.call_args_list
        # 첫 번째 호출: rev-parse
        assert "rev-parse" in calls[0][0][0]
        # push 가 포함되어 있어야 함
        push_calls = [c for c in calls if "push" in str(c)]
        assert len(push_calls) == 1
        # gh pr create 가 마지막 호출
        last_cmd = calls[-1][0][0]
        assert "gh" in last_cmd
        assert "pr" in last_cmd
        assert "create" in last_cmd

    @patch("orchestrator.git_workflow.subprocess.run")
    @patch.object(GitWorkflow, "_copy_workspace_to_repo")
    def test_checkout_restores_original_branch(self, mock_copy, mock_run, task, workspace, pipeline_result):
        """PR 생성 후 원래 브랜치(main)로 복귀한다."""
        mock_copy.return_value = [Path("src/auth.py")]
        mock_run.side_effect = [
            _make_proc(stdout="main"),
            _make_proc(stdout=""),
            _make_proc(stdout=""),
            _make_proc(stdout=""),
            _make_proc(stdout=""),
            _make_proc(stdout=""),
            _make_proc(stdout="main"),
            _make_proc(stdout="https://github.com/user/repo/pull/1"),
        ]

        git = GitWorkflow(workspace.path, base_branch="dev")
        git.run(task, workspace, pipeline_result)

        calls = mock_run.call_args_list
        # checkout 복귀 명령에 'main' 이 있어야 함
        checkout_calls = [c for c in calls if "checkout" in str(c) and "main" in str(c)]
        assert len(checkout_calls) >= 1


# ── _create_branch ────────────────────────────────────────────────────────────


class TestCreateBranch:
    @patch("orchestrator.git_workflow.subprocess.run")
    def test_creates_new_branch(self, mock_run, tmp_path):
        """존재하지 않는 브랜치를 새로 생성한다."""
        mock_run.side_effect = [
            _make_proc(stdout=""),   # branch --list: 없음
            _make_proc(stdout=""),   # checkout -b
        ]

        git = GitWorkflow(tmp_path)
        git._create_branch("agent/task-001")

        calls = mock_run.call_args_list
        assert any("checkout" in str(c) and "-b" in str(c) for c in calls)

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_deletes_existing_branch_before_create(self, mock_run, tmp_path):
        """이미 존재하는 브랜치는 삭제 후 재생성한다."""
        mock_run.side_effect = [
            _make_proc(stdout="agent/task-001"),  # branch --list: 이미 있음
            _make_proc(stdout=""),                 # branch -D
            _make_proc(stdout=""),                 # checkout -b
        ]

        git = GitWorkflow(tmp_path)
        git._create_branch("agent/task-001")

        calls = mock_run.call_args_list
        # branch -D 호출이 있어야 함
        assert any("-D" in str(c) for c in calls)
        # checkout -b 도 있어야 함
        assert any("checkout" in str(c) and "-b" in str(c) for c in calls)

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_raises_on_checkout_failure(self, mock_run, tmp_path):
        """checkout -b 실패 시 GitWorkflowError 를 발생시킨다."""
        mock_run.side_effect = [
            _make_proc(stdout=""),                       # branch --list: 없음
            _make_proc(returncode=1, stderr="fatal"),    # checkout -b 실패
        ]

        git = GitWorkflow(tmp_path)
        with pytest.raises(GitWorkflowError):
            git._create_branch("agent/task-001")


# ── _copy_workspace_to_repo ───────────────────────────────────────────────────


class TestCopyWorkspaceToRepo:
    def test_copies_src_files_to_repo_root(self, tmp_path, task, workspace):
        """workspace/src/ 파일이 repo 루트에 복사된다."""
        repo = tmp_path / "repo"
        repo.mkdir()

        git = GitWorkflow(repo)
        changed = git._copy_workspace_to_repo(workspace)

        assert (repo / "auth.py").exists()
        assert any("auth.py" in str(f) for f in changed)

    def test_copies_test_files_to_repo_tests(self, tmp_path, task, workspace):
        """workspace/tests/ 파일이 repo/tests/ 에 복사된다."""
        repo = tmp_path / "repo"
        repo.mkdir()

        git = GitWorkflow(repo)
        changed = git._copy_workspace_to_repo(workspace)

        assert (repo / "tests" / "test_auth.py").exists()
        assert any("test_auth.py" in str(f) for f in changed)

    def test_returns_relative_paths(self, tmp_path, task, workspace):
        """반환된 경로는 repo 기준 상대 경로다."""
        repo = tmp_path / "repo"
        repo.mkdir()

        git = GitWorkflow(repo)
        changed = git._copy_workspace_to_repo(workspace)

        for path in changed:
            assert not path.is_absolute()

    def test_creates_nested_directories(self, tmp_path, task):
        """중첩된 디렉토리가 있어도 올바르게 복사된다."""
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        nested = ws.src_dir / "sub" / "module.py"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text("x = 1\n")

        repo = tmp_path / "repo"
        repo.mkdir()
        git = GitWorkflow(repo)
        git._copy_workspace_to_repo(ws)

        assert (repo / "sub" / "module.py").exists()
        ws.cleanup()

    def test_returns_empty_for_empty_workspace(self, tmp_path, task):
        """파일이 없는 workspace 는 빈 목록을 반환한다."""
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()

        repo = tmp_path / "repo"
        repo.mkdir()
        git = GitWorkflow(repo)
        changed = git._copy_workspace_to_repo(ws)

        assert changed == []
        ws.cleanup()


# ── _git_add / _git_commit / _git_push ───────────────────────────────────────


class TestGitOperations:
    @patch("orchestrator.git_workflow.subprocess.run")
    def test_git_add_calls_with_files(self, mock_run, tmp_path):
        """_git_add 는 변경된 파일 목록으로 `git add` 를 호출한다."""
        mock_run.return_value = _make_proc()

        git = GitWorkflow(tmp_path)
        files = [Path("src/auth.py"), Path("tests/test_auth.py")]
        git._git_add(files)

        args = mock_run.call_args[0][0]
        assert "add" in args
        assert "src/auth.py" in args

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_git_add_skips_empty_list(self, mock_run, tmp_path):
        """파일 목록이 비어있으면 git add 를 호출하지 않는다."""
        git = GitWorkflow(tmp_path)
        git._git_add([])

        mock_run.assert_not_called()

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_git_commit_contains_task_info(self, mock_run, task, tmp_path):
        """커밋 메시지에 태스크 제목과 ID 가 포함된다."""
        mock_run.return_value = _make_proc()

        git = GitWorkflow(tmp_path)
        git._git_commit(task)

        args = mock_run.call_args[0][0]
        msg = " ".join(args)
        assert "task-001" in msg
        assert "사용자 로그인 구현" in msg

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_git_push_targets_correct_branch(self, mock_run, tmp_path):
        """_git_push 는 올바른 브랜치로 push 한다."""
        mock_run.return_value = _make_proc()

        git = GitWorkflow(tmp_path)
        git._git_push("agent/task-001")

        args = mock_run.call_args[0][0]
        assert "push" in args
        assert "origin" in args
        assert "agent/task-001" in args

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_git_push_raises_on_failure(self, mock_run, tmp_path):
        """push 실패 시 GitWorkflowError 가 발생한다."""
        mock_run.return_value = _make_proc(returncode=1, stderr="remote: Permission denied")

        git = GitWorkflow(tmp_path)
        with pytest.raises(GitWorkflowError, match="push"):
            git._git_push("agent/task-001")


# ── _create_pr ────────────────────────────────────────────────────────────────


class TestCreatePR:
    @patch("orchestrator.git_workflow.subprocess.run")
    def test_returns_pr_url(self, mock_run, task, pipeline_result, tmp_path):
        """gh pr create 가 성공하면 URL 을 반환한다."""
        mock_run.return_value = _make_proc(stdout="https://github.com/user/repo/pull/42")

        git = GitWorkflow(tmp_path, base_branch="dev")
        url = git._create_pr(task, pipeline_result)

        assert url == "https://github.com/user/repo/pull/42"

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_uses_correct_base_branch(self, mock_run, task, pipeline_result, tmp_path):
        """--base 옵션에 지정된 base_branch 가 사용된다."""
        mock_run.return_value = _make_proc(stdout="https://github.com/user/repo/pull/1")

        git = GitWorkflow(tmp_path, base_branch="staging")
        git._create_pr(task, pipeline_result)

        args = mock_run.call_args[0][0]
        assert "--base" in args
        idx = args.index("--base")
        assert args[idx + 1] == "staging"

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_uses_task_branch_as_head(self, mock_run, task, pipeline_result, tmp_path):
        """--head 에 task.branch_name 이 사용된다."""
        mock_run.return_value = _make_proc(stdout="https://github.com/user/repo/pull/1")

        git = GitWorkflow(tmp_path)
        git._create_pr(task, pipeline_result)

        args = mock_run.call_args[0][0]
        assert "--head" in args
        idx = args.index("--head")
        assert args[idx + 1] == task.branch_name

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_raises_on_gh_failure(self, mock_run, task, pipeline_result, tmp_path):
        """gh pr create 실패 시 GitWorkflowError 가 발생한다."""
        mock_run.return_value = _make_proc(returncode=1, stderr="GraphQL error")

        git = GitWorkflow(tmp_path)
        with pytest.raises(GitWorkflowError):
            git._create_pr(task, pipeline_result)


# ── check_prerequisites ───────────────────────────────────────────────────────


class TestCheckPrerequisites:
    @patch("orchestrator.git_workflow.subprocess.run")
    def test_returns_empty_when_all_ok(self, mock_run, tmp_path):
        """모든 조건이 만족되면 빈 목록을 반환한다."""
        mock_run.side_effect = [
            _make_proc(),                           # git --version
            _make_proc(),                           # gh --version
            _make_proc(),                           # gh auth status
            _make_proc(stdout=""),                  # git status --porcelain (클린)
        ]

        issues = check_prerequisites(tmp_path)
        assert issues == []

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_reports_missing_git(self, mock_run, tmp_path):
        """git 명령어를 찾을 수 없으면 해당 메시지가 포함된다."""
        mock_run.side_effect = [
            _make_proc(returncode=1),               # git --version 실패
            _make_proc(),                           # gh --version
            _make_proc(),                           # gh auth status
            _make_proc(stdout=""),                  # git status
        ]

        issues = check_prerequisites(tmp_path)
        assert any("git" in issue.lower() for issue in issues)

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_reports_missing_gh(self, mock_run, tmp_path):
        """gh 명령어가 없으면 설치 안내 메시지가 포함된다."""
        mock_run.side_effect = [
            _make_proc(),                           # git --version
            _make_proc(returncode=1),               # gh --version 실패
            _make_proc(stdout=""),                  # git status
        ]

        issues = check_prerequisites(tmp_path)
        assert any("gh" in issue.lower() or "GitHub CLI" in issue for issue in issues)

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_reports_unauthenticated_gh(self, mock_run, tmp_path):
        """gh 는 있지만 인증이 안 된 경우 안내 메시지가 포함된다."""
        mock_run.side_effect = [
            _make_proc(),                           # git --version
            _make_proc(),                           # gh --version
            _make_proc(returncode=1),               # gh auth status 실패
            _make_proc(stdout=""),                  # git status
        ]

        issues = check_prerequisites(tmp_path)
        assert any("auth" in issue.lower() or "login" in issue.lower() for issue in issues)

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_reports_dirty_repo(self, mock_run, tmp_path):
        """uncommitted 변경이 있으면 해당 메시지가 포함된다."""
        mock_run.side_effect = [
            _make_proc(),                           # git --version
            _make_proc(),                           # gh --version
            _make_proc(),                           # gh auth status
            _make_proc(stdout=" M src/main.py\n"), # git status --porcelain (더티)
        ]

        issues = check_prerequisites(tmp_path)
        assert any("uncommitted" in issue.lower() or "커밋" in issue for issue in issues)

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_can_report_multiple_issues(self, mock_run, tmp_path):
        """여러 조건이 동시에 실패하면 모두 보고된다."""
        mock_run.side_effect = [
            _make_proc(returncode=1),               # git --version 실패
            _make_proc(returncode=1),               # gh --version 실패
            _make_proc(stdout=" M file.py\n"),      # git status (더티)
        ]

        issues = check_prerequisites(tmp_path)
        assert len(issues) >= 2


# ── _build_pr_body ────────────────────────────────────────────────────────────


class TestBuildPRBody:
    def test_contains_task_title_and_description(self, task, pipeline_result):
        body = _build_pr_body(task, pipeline_result)
        assert "사용자 로그인 구현" in body
        assert task.description in body

    def test_contains_acceptance_criteria(self, task, pipeline_result):
        body = _build_pr_body(task, pipeline_result)
        assert "올바른 자격증명" in body
        assert "잘못된 자격증명" in body

    def test_contains_test_summary(self, task, pipeline_result):
        body = _build_pr_body(task, pipeline_result)
        assert "2 passed" in body

    def test_contains_review_verdict(self, task, pipeline_result):
        body = _build_pr_body(task, pipeline_result)
        assert "APPROVED" in body
        assert "잘 구현됨" in body

    def test_contains_changed_files(self, task, pipeline_result):
        body = _build_pr_body(task, pipeline_result)
        assert "test_auth.py" in body
        assert "auth.py" in body

    def test_contains_auto_generated_marker(self, task, pipeline_result):
        body = _build_pr_body(task, pipeline_result)
        assert "AI Coding Agent" in body

    def test_includes_retry_info_when_retried(self, task, pipeline_result):
        task.retry_count = 2
        body = _build_pr_body(task, pipeline_result)
        assert "2회" in body

    def test_no_retry_info_when_first_attempt(self, task, pipeline_result):
        task.retry_count = 0
        body = _build_pr_body(task, pipeline_result)
        assert "재시도" not in body or "0회" not in body

    def test_handles_missing_review(self, task):
        result = PipelineResult(
            task=task,
            succeeded=True,
            test_result=RunResult(passed=True, returncode=0, stdout="", summary="1 passed"),
            review=None,
            test_files=[],
            impl_files=[],
        )
        body = _build_pr_body(task, result)
        assert "사용자 로그인 구현" in body

    def test_handles_missing_test_result(self, task):
        result = PipelineResult(
            task=task,
            succeeded=True,
            test_result=None,
            review=None,
            test_files=[],
            impl_files=[],
        )
        body = _build_pr_body(task, result)
        # 에러 없이 실행돼야 함
        assert "사용자 로그인 구현" in body

    def test_changes_requested_verdict_shows_warning_icon(self, task):
        review = ReviewResult(
            verdict="CHANGES_REQUESTED",
            summary="수정 필요",
            details="보안 문제 있음",
            raw="",
        )
        result = PipelineResult(
            task=task,
            succeeded=True,
            test_result=RunResult(passed=True, returncode=0, stdout="", summary="1 passed"),
            review=review,
            test_files=[],
            impl_files=[],
        )
        body = _build_pr_body(task, result)
        assert "CHANGES_REQUESTED" in body

    def test_includes_failed_tests_when_present(self, task):
        run_result = RunResult(
            passed=False,
            returncode=1,
            stdout="",
            summary="1 failed",
            failed_tests=["test_login_invalid"],
        )
        result = PipelineResult(
            task=task,
            succeeded=True,
            test_result=run_result,
            review=None,
            test_files=[],
            impl_files=[],
        )
        body = _build_pr_body(task, result)
        assert "test_login_invalid" in body


# ── GitWorkflowError ──────────────────────────────────────────────────────────


class TestGitWorkflowError:
    def test_is_exception(self):
        err = GitWorkflowError("git push 실패")
        assert isinstance(err, Exception)

    def test_message_preserved(self):
        err = GitWorkflowError("fatal: remote rejected")
        assert "fatal" in str(err)


# ── _safe_checkout ────────────────────────────────────────────────────────────


class TestSafeCheckout:
    @patch("orchestrator.git_workflow.subprocess.run")
    def test_does_not_raise_on_failure(self, mock_run, tmp_path):
        """_safe_checkout 는 실패해도 예외를 발생시키지 않는다."""
        mock_run.return_value = _make_proc(returncode=1, stderr="error")

        git = GitWorkflow(tmp_path)
        git._safe_checkout("main")  # 예외가 없어야 함

    @patch("orchestrator.git_workflow.subprocess.run")
    @patch.object(GitWorkflow, "_copy_workspace_to_repo")
    def test_checkout_fallback_on_pipeline_error(self, mock_copy, mock_run, task, workspace, pipeline_result):
        """git 중간 단계 실패 시 원래 브랜치로 복귀를 시도한다."""
        mock_copy.return_value = [Path("src/auth.py")]
        mock_run.side_effect = [
            _make_proc(stdout="main"),          # rev-parse
            _make_proc(stdout=""),              # branch --list
            _make_proc(stdout=""),              # checkout -b
            _make_proc(stdout=""),              # add
            _make_proc(returncode=1, stderr="rejected"),  # commit 실패
            _make_proc(stdout="main"),          # safe_checkout 복귀
        ]

        git = GitWorkflow(workspace.path, base_branch="dev")

        with pytest.raises(GitWorkflowError):
            git.run(task, workspace, pipeline_result)

        # safe_checkout 가 호출되었는지 확인 (마지막에 checkout main)
        calls = mock_run.call_args_list
        checkout_calls = [c for c in calls if "checkout" in str(c) and "-b" not in str(c)]
        assert len(checkout_calls) >= 1
