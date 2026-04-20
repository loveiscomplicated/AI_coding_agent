"""
tests/test_git_workflow.py

orchestrator/git_workflow.py 단위 테스트.
현재 worktree 기반 구현 기준으로 검증한다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
from orchestrator.task import Task
from orchestrator.workspace import WorkspaceManager


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
    (ws.src_dir / "auth.py").write_text("def login(email, password): pass\n", encoding="utf-8")
    (ws.tests_dir / "test_auth.py").write_text("def test_login(): assert True\n", encoding="utf-8")
    return ws


@pytest.fixture
def pipeline_result(task):
    run_result = RunResult(passed=True, returncode=0, stdout="", summary="2 passed in 0.1s")
    review = ReviewResult(
        verdict="APPROVED",
        summary="잘 구현됨",
        details="문제 없음",
        raw="",
    )
    return PipelineResult(
        task=task,
        succeeded=True,
        test_result=run_result,
        review=review,
        test_files=["tests/test_auth.py"],
        impl_files=["src/auth.py"],
    )


def _proc(returncode: int = 0, stdout: str = "", stderr: str = ""):
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


class TestCopyWorkspaceToWorktree:
    def test_copies_src_and_tests(self, tmp_path, workspace):
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        repo.mkdir()
        wt.mkdir()

        git = GitWorkflow(repo)
        changed = git._copy_workspace_to_worktree(workspace, wt)

        assert (wt / "auth.py").exists()
        assert (wt / "tests" / "test_auth.py").exists()
        assert Path("auth.py") in changed
        assert Path("tests/test_auth.py") in changed


class TestRunWorkflow:
    @patch.object(GitWorkflow, "_create_pr")
    @patch.object(GitWorkflow, "_remove_worktree")
    @patch.object(GitWorkflow, "_wt_git_checked")
    @patch.object(GitWorkflow, "_copy_workspace_to_worktree")
    @patch.object(GitWorkflow, "_create_worktree")
    def test_run_returns_pr_url(
        self,
        mock_create_wt,
        mock_copy,
        mock_wt_git,
        mock_remove_wt,
        mock_create_pr,
        task,
        workspace,
        pipeline_result,
        tmp_path,
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        mock_copy.return_value = [Path("auth.py"), Path("tests/test_auth.py")]
        mock_create_pr.return_value = "https://github.com/acme/repo/pull/1"

        git = GitWorkflow(repo, base_branch="dev")
        url = git.run(task, workspace, pipeline_result)

        assert url == "https://github.com/acme/repo/pull/1"
        mock_create_wt.assert_called_once()
        assert mock_wt_git.call_count == 3  # add, commit, push
        mock_remove_wt.assert_called_once()

    @patch.object(GitWorkflow, "_create_pr")
    @patch.object(GitWorkflow, "_remove_worktree")
    @patch.object(GitWorkflow, "_wt_git_checked")
    @patch.object(GitWorkflow, "_copy_workspace_to_worktree")
    @patch.object(GitWorkflow, "_create_worktree")
    def test_run_no_push_skips_push_and_pr(
        self,
        mock_create_wt,
        mock_copy,
        mock_wt_git,
        mock_remove_wt,
        mock_create_pr,
        task,
        workspace,
        pipeline_result,
        tmp_path,
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        mock_copy.return_value = [Path("auth.py")]

        git = GitWorkflow(repo, base_branch="dev")
        url = git.run(task, workspace, pipeline_result, no_push=True)

        assert url == ""
        assert mock_wt_git.call_count == 2  # add, commit
        mock_create_pr.assert_not_called()
        mock_remove_wt.assert_called_once()

    @patch.object(GitWorkflow, "_remove_worktree")
    @patch.object(GitWorkflow, "_wt_git_checked")
    @patch.object(GitWorkflow, "_copy_workspace_to_worktree")
    @patch.object(GitWorkflow, "_create_worktree")
    def test_run_always_removes_worktree_on_error(
        self,
        mock_create_wt,
        mock_copy,
        mock_wt_git,
        mock_remove_wt,
        task,
        workspace,
        pipeline_result,
        tmp_path,
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        mock_copy.return_value = [Path("auth.py")]
        mock_wt_git.side_effect = [None, GitWorkflowError("commit failed")]

        git = GitWorkflow(repo, base_branch="dev")
        with pytest.raises(GitWorkflowError):
            git.run(task, workspace, pipeline_result)

        mock_remove_wt.assert_called_once()


class TestCreateWorktree:
    @patch.object(GitWorkflow, "_git")
    def test_create_worktree_fallback_to_origin_base(self, mock_git, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        wt = tmp_path / "wt"
        mock_git.side_effect = [
            _proc(returncode=1, stderr="unknown ref"),  # base_branch 실패
            _proc(returncode=0),  # origin/base_branch 성공
        ]

        git = GitWorkflow(repo, base_branch="dev")
        git._create_worktree("agent/task-001", wt)

        first_call = mock_git.call_args_list[0].args[0]
        second_call = mock_git.call_args_list[1].args[0]
        assert "dev" in first_call
        assert "origin/dev" in second_call

    @patch.object(GitWorkflow, "_git")
    def test_create_worktree_raises_when_all_starts_fail(self, mock_git, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        wt = tmp_path / "wt"
        mock_git.side_effect = [
            _proc(returncode=1, stderr="fail-1"),
            _proc(returncode=1, stderr="fail-2"),
            _proc(returncode=1, stderr="fail-3"),
        ]

        git = GitWorkflow(repo, base_branch="dev")
        with pytest.raises(GitWorkflowError):
            git._create_worktree("agent/task-001", wt)


class TestCreatePR:
    @patch("orchestrator.git_workflow.subprocess.run")
    @patch.object(GitWorkflow, "_resolve_pr_base")
    def test_create_pr_returns_url(self, mock_resolve_base, mock_run, task, pipeline_result, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        mock_resolve_base.return_value = "dev"
        mock_run.return_value = _proc(stdout="https://github.com/org/repo/pull/42")

        git = GitWorkflow(repo, base_branch="dev")
        url = git._create_pr(task, pipeline_result)
        assert url == "https://github.com/org/repo/pull/42"

    @patch("orchestrator.git_workflow.subprocess.run")
    @patch.object(GitWorkflow, "_resolve_pr_base")
    def test_create_pr_uses_resolved_base(self, mock_resolve_base, mock_run, task, pipeline_result, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        mock_resolve_base.return_value = "agent/task-000"
        mock_run.return_value = _proc(stdout="https://github.com/org/repo/pull/42")

        git = GitWorkflow(repo, base_branch="dev")
        git._create_pr(task, pipeline_result)

        cmd = mock_run.call_args.args[0]
        idx = cmd.index("--base")
        assert cmd[idx + 1] == "agent/task-000"


class TestResolvePRBase:
    @patch.object(GitWorkflow, "_is_merged")
    @patch.object(GitWorkflow, "_remote_branch_exists")
    def test_returns_dependency_branch_if_not_merged(self, mock_exists, mock_merged, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        task = Task(
            id="task-002",
            title="next",
            description="",
            acceptance_criteria=["x"],
            target_files=[],
            depends_on=["task-001"],
        )
        mock_exists.return_value = True
        mock_merged.return_value = False

        git = GitWorkflow(repo, base_branch="dev")
        assert git._resolve_pr_base(task) == "agent/task-001"


class TestPrerequisites:
    @patch("orchestrator.git_workflow.subprocess.run")
    def test_returns_empty_when_ok_and_clean(self, mock_run, tmp_path):
        mock_run.side_effect = [
            _proc(),  # git --version
            _proc(),  # gh --version
            _proc(),  # gh auth status
            _proc(stdout=""),  # git status --porcelain
        ]
        assert check_prerequisites(tmp_path) == []

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_reports_dirty_repo(self, mock_run, tmp_path):
        mock_run.side_effect = [
            _proc(),
            _proc(),
            _proc(),
            _proc(stdout=" M src/main.py\n"),
        ]
        issues = check_prerequisites(tmp_path)
        assert any("uncommitted" in i.lower() or "커밋" in i for i in issues)

    @patch("orchestrator.git_workflow.subprocess.run")
    def test_reports_missing_gh(self, mock_run, tmp_path):
        mock_run.side_effect = [
            _proc(),  # git --version
            _proc(returncode=1),  # gh --version
            _proc(stdout=""),  # git status
        ]
        issues = check_prerequisites(tmp_path)
        assert any("gh" in i.lower() for i in issues)


class TestBuildPRBody:
    def test_build_body_contains_key_sections(self, task, pipeline_result):
        body = _build_pr_body(task, pipeline_result)
        assert task.title in body
        assert "수락 기준" in body
        assert "테스트 결과" in body
        assert "코드 리뷰" in body
        assert "AI Coding Agent Pipeline" in body

    def test_unclosed_code_fence_in_details_is_balanced(self, task):
        """reviewer details 에 닫히지 않은 ``` 가 들어와도 footer 가
        code block 으로 먹히지 않도록 balancer 가 닫아야 한다."""
        run_result = RunResult(passed=True, returncode=0, stdout="",
                               summary="1 passed")
        review = ReviewResult(
            verdict="CHANGES_REQUESTED",
            summary="미닫힘 코드펜스 주입",
            details="다음 코드가 문제:\n```python\ndef bad():\n    pass",
            raw="",
        )
        result = PipelineResult(
            task=task, succeeded=False, test_result=run_result,
            review=review, test_files=[], impl_files=[],
        )
        body = _build_pr_body(task, result)
        assert body.count("```") % 2 == 0, (
            "triple-backtick 개수가 홀수 — 미닫힘 코드펜스가 뒤 섹션을 오염시킨다"
        )
        # footer 가 살아있어야 한다
        assert "AI Coding Agent Pipeline" in body

    def test_approved_with_suggestions_does_not_duplicate_details(self, task):
        """APPROVED_WITH_SUGGESTIONS 에서 details 는 'Reviewer Suggestions'
        섹션에만 포함돼야 하고, '## 코드 리뷰' 섹션은 verdict+summary 요약만
        유지한다."""
        run_result = RunResult(passed=True, returncode=0, stdout="",
                               summary="4 passed")
        unique_marker = "UNIQUE_SUGGESTION_MARKER_XYZ123"
        review = ReviewResult(
            verdict="APPROVED_WITH_SUGGESTIONS",
            summary="기능 충족, 제안 있음",
            details=f"## 개선 제안\n- {unique_marker}: 이름 변경 고려",
            raw="",
        )
        result = PipelineResult(
            task=task, succeeded=True, test_result=run_result,
            review=review, test_files=[], impl_files=[],
        )
        body = _build_pr_body(task, result)
        # 피드백은 정확히 한 번만 등장해야 한다
        assert body.count(unique_marker) == 1, (
            f"중복 포함됨 — body.count('{unique_marker}') = {body.count(unique_marker)}"
        )
        assert "Reviewer Suggestions (non-blocking)" in body
        # 코드 리뷰 섹션에는 verdict+summary 만
        assert "APPROVED_WITH_SUGGESTIONS" in body
        assert "기능 충족, 제안 있음" in body

    def test_plain_approved_without_suggestions_section(self, task):
        """단순 APPROVED 는 'Reviewer Suggestions' 섹션을 만들지 않는다."""
        run_result = RunResult(passed=True, returncode=0, stdout="",
                               summary="4 passed")
        review = ReviewResult(
            verdict="APPROVED",
            summary="잘 됨",
            details="상세 리뷰 내용",
            raw="",
        )
        result = PipelineResult(
            task=task, succeeded=True, test_result=run_result,
            review=review, test_files=[], impl_files=[],
        )
        body = _build_pr_body(task, result)
        assert "Reviewer Suggestions (non-blocking)" not in body
        # APPROVED 의 경우 details 는 여전히 코드 리뷰 섹션에 표시
        assert "상세 리뷰 내용" in body


