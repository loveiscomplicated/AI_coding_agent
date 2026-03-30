"""
tests/test_workspace.py

orchestrator/workspace.py 단위 테스트.

실행:
    pytest tests/test_workspace.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from orchestrator.task import Task
from orchestrator.workspace import WorkspaceManager


# ── 픽스처 ────────────────────────────────────────────────────────────────────


@pytest.fixture
def simple_task():
    return Task(
        id="task-001",
        title="테스트 태스크",
        description="테스트용",
        acceptance_criteria=["기준 1"],
        target_files=["src/auth.py"],
    )


@pytest.fixture
def repo_with_files(tmp_path):
    """target_files 가 있는 가짜 repo."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def login(): pass\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest>=9.0\n", encoding="utf-8")
    return tmp_path


# ── create / cleanup ──────────────────────────────────────────────────────────


class TestWorkspaceCreation:
    def test_creates_src_and_tests_dirs(self, simple_task, repo_with_files):
        ws = WorkspaceManager(simple_task, repo_with_files, base_dir=repo_with_files / "workspaces")
        ws.create()
        try:
            assert ws.src_dir.exists()
            assert ws.tests_dir.exists()
        finally:
            ws.cleanup()

    def test_copies_target_file(self, simple_task, repo_with_files):
        ws = WorkspaceManager(simple_task, repo_with_files, base_dir=repo_with_files / "workspaces")
        ws.create()
        try:
            dest = ws.src_dir / "src" / "auth.py"
            assert dest.exists()
            assert "login" in dest.read_text()
        finally:
            ws.cleanup()

    def test_copies_requirements_if_present(self, simple_task, repo_with_files):
        ws = WorkspaceManager(simple_task, repo_with_files, base_dir=repo_with_files / "workspaces")
        ws.create()
        try:
            assert (ws.path / "requirements.txt").exists()
        finally:
            ws.cleanup()

    def test_skips_missing_target_file_without_error(self, tmp_path):
        task = Task(
            id="x", title="t", description="d",
            acceptance_criteria=["c"],
            target_files=["nonexistent.py"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()  # should not raise
        try:
            assert ws.src_dir.exists()
        finally:
            ws.cleanup()

    def test_create_is_idempotent(self, simple_task, repo_with_files):
        ws = WorkspaceManager(simple_task, repo_with_files, base_dir=repo_with_files / "workspaces")
        ws.create()
        path1 = ws.path
        ws.create()  # second call is no-op
        assert ws.path == path1
        ws.cleanup()

    def test_cleanup_removes_directory(self, simple_task, repo_with_files):
        ws = WorkspaceManager(simple_task, repo_with_files, base_dir=repo_with_files / "workspaces")
        ws.create()
        path = ws.path
        ws.cleanup()
        assert not path.exists()

    def test_path_raises_before_create(self, simple_task, tmp_path):
        ws = WorkspaceManager(simple_task, tmp_path)
        with pytest.raises(RuntimeError, match="create"):
            _ = ws.path


# ── 컨텍스트 매니저 ───────────────────────────────────────────────────────────


class TestWorkspaceContextManager:
    def test_cleans_up_on_success(self, simple_task, repo_with_files):
        base = repo_with_files / "workspaces"
        with WorkspaceManager(simple_task, repo_with_files, base_dir=base) as ws:
            path = ws.path
            assert path.exists()
        assert not path.exists()

    def test_preserves_on_failure_by_default(self, simple_task, repo_with_files):
        base = repo_with_files / "workspaces"
        path = None
        try:
            with WorkspaceManager(simple_task, repo_with_files, base_dir=base) as ws:
                path = ws.path
                raise ValueError("의도적 실패")
        except ValueError:
            pass
        assert path is not None and path.exists()
        # 정리
        import shutil
        shutil.rmtree(path)

    def test_cleans_up_on_failure_when_keep_false(self, simple_task, repo_with_files):
        base = repo_with_files / "workspaces"
        path = None
        try:
            with WorkspaceManager(
                simple_task, repo_with_files, keep_on_failure=False, base_dir=base
            ) as ws:
                path = ws.path
                raise ValueError("의도적 실패")
        except ValueError:
            pass
        assert path is not None and not path.exists()


# ── list_files ────────────────────────────────────────────────────────────────


class TestListFiles:
    def test_lists_copied_files(self, simple_task, repo_with_files):
        ws = WorkspaceManager(simple_task, repo_with_files, base_dir=repo_with_files / "workspaces")
        ws.create()
        try:
            files = ws.list_files()
            assert any("auth.py" in f for f in files)
        finally:
            ws.cleanup()

    def test_list_test_files_empty_initially(self, simple_task, repo_with_files):
        ws = WorkspaceManager(simple_task, repo_with_files, base_dir=repo_with_files / "workspaces")
        ws.create()
        try:
            assert ws.list_test_files() == []
        finally:
            ws.cleanup()

    def test_list_src_files_contains_target(self, simple_task, repo_with_files):
        ws = WorkspaceManager(simple_task, repo_with_files, base_dir=repo_with_files / "workspaces")
        ws.create()
        try:
            src_files = ws.list_src_files()
            assert any("auth.py" in f for f in src_files)
        finally:
            ws.cleanup()

    def test_list_files_returns_empty_before_create(self, simple_task, tmp_path):
        ws = WorkspaceManager(simple_task, tmp_path)
        # _path is None → should return []
        assert ws.list_files() == []
