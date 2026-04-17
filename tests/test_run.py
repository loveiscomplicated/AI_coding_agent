"""
tests/test_run.py — orchestrator/run.py 단위 테스트

resolve_execution_groups() 위상 정렬 로직을 검증한다.
run_pipeline() 통합 테스트는 DockerRunner / LLM 의존성 때문에 별도 E2E로 처리.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator import run as run_module
from orchestrator.pipeline import PipelineMetrics, PipelineResult
from orchestrator.run import _run_single_task, resolve_execution_groups
from orchestrator.task import Task, TaskStatus


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def make_task(task_id: str, depends_on: list[str] | None = None) -> Task:
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        description="",
        acceptance_criteria=["조건"],
        target_files=[],
        depends_on=depends_on or [],
    )


# ── 기본 동작 ─────────────────────────────────────────────────────────────────

class TestResolveExecutionGroups:
    def test_single_task_no_deps(self):
        tasks = [make_task("a")]
        groups = resolve_execution_groups(tasks)
        assert len(groups) == 1
        assert groups[0][0].id == "a"

    def test_independent_tasks_in_same_group(self):
        tasks = [make_task("a"), make_task("b"), make_task("c")]
        groups = resolve_execution_groups(tasks)
        assert len(groups) == 1
        ids = {t.id for t in groups[0]}
        assert ids == {"a", "b", "c"}

    def test_linear_chain_creates_sequential_groups(self):
        # a → b → c
        tasks = [
            make_task("a"),
            make_task("b", depends_on=["a"]),
            make_task("c", depends_on=["b"]),
        ]
        groups = resolve_execution_groups(tasks)
        assert len(groups) == 3
        assert groups[0][0].id == "a"
        assert groups[1][0].id == "b"
        assert groups[2][0].id == "c"

    def test_diamond_dependency(self):
        #   a
        #  / \
        # b   c
        #  \ /
        #   d
        tasks = [
            make_task("a"),
            make_task("b", depends_on=["a"]),
            make_task("c", depends_on=["a"]),
            make_task("d", depends_on=["b", "c"]),
        ]
        groups = resolve_execution_groups(tasks)
        # Group 0: a, Group 1: b+c, Group 2: d
        assert len(groups) == 3
        assert groups[0][0].id == "a"
        second_ids = {t.id for t in groups[1]}
        assert second_ids == {"b", "c"}
        assert groups[2][0].id == "d"

    def test_two_independent_chains(self):
        # Chain 1: a → b
        # Chain 2: c → d
        tasks = [
            make_task("a"),
            make_task("b", depends_on=["a"]),
            make_task("c"),
            make_task("d", depends_on=["c"]),
        ]
        groups = resolve_execution_groups(tasks)
        assert len(groups) == 2
        first_ids = {t.id for t in groups[0]}
        assert first_ids == {"a", "c"}
        second_ids = {t.id for t in groups[1]}
        assert second_ids == {"b", "d"}

    def test_all_tasks_resolved(self):
        tasks = [
            make_task("x"),
            make_task("y", depends_on=["x"]),
            make_task("z", depends_on=["x"]),
        ]
        groups = resolve_execution_groups(tasks)
        total = sum(len(g) for g in groups)
        assert total == 3

    # ── 오류 케이스 ───────────────────────────────────────────────────────────

    def test_nonexistent_dependency_raises(self):
        tasks = [make_task("a", depends_on=["nonexistent"])]
        with pytest.raises(ValueError, match="nonexistent"):
            resolve_execution_groups(tasks)

    def test_circular_dependency_raises(self):
        # a → b → a
        tasks = [
            make_task("a", depends_on=["b"]),
            make_task("b", depends_on=["a"]),
        ]
        with pytest.raises(ValueError, match="순환"):
            resolve_execution_groups(tasks)

    def test_self_dependency_raises(self):
        tasks = [make_task("a", depends_on=["a"])]
        with pytest.raises(ValueError):
            resolve_execution_groups(tasks)

    def test_empty_task_list_returns_empty(self):
        groups = resolve_execution_groups([])
        assert groups == []

    # ── 순서 보장 ──────────────────────────────────────────────────────────────

    def test_group_ordering_respected(self):
        """각 그룹의 태스크는 이전 그룹의 태스크가 모두 포함된 다음에만 등장한다."""
        tasks = [
            make_task("root"),
            make_task("mid", depends_on=["root"]),
            make_task("leaf", depends_on=["mid"]),
        ]
        groups = resolve_execution_groups(tasks)
        seen = set()
        for group in groups:
            for task in group:
                for dep in task.depends_on:
                    assert dep in seen, f"{task.id}의 의존성 {dep}이 아직 처리되지 않음"
            for task in group:
                seen.add(task.id)


# ── _run_single_task --no-pr 성공 경로 ────────────────────────────────────────


class _FakeWorkspace:
    """WorkspaceManager 의 최소 컨텍스트 매니저 stub."""

    def __init__(self, *_, **__):
        self.path = Path("/tmp/fake-ws")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None

    def inject_dependency_context(self, dep_tasks):
        return None


class TestRunSingleTaskNoPr:
    """--no-pr 성공 경로에서 _run_single_task 가 예외 없이 (True, "") 를 반환해야 한다.

    회귀 방지: 이전에 run.py 의 executor.submit() 호출이 positional 인자
    정렬을 잘못 맞춰서 save_lock 자리에 list(all_tasks) 가 들어갔고,
    그 결과 `with save_lock:` 이
    `'list' object does not support the context manager protocol` 로 터졌다.
    """

    def _invoke_via_main_loop(self, tmp_path, monkeypatch):
        """run.py:main() 의 태스크 루프를 그대로 재사용해서 호출 경로 자체를 검증."""
        task = make_task("task-001")
        all_tasks = [task]
        tasks_path = tmp_path / "tasks.yaml"
        tasks_path.write_text("tasks: []\n")
        reports_dir = tmp_path / "reports"

        pipeline_result = PipelineResult(
            task=task, succeeded=True, metrics=PipelineMetrics()
        )

        # 무거운 의존성을 전부 stub
        monkeypatch.setattr(run_module, "WorkspaceManager", _FakeWorkspace)
        monkeypatch.setattr(run_module, "build_report",
                            lambda *a, **kw: {"task_id": task.id})
        monkeypatch.setattr(run_module, "save_report",
                            lambda *a, **kw: reports_dir / f"{task.id}.yaml")
        monkeypatch.setattr(run_module, "save_tasks", lambda *a, **kw: None)

        pipeline = MagicMock()
        pipeline.run.return_value = pipeline_result

        save_lock = threading.Lock()

        # executor.submit 와 동일한 호출 패턴 — 호출 부 (run.py:main 안)에서
        # 인자 정렬이 어긋나면 여기서 같은 예외가 재현돼야 한다.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _run_single_task,
                task, pipeline, MagicMock(), tmp_path,
                no_pr=True,
                no_push=None,
                notifier=None,
                save_lock=save_lock,
                all_tasks=all_tasks,
                tasks_path=tasks_path,
                reports_dir=reports_dir,
            )
            return task, future.result()

    def test_no_pr_success_returns_without_exception(self, tmp_path, monkeypatch):
        task, (succeeded, branch) = self._invoke_via_main_loop(tmp_path, monkeypatch)
        assert succeeded is True
        assert branch == ""
        assert task.status == TaskStatus.DONE

    def test_run_single_task_signature_matches_call_site(self):
        """_run_single_task 의 kwarg 이름이 호출 부 (run.py:main) 와 일치해야 한다.

        positional 호출로 어긋났던 과거 회귀를 막기 위해 kwarg 계약을 고정.
        """
        import inspect

        sig = inspect.signature(_run_single_task)
        params = sig.parameters
        # 호출 부에서 쓰는 kwarg 이름들
        expected_kwargs = {
            "no_pr", "no_push", "notifier",
            "save_lock", "all_tasks", "tasks_path", "reports_dir",
        }
        missing = expected_kwargs - set(params.keys())
        assert not missing, f"_run_single_task 시그니처에서 누락: {missing}"

    def test_executor_submit_passes_run_single_task_by_kwargs(self):
        """run.py:main() 의 executor.submit(_run_single_task, ...) 는 반드시
        save_lock/all_tasks/tasks_path 를 keyword 로 넘겨야 한다.

        과거에 positional 로 넘기면서 인자 정렬이 어긋나 list 가 save_lock
        자리로 들어가 'list' object does not support the context manager
        protocol 예외가 났었다. 이 회귀를 막기 위해 호출 형태를 AST 로 고정.
        """
        import ast

        source = Path(run_module.__file__).read_text()
        tree = ast.parse(source)

        found = False
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "submit"
                    and node.args
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == "_run_single_task"):
                continue
            found = True
            kwarg_names = {kw.arg for kw in node.keywords}
            for required in ("save_lock", "all_tasks", "tasks_path"):
                assert required in kwarg_names, (
                    f"executor.submit(_run_single_task) 는 {required} 를 "
                    f"keyword 인자로 전달해야 한다 (현재 keywords={kwarg_names})"
                )
        assert found, "run.py 에서 executor.submit(_run_single_task, ...) 호출을 찾지 못함"
