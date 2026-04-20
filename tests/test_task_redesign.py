from __future__ import annotations

import pytest

from types import SimpleNamespace

from orchestrator.task import Task, TaskStatus, load_tasks
from orchestrator.task_redesign import SplitTaskError, redesign_task, split_task


class _FakeLLM:
    def __init__(self, text: str):
        self._text = text

    def chat(self, messages):
        return SimpleNamespace(content=[{"type": "text", "text": self._text}])


def _task(task_id: str = "task-001") -> Task:
    return Task(
        id=task_id,
        title="authentication login flow",
        description="implement user authentication login flow with token refresh",
        acceptance_criteria=[
            "user authentication succeeds with valid credentials",
            "token refresh updates session state",
            "invalid credentials return an error response",
        ],
        target_files=["src/a.py", "src/b.py", "src/c.py"],
    )


def test_redesign_task_returns_error_on_invalid_json():
    task = _task()
    llm = _FakeLLM("this is not json")

    result = redesign_task(task, [task], "spec", llm)

    assert result.success is False
    assert "파싱 실패" in result.error


def test_redesign_task_split_success():
    task = _task()
    llm = _FakeLLM(
        """
        {
          "action": "split",
          "explanation": "큰 태스크를 두 단계로 분리",
          "tasks": [
            {
              "id": "task-001-a",
              "title": "인터페이스 정의",
              "description": "d1",
              "acceptance_criteria": ["1","2","3"],
              "target_files": ["src/a.py"],
              "depends_on": [],
              "task_type": "backend"
            },
            {
              "id": "task-001-b",
              "title": "구현",
              "description": "d2",
              "acceptance_criteria": ["1","2","3"],
              "target_files": ["src/b.py"],
              "depends_on": ["task-001-a"],
              "task_type": "backend"
            }
          ]
        }
        """
    )

    result = redesign_task(task, [task], "spec", llm)

    assert result.success is True
    assert result.action == "split"
    assert len(result.tasks) == 2


def test_redesign_task_simplify_success():
    task = _task()
    llm = _FakeLLM(
        """
        {
          "action": "simplify",
          "explanation": "핵심 기능만 남김",
          "tasks": [
            {
              "id": "task-001",
              "title": "간소화 태스크",
              "description": "d",
              "acceptance_criteria": ["1","2","3"],
              "target_files": ["src/a.py"],
              "depends_on": [],
              "task_type": "backend"
            }
          ]
        }
        """
    )

    result = redesign_task(task, [task], "spec", llm)

    assert result.success is True
    assert result.action == "simplify"
    assert len(result.tasks) == 1


def test_redesign_task_fails_when_required_field_missing():
    task = _task()
    llm = _FakeLLM(
        """
        {
          "action": "split",
          "explanation": "필수 필드 누락 케이스",
          "tasks": [
            {
              "id": "task-001-a",
              "description": "title 없음",
              "acceptance_criteria": ["1","2","3"],
              "target_files": ["src/a.py"],
              "depends_on": [],
              "task_type": "backend"
            }
          ]
        }
        """
    )

    result = redesign_task(task, [task], "spec", llm)

    assert result.success is False
    assert "id 또는 title" in result.error


# ── split_task() ──────────────────────────────────────────────────────────────


def _split_llm(n: int = 2) -> _FakeLLM:
    payloads = []
    for i, suffix in enumerate(["a", "b", "c"][:n]):
        payloads.append(
            f'{{"id": "ignored-{suffix}", "title": "part-{suffix}",'
            f' "description": "d{i}",'
            f' "acceptance_criteria": ["1","2","3"],'
            f' "target_files": ["src/{suffix}.py"],'
            f' "depends_on": [], "task_type": "backend"}}'
        )
    body = ",".join(payloads)
    return _FakeLLM(
        '{"action": "split", "explanation": "쪼개자",'
        f' "tasks": [{body}]}}'
    )