class TestBuildPRBodyDepInjection:
    """PR body 에 dep injection 검증 결과가 항상 포함돼야 한다.

    verify_dep_injection.py 의 판정 규칙(depends_on vs dep_files_injected) 과
    동일한 결과를 섹션으로 고정 노출하기 위한 회귀 가드.
    """

    def _make_result(self, task: Task, *, injected: int) -> PipelineResult:
        from orchestrator.pipeline import PipelineMetrics
        return PipelineResult(
            task=task,
            succeeded=True,
            test_result=RunResult(passed=True, returncode=0, stdout="", summary=""),
            review=ReviewResult(verdict="APPROVED", summary="ok", details="", raw=""),
            test_files=[],
            impl_files=[],
            metrics=PipelineMetrics(dep_files_injected=injected),
        )

    def test_no_deps_section_is_informational(self, task):
        assert task.depends_on == []
        body = _build_pr_body(task, self._make_result(task, injected=0))
        assert "의존성 주입 검증" in body
        assert "검증 대상 아님" in body

    def test_deps_with_zero_injected_flags_warning(self):
        task = Task(
            id="task-002",
            title="x",
            description="",
            acceptance_criteria=[],
            target_files=["src/a.py"],
            depends_on=["task-001"],
        )
        body = _build_pr_body(task, self._make_result(task, injected=0))
        assert "의존성 주입 검증" in body
        assert "⚠️" in body
        assert "dep_files_injected=0" in body
        assert "task-001" in body

    def test_deps_with_nonzero_injected_shows_ok(self):
        task = Task(
            id="task-002",
            title="x",
            description="",
            acceptance_criteria=[],
            target_files=["src/a.py"],
            depends_on=["task-001"],
        )
        body = _build_pr_body(task, self._make_result(task, injected=3))
        assert "의존성 주입 검증" in body
        assert "✅" in body
        assert "dep_files_injected=3" in body

    def test_section_present_even_in_minimal_pr(self, task):
        """review/test_result 여부와 무관하게 섹션이 항상 노출돼야 한다."""
        from orchestrator.pipeline import PipelineMetrics
        result = PipelineResult(
            task=task, succeeded=True, test_result=None, review=None,
            test_files=[], impl_files=[], metrics=PipelineMetrics(),
        )
        body = _build_pr_body(task, result)
        assert "의존성 주입 검증" in body


