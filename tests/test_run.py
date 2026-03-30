"""
tests/test_run.py — orchestrator/run.py 단위 테스트

resolve_execution_groups() 위상 정렬 로직을 검증한다.
run_pipeline() 통합 테스트는 DockerRunner / LLM 의존성 때문에 별도 E2E로 처리.
"""

from __future__ import annotations

import pytest

from orchestrator.run import resolve_execution_groups
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