def test_split_task_preserves_original_as_superseded(tmp_path):
    task = _task("task-001")
    all_tasks = [task]
    tasks_path = tmp_path / "tasks.yaml"

    subs = split_task(task, all_tasks, "spec", _split_llm(2), tasks_path)

    assert task.status == TaskStatus.SUPERSEDED
    assert len(subs) == 2
    assert [s.id for s in subs] == ["task-001-a", "task-001-b"]
    # tasks.yaml 에 SUPERSEDED 가 저장되고, 하위 태스크도 기록됐는지 확인
    loaded = load_tasks(tasks_path)
    by_id = {t.id: t for t in loaded}
    assert by_id["task-001"].status == TaskStatus.SUPERSEDED
    assert "task-001-a" in by_id and "task-001-b" in by_id


def test_split_task_sequential_dependency_chain(tmp_path):
    task = _task("task-001")
    task.depends_on = ["task-000"]
    all_tasks = [Task(id="task-000", title="t", description="d",
                      acceptance_criteria=["c"], target_files=[]),
                 task]
    subs = split_task(task, all_tasks, "spec", _split_llm(3),
                      tmp_path / "tasks.yaml")
    # a 는 원 태스크 의존성을 이어받고, b/c 는 순차 체인
    assert subs[0].depends_on == ["task-000"]
    assert subs[1].depends_on == ["task-001-a"]
    assert subs[2].depends_on == ["task-001-b"]


def test_split_task_rejects_simplify_action(tmp_path):
    task = _task()
    llm = _FakeLLM(
        '{"action": "simplify", "explanation": "단일",'
        ' "tasks": [{"id": "task-001", "title": "x", "description": "d",'
        ' "acceptance_criteria": ["1","2","3"], "target_files": ["src/a.py"],'
        ' "depends_on": [], "task_type": "backend"}]}'
    )
    with pytest.raises(SplitTaskError, match="split 이 아닌"):
        split_task(task, [task], "spec", llm, tmp_path / "tasks.yaml")


def test_split_task_rejects_single_subtask(tmp_path):
    task = _task()
    with pytest.raises(SplitTaskError, match="하위 태스크 개수"):
        split_task(task, [task], "spec", _split_llm(1), tmp_path / "tasks.yaml")


def test_split_task_inserts_after_original_in_all_tasks(tmp_path):
    orig = _task("task-002")
    other_before = Task(id="task-001", title="t", description="d",
                        acceptance_criteria=["c"], target_files=[])
    other_after = Task(id="task-003", title="t", description="d",
                       acceptance_criteria=["c"], target_files=[])
    all_tasks = [other_before, orig, other_after]

    split_task(orig, all_tasks, "spec", _split_llm(2), tmp_path / "tasks.yaml")

    ids = [t.id for t in all_tasks]
    assert ids == ["task-001", "task-002", "task-002-a", "task-002-b", "task-003"]


def test_split_task_rejects_id_collision(tmp_path):
    task = _task("task-001")
    existing_collision = Task(id="task-001-a", title="선점", description="d",
                              acceptance_criteria=["c"], target_files=[])
    all_tasks = [task, existing_collision]
    with pytest.raises(SplitTaskError, match="id 충돌"):
        split_task(task, all_tasks, "spec", _split_llm(2), tmp_path / "tasks.yaml")


def test_split_task_saves_yaml_to_path(tmp_path):
    task = _task("task-001")
    yaml_path = tmp_path / "nested" / "tasks.yaml"
    split_task(task, [task], "spec", _split_llm(2), yaml_path)
    assert yaml_path.exists()
    loaded = load_tasks(yaml_path)
    assert {t.id for t in loaded} == {"task-001", "task-001-a", "task-001-b"}


# ── scope 검증 (P1) ──────────────────────────────────────────────────────────


def _scoped_split_llm(
    children: list[dict],
    action: str = "split",
) -> _FakeLLM:
    import json as _json
    return _FakeLLM(_json.dumps({
        "action": action,
        "explanation": "test",
        "tasks": children,
    }))