# ── PR body: collect-only 게이트 결과 노출 ────────────────────────────────────


class TestBuildPRBodyCollectGate:
    """
    RunResult.failure_reason 이 [NO_TESTS_COLLECTED] / [COLLECTION_ERROR]
    인 경우 PR body 에 증빙 섹션이 들어가야 한다 (task-025 재현 근거).
    """

    def _result(self, task, *, failure_reason: str, stdout: str = "") -> PipelineResult:
        from orchestrator.pipeline import PipelineMetrics
        return PipelineResult(
            task=task,
            succeeded=False,
            failure_reason=failure_reason,
            test_result=RunResult(
                passed=False,
                returncode=71 if failure_reason == "[NO_TESTS_COLLECTED]" else 70,
                stdout=stdout,
                summary="(n/a)",
                failure_reason=failure_reason,
            ),
            review=None,
            test_files=[], impl_files=[],
            metrics=PipelineMetrics(),
        )

    def test_no_tests_collected_adds_warning_section(self, task):
        body = _build_pr_body(
            task,
            self._result(task, failure_reason="[NO_TESTS_COLLECTED]"),
        )
        assert "## 수집 게이트" in body
        assert "0 tests collected" in body
        assert "⚠️" in body

    def test_collection_error_adds_error_section_with_snippet(self, task):
        stdout = (
            "---COLLECTION_ERROR---\n"
            "ERROR collecting tests/test_x.py\n"
            "ImportError: No module named 'missing_pkg'\n"
        )
        body = _build_pr_body(
            task,
            self._result(
                task, failure_reason="[COLLECTION_ERROR]", stdout=stdout,
            ),
        )
        assert "## 수집 게이트" in body
        assert "Collection error" in body
        assert "⛔" in body
        assert "ImportError" in body  # 에러 스니펫 포함

    def test_no_collect_gate_section_when_failure_reason_empty(self, task, pipeline_result):
        """정상 통과 케이스에서는 수집 게이트 섹션 없음 (노이즈 방지)."""
        body = _build_pr_body(task, pipeline_result)
        assert "## 수집 게이트" not in body
