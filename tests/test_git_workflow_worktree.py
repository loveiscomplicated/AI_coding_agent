from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from docker.runner import RunResult
from orchestrator.git_workflow import GitWorkflow, GitWorkflowError
from orchestrator.pipeline import PipelineResult, ReviewResult
from orchestrator.task import Task
from orchestrator.workspace import WorkspaceManager


@pytest.fixture
def task() -> Task:
    return Task(
        id="task-101",
        title="worktree regression guard",
        description="desc",
        acceptance_criteria=["ok"],
        target_files=["src/auth.py"],
    )


@pytest.fixture
def workspace(tmp_path: Path, task: Task) -> WorkspaceManager:
    ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
    ws.create()
    (ws.src_dir / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    (ws.tests_dir / "test_auth.py").write_text("def test_login():\n    assert True\n", encoding="utf-8")
    return ws


@pytest.fixture
def pipeline_result(task: Task) -> PipelineResult:
    return PipelineResult(
        task=task,
        succeeded=True,
        test_result=RunResult(passed=True, returncode=0, stdout="", summary="1 passed"),
        review=ReviewResult(verdict="APPROVED", summary="ok", details="ok", raw=""),
        test_files=["tests/test_auth.py"],
        impl_files=["src/auth.py"],
    )


def test_run_no_push_uses_worktree_and_skips_push_and_pr(task, workspace, pipeline_result, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    seen_args: list[list[str]] = []

    def _fake_wt_git_checked(_wt_path: Path, args: list[str]):
        seen_args.append(args)

    with (
        patch.object(GitWorkflow, "_create_pr") as mock_create_pr,
        patch.object(GitWorkflow, "_create_worktree"),
        patch.object(GitWorkflow, "_remove_worktree"),
        patch.object(GitWorkflow, "_wt_git_checked", side_effect=_fake_wt_git_checked),
    ):
        git = GitWorkflow(repo, base_branch="main")
        pr = git.run(task, workspace, pipeline_result, no_push=True)

    assert pr == ""
    assert any(a and a[0] == "add" for a in seen_args)
    assert any(a and a[0] == "commit" for a in seen_args)
    assert not any(a and a[0] == "push" for a in seen_args)
    mock_create_pr.assert_not_called()


def test_run_removes_worktree_when_commit_fails(task, workspace, pipeline_result, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    removed_paths: list[Path] = []
    call_count = {"n": 0}

    def _fake_wt_git_checked(_wt_path: Path, args: list[str]):
        call_count["n"] += 1
        if args and args[0] == "commit":
            raise GitWorkflowError("commit failed")

    def _fake_remove(wt_path: Path):
        removed_paths.append(wt_path)

    with (
        patch.object(GitWorkflow, "_create_worktree"),
        patch.object(GitWorkflow, "_wt_git_checked", side_effect=_fake_wt_git_checked),
        patch.object(GitWorkflow, "_remove_worktree", side_effect=_fake_remove),
    ):
        git = GitWorkflow(repo, base_branch="main")
        with pytest.raises(GitWorkflowError):
            git.run(task, workspace, pipeline_result, no_push=True)

    assert removed_paths, "실패 시에도 worktree 정리가 호출되어야 함"


def test_run_creates_worktree_under_agent_workspace(task, workspace, pipeline_result, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    captured_wt_paths: list[Path] = []

    def _capture_create(branch: str, wt_path: Path):
        assert branch == task.branch_name
        captured_wt_paths.append(wt_path)

    with (
        patch.object(GitWorkflow, "_create_worktree", side_effect=_capture_create),
        patch.object(GitWorkflow, "_wt_git_checked"),
        patch.object(GitWorkflow, "_remove_worktree"),
    ):
        git = GitWorkflow(repo, base_branch="main")
        git.run(task, workspace, pipeline_result, no_push=True)

    assert captured_wt_paths, "worktree 생성 경로가 캡처되어야 함"
    wt_path = captured_wt_paths[0]
    assert ".agent-workspace" in wt_path.parts
    assert "worktrees" in wt_path.parts