def test_split_task_rejects_out_of_scope_target_file(tmp_path):
    task = _task("task-001")
    children = [
        {
            "id": "x", "title": "a", "description": "d",
            "acceptance_criteria": ["user authentication valid"],
            "target_files": ["src/a.py"],
            "depends_on": [], "task_type": "backend",
        },
        {
            "id": "x", "title": "b", "description": "d",
            "acceptance_criteria": ["token refresh updates"],
            "target_files": ["src/malicious.py"],
            "depends_on": [], "task_type": "backend",
        },
    ]
    with pytest.raises(SplitTaskError, match="target_files 범위 이탈"):
        split_task(task, [task], "spec", _scoped_split_llm(children),
                   tmp_path / "tasks.yaml")


def test_split_task_rejects_out_of_scope_acceptance_criterion(tmp_path):
    task = _task("task-001")
    children = [
        {
            "id": "x", "title": "a", "description": "d",
            "acceptance_criteria": ["user authentication login succeeds"],
            "target_files": ["src/a.py"],
            "depends_on": [], "task_type": "backend",
        },
        {
            "id": "x", "title": "b", "description": "d",
            "acceptance_criteria": ["kubernetes autoscaler deploys docker containers"],
            "target_files": ["src/b.py"],
            "depends_on": [], "task_type": "backend",
        },
    ]
    with pytest.raises(SplitTaskError, match="acceptance_criteria 범위 이탈"):
        split_task(task, [task], "spec", _scoped_split_llm(children),
                   tmp_path / "tasks.yaml")


def test_split_task_allows_rephrased_criterion_within_scope(tmp_path):
    task = _task("task-001")
    children = [
        {
            "id": "x", "title": "a", "description": "d",
            "acceptance_criteria": ["authentication login with credentials works"],
            "target_files": ["src/a.py"],
            "depends_on": [], "task_type": "backend",
        },
        {
            "id": "x", "title": "b", "description": "d",
            "acceptance_criteria": ["token refresh updates the session"],
            "target_files": ["src/b.py"],
            "depends_on": [], "task_type": "backend",
        },
    ]
    subs = split_task(task, [task], "spec", _scoped_split_llm(children),
                      tmp_path / "tasks.yaml")
    assert len(subs) == 2


def test_split_task_rejects_prefixed_parent_criterion_with_extra_scope(tmp_path):
    task = _task("task-001")
    children = [
        {
            "id": "x", "title": "a", "description": "d",
            "acceptance_criteria": [
                "user authentication succeeds with valid credentials and deploy kubernetes autoscaler"
            ],
            "target_files": ["src/a.py"],
            "depends_on": [], "task_type": "backend",
        },
        {
            "id": "x", "title": "b", "description": "d",
            "acceptance_criteria": ["token refresh updates the session"],
            "target_files": ["src/b.py"],
            "depends_on": [], "task_type": "backend",
        },
    ]
    with pytest.raises(SplitTaskError, match="acceptance_criteria 범위 이탈"):
        split_task(task, [task], "spec", _scoped_split_llm(children),
                   tmp_path / "tasks.yaml")


def test_split_task_rejects_target_files_not_a_list(tmp_path):
    task = _task("task-001")
    children = [
        {
            "id": "x", "title": "a", "description": "d",
            "acceptance_criteria": ["user authentication login"],
            "target_files": "src/a.py",  # 잘못된 타입
            "depends_on": [], "task_type": "backend",
        },
        {
            "id": "y", "title": "b", "description": "d",
            "acceptance_criteria": ["token refresh updates session"],
            "target_files": ["src/b.py"],
            "depends_on": [], "task_type": "backend",
        },
    ]
    with pytest.raises(SplitTaskError, match="형식 오류"):
        split_task(task, [task], "spec", _scoped_split_llm(children),
                   tmp_path / "tasks.yaml")
