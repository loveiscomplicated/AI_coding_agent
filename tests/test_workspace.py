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

    def test_creates_empty_skeleton_for_missing_target_file(self, tmp_path):
        """신규 파일 태스크: repo 에 없는 target_file 은 빈 스켈레톤으로 선주입."""
        task = Task(
            id="x", title="t", description="d",
            acceptance_criteria=["c"],
            target_files=["new_module.py", "sub/nested.py"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            assert (ws.src_dir / "new_module.py").exists()
            assert (ws.src_dir / "new_module.py").stat().st_size == 0
            assert (ws.src_dir / "sub" / "nested.py").exists()
            assert (ws.src_dir / "sub" / "nested.py").stat().st_size == 0
        finally:
            ws.cleanup()

    def test_missing_or_empty_target_files_detects_skeleton(self, tmp_path):
        """Implementer 가 아무 것도 안 쓰면 skeleton 그대로 → 전부 missing 반환."""
        task = Task(
            id="x", title="t", description="d",
            acceptance_criteria=["c"],
            target_files=["a.py", "b.py"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            # 초기 상태: 둘 다 빈 스켈레톤
            assert ws.missing_or_empty_target_files() == ["a.py", "b.py"]
            # a.py 만 내용 채움
            (ws.src_dir / "a.py").write_text("x = 1\n")
            assert ws.missing_or_empty_target_files() == ["b.py"]
            # b.py 도 채움
            (ws.src_dir / "b.py").write_text("y = 2\n")
            assert ws.missing_or_empty_target_files() == []
        finally:
            ws.cleanup()

    def test_missing_or_empty_detects_deleted_target(self, tmp_path):
        """Implementer 가 실수로 파일을 삭제한 경우도 missing 으로 잡힌다."""
        task = Task(
            id="x", title="t", description="d",
            acceptance_criteria=["c"],
            target_files=["a.py"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            (ws.src_dir / "a.py").unlink()
            assert ws.missing_or_empty_target_files() == ["a.py"]
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
