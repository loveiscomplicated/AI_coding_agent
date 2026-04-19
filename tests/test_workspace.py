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

from orchestrator.task import Task, TaskStatus
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
        """target_files 의 선행 'src/' 는 workspace.src_dir 가 흡수하므로
        파일은 src_dir 바로 아래에 놓인다 (src/src/ 중첩 X).
        """
        ws = WorkspaceManager(simple_task, repo_with_files, base_dir=repo_with_files / "workspaces")
        ws.create()
        try:
            dest = ws.src_dir / "auth.py"
            assert dest.exists()
            assert "login" in dest.read_text()
            # 회귀 가드: 중첩된 src/src/ 경로는 생성되지 않아야 한다
            assert not (ws.src_dir / "src" / "auth.py").exists()
        finally:
            ws.cleanup()

    def test_strips_src_prefix_for_python_target(self, tmp_path):
        """target_files=['src/foo.py'] → workspace 안 실제 파일은 src/foo.py
        (중첩된 src/src/foo.py 가 아니어야 한다).
        """
        task = Task(
            id="x", title="t", description="d",
            acceptance_criteria=["c"],
            target_files=["src/foo.py", "src/models/user.py"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            assert (ws.src_dir / "foo.py").exists()
            assert (ws.src_dir / "models" / "user.py").exists()
            assert not (ws.src_dir / "src" / "foo.py").exists()
            # missing_or_empty_target_files 도 같은 경로 해석을 사용해야 한다
            (ws.src_dir / "foo.py").write_text("x = 1\n")
            (ws.src_dir / "models" / "user.py").write_text("y = 2\n")
            assert ws.missing_or_empty_target_files() == []
        finally:
            ws.cleanup()

    def test_preserves_paths_without_src_prefix(self, tmp_path):
        """선행 'src/' 가 없는 경로 (예: Kotlin 의 app/src/main/...) 는 그대로 유지.
        한 단계만 떼므로, src/ 안의 다른 src/ 같은 의도된 중첩은 보존된다.
        """
        task = Task(
            id="x", title="t", description="d",
            acceptance_criteria=["c"],
            target_files=["app/src/main/Foo.kt"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            assert (ws.src_dir / "app" / "src" / "main" / "Foo.kt").exists()
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

    def test_list_test_files_contains_preinjected_skeleton(self, simple_task, repo_with_files):
        """workspace 생성 시 target_files 마다 tests/ 스켈레톤이 선주입된다.

        (회귀 가드: 이전에는 tests/ 가 비어있었으나, TestWriter 가 파일 생성 없이
        종료하는 패턴을 차단하기 위해 create() 시 스켈레톤을 선주입한다.)
        """
        ws = WorkspaceManager(simple_task, repo_with_files, base_dir=repo_with_files / "workspaces")
        ws.create()
        try:
            tests = ws.list_test_files()
            assert tests == ["tests/test_auth.py"]
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


# ── 선행 태스크 산출물 주입 ──────────────────────────────────────────────────


class TestDependencyContextInjection:
    @pytest.mark.parametrize(
        ("dep_rel_path", "expected_rel_path"),
        [
            ("src/foo.py", "foo.py"),
            ("foo.py", "foo.py"),
        ],
    )
    def test_inject_dependency_context_copies_files_under_src_dir(
        self, tmp_path, dep_rel_path, expected_rel_path
    ):
        """선행 태스크 target_files 는 선행 src/ 한 단계만 제거해 workspace/src 에 주입한다."""
        task = Task(
            id="task-main", title="main", description="d",
            acceptance_criteria=["c"],
            target_files=[],
        )
        dep = Task(
            id="task-dep", title="dep", description="d",
            acceptance_criteria=["c"],
            target_files=[dep_rel_path],
            status=TaskStatus.DONE,
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            ws._read_from_branch = lambda branch, rel_path: (
                "def injected():\n    return 'ok'\n"
                if rel_path == dep_rel_path else None
            )

            ws.inject_dependency_context([dep])

            dest = ws.src_dir / expected_rel_path
            assert dest.exists()
            assert "def injected" in dest.read_text(encoding="utf-8")
            assert not (ws.src_dir / "src" / expected_rel_path).exists()
        finally:
            ws.cleanup()

    def test_inject_dependency_context_preserves_nested_paths(self, tmp_path):
        task = Task(
            id="task-main", title="main", description="d",
            acceptance_criteria=["c"],
            target_files=[],
        )
        dep = Task(
            id="task-dep", title="dep", description="d",
            acceptance_criteria=["c"],
            target_files=["src/models/user.py"],
            status=TaskStatus.DONE,
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            ws._read_from_branch = lambda branch, rel_path: (
                "class User:\n    pass\n"
                if rel_path == "src/models/user.py" else None
            )

            ws.inject_dependency_context([dep])

            assert (ws.src_dir / "models" / "user.py").exists()
            assert not (ws.src_dir / "src" / "models" / "user.py").exists()
        finally:
            ws.cleanup()

    def test_inject_dependency_context_writes_dependency_artifacts_summary(self, tmp_path):
        task = Task(
            id="task-main", title="main", description="d",
            acceptance_criteria=["c"],
            target_files=[],
        )
        dep = Task(
            id="task-dep", title="dep title", description="d",
            acceptance_criteria=["c"],
            target_files=["src/models/user.py"],
            status=TaskStatus.DONE,
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            ws._read_from_branch = lambda branch, rel_path: (
                "class User:\n"
                "    def full_name(self, first: str, last: str) -> str:\n"
                "        return first + last\n\n"
                "def helper(x: int) -> int:\n"
                "    return x\n"
                if rel_path == "src/models/user.py" else None
            )

            ws.inject_dependency_context([dep])

            artifact = (ws.path / "context" / "dependency_artifacts.md").read_text(
                encoding="utf-8"
            )
            assert "## task-dep: dep title" in artifact
            assert "**파일**: src/models/user.py" in artifact
            assert "### `src/models/user.py`" in artifact
            assert "class User():" in artifact
            assert "def helper(x: int) -> int: ..." in artifact
        finally:
            ws.cleanup()

    def test_inject_dependency_context_fallback_uses_actual_branch_files_with_same_path_rule(
        self, tmp_path
    ):
        task = Task(
            id="task-main", title="main", description="d",
            acceptance_criteria=["c"],
            target_files=[],
        )
        dep = Task(
            id="task-dep", title="dep", description="d",
            acceptance_criteria=["c"],
            target_files=["mismatch.py"],
            status=TaskStatus.DONE,
        )
        file_map = {
            "src/foo.py": "def injected():\n    return 1\n",
            "src/models/user.py": "class User:\n    pass\n",
        }
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            ws._read_from_branch = lambda branch, rel_path: file_map.get(rel_path)
            ws._list_branch_added_files = lambda branch, target_files: list(file_map)

            ws.inject_dependency_context([dep])

            assert (ws.src_dir / "foo.py").exists()
            assert (ws.src_dir / "models" / "user.py").exists()
            assert not (ws.src_dir / "src" / "foo.py").exists()
            assert not (ws.src_dir / "src" / "models" / "user.py").exists()

            artifact = (ws.path / "context" / "dependency_artifacts.md").read_text(
                encoding="utf-8"
            )
            assert "**파일**: src/foo.py, src/models/user.py" in artifact
        finally:
            ws.cleanup()


# ── 테스트 스켈레톤 선주입 ────────────────────────────────────────────────────


class TestTestSkeletonInjection:
    """workspace 생성 시 target_files 기준으로 tests/ 에 스켈레톤을 선주입한다.

    탐색만 하고 write_file 을 호출하지 않는 TestWriter 패턴을 차단하고,
    pytest 가 수집할 수 있는 경로에 파일이 놓이도록 강제한다.
    """

    def test_skeleton_test_files_injected_for_target_files(self, tmp_path):
        task = Task(
            id="task-100", title="t", description="d",
            acceptance_criteria=["c"],
            target_files=["src/auth.py", "src/models/user.py"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            # 디렉토리 구조 보존: src/ 는 tests/ 가 흡수, 그 아래 경로는 유지
            assert (ws.tests_dir / "test_auth.py").exists()
            assert (ws.tests_dir / "models" / "test_user.py").exists()

            # TODO 마커 포함 (is_skeleton_unchanged 판정용)
            auth_content = (ws.tests_dir / "test_auth.py").read_text(encoding="utf-8")
            assert "# TODO: tests for task task-100" in auth_content

            # 명백히 비어있음이 드러나야 — 실제 test 함수는 없어야 한다
            assert "def test_" not in auth_content
            assert "assert " not in auth_content
        finally:
            ws.cleanup()

    def test_skeleton_not_overwritten_if_exists(self, tmp_path):
        """재실행·선 조건: 이미 파일이 존재하면 덮어쓰지 않는다."""
        task = Task(
            id="task-101", title="t", description="d",
            acceptance_criteria=["c"],
            target_files=["src/auth.py"],
        )
        # 첫 번째 create → skeleton 주입
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            # 사용자(또는 직전 에이전트)가 실제 테스트를 채웠다고 가정
            real = "def test_login_ok():\n    assert 1 == 1\n"
            (ws.tests_dir / "test_auth.py").write_text(real, encoding="utf-8")

            # 두 번째 호출은 no-op (create 자체가 idempotent). 같은 workspace
            # 디렉토리가 재사용되어도 skeleton 이 사용자 코드를 덮으면 안 된다.
            ws.create()

            # 더 직접적인 검증: 새 WorkspaceManager 로 같은 base_dir 에 create 해도
            # 이미 있는 파일을 보존하는지 확인한다.
        finally:
            ws.cleanup()

        # 별도 재실행 시나리오: 같은 task_id·동일 workspace 경로 가정
        # (WorkspaceManager 는 timestamp 기반 새 경로를 만들므로 workspace 자체는
        # 새로 생성되지만, _inject_test_skeletons 의 dest.exists() 분기가 덮어쓰기
        # 방지 로직이다 — 동일 디렉토리에서 두 번 호출해 검증한다.)
        ws2 = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws2.create()
        try:
            target = ws2.tests_dir / "test_auth.py"
            # 새 workspace 경로이므로 주입된 스켈레톤 (TODO 포함) 이어야 한다
            assert "# TODO: tests for task task-101" in target.read_text()

            # 사용자가 채운 상태에서 _inject_test_skeletons 를 재호출해 보호를 확인
            real = "def test_login_ok():\n    assert 1 == 1\n"
            target.write_text(real, encoding="utf-8")
            ws2._inject_test_skeletons()
            assert target.read_text() == real
        finally:
            ws2.cleanup()

    def test_skeleton_content_per_language(self, tmp_path):
        """언어별 스켈레톤 문법이 해당 언어 규약과 일치해야 한다."""
        # Python
        t_py = Task(
            id="task-py", title="t", description="d", acceptance_criteria=["c"],
            target_files=["auth.py"], language="python",
        )
        ws = WorkspaceManager(t_py, tmp_path, base_dir=tmp_path / "ws_py")
        ws.create()
        try:
            content = (ws.tests_dir / "test_auth.py").read_text()
            assert content.startswith("import pytest")
            assert "# TODO: tests for task task-py" in content
        finally:
            ws.cleanup()

        # Kotlin
        t_kt = Task(
            id="task-kt", title="t", description="d", acceptance_criteria=["c"],
            target_files=["Foo.kt"], language="kotlin",
        )
        ws = WorkspaceManager(t_kt, tmp_path, base_dir=tmp_path / "ws_kt")
        ws.create()
        try:
            content = (ws.tests_dir / "test_Foo.kt").read_text()
            assert content.startswith("package tests")
            assert "import org.junit.Test" in content
            assert "// TODO: tests for task task-kt" in content
        finally:
            ws.cleanup()

        # Go — 파일명은 반드시 *_test.go (go test 가 수집하는 유일한 규약)
        t_go = Task(
            id="task-go", title="t", description="d", acceptance_criteria=["c"],
            target_files=["server.go"], language="go",
        )
        ws = WorkspaceManager(t_go, tmp_path, base_dir=tmp_path / "ws_go")
        ws.create()
        try:
            target = ws.tests_dir / "server_test.go"
            assert target.exists(), f"Go skeleton 은 *_test.go 패턴이어야 한다: {list(ws.tests_dir.iterdir())}"
            content = target.read_text()
            assert content.startswith("package tests_test")
            assert 'import "testing"' in content
            assert "// TODO: tests for task task-go" in content
            # 잘못된 'test_server.go' 가 남지 않도록 확인
            assert not (ws.tests_dir / "test_server.go").exists()
        finally:
            ws.cleanup()

        # JavaScript
        t_js = Task(
            id="task-js", title="t", description="d", acceptance_criteria=["c"],
            target_files=["app.js"], language="javascript",
        )
        ws = WorkspaceManager(t_js, tmp_path, base_dir=tmp_path / "ws_js")
        ws.create()
        try:
            content = (ws.tests_dir / "test_app.js").read_text()
            assert "// TODO: tests for task task-js" in content
            # JS 스켈레톤은 짧아도 되지만 test 함수는 없어야 한다
            assert "test(" not in content
            assert "it(" not in content
        finally:
            ws.cleanup()

    def test_skeleton_preserves_directory_structure(self, tmp_path):
        """동일한 basename 이지만 다른 디렉토리에 있는 target 은 서로 충돌하지 않아야 한다.

        회귀 가드: 이전에는 두 경로 모두 tests/test_user.py 로 평탄화되어
        하나가 덮어씌워졌다.
        """
        task = Task(
            id="task-collide", title="t", description="d", acceptance_criteria=["c"],
            target_files=["src/a/user.py", "src/b/user.py"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            a_test = ws.tests_dir / "a" / "test_user.py"
            b_test = ws.tests_dir / "b" / "test_user.py"
            assert a_test.exists(), f"a/test_user.py 가 없음: {list(ws.tests_dir.rglob('*'))}"
            assert b_test.exists(), f"b/test_user.py 가 없음: {list(ws.tests_dir.rglob('*'))}"
            # 충돌 방지: 동일 basename 이지만 서로 다른 파일이어야 한다
            assert a_test.read_text() == b_test.read_text()  # 템플릿은 같은 task_id 라 동일
            # 평탄화된 경로에는 만들어지지 않아야 한다
            assert not (ws.tests_dir / "test_user.py").exists()
        finally:
            ws.cleanup()

    def test_skeleton_skipped_for_unknown_extension(self, tmp_path):
        """지원하지 않는 확장자(.md, .html 등)는 스켈레톤을 만들지 않는다."""
        task = Task(
            id="task-doc", title="t", description="d", acceptance_criteria=["c"],
            target_files=["docs/spec.md", "templates/index.html"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        try:
            assert ws.list_test_files() == []
        finally:
            ws.cleanup()
