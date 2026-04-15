from __future__ import annotations

from types import SimpleNamespace

from orchestrator.task import Task
from orchestrator.task_redesign import redesign_task


class _FakeLLM:
    def __init__(self, text: str):
        self._text = text

    def chat(self, messages):
        return SimpleNamespace(content=[{"type": "text", "text": self._text}])


def _task(task_id: str = "task-001") -> Task:
    return Task(
        id=task_id,
        title="title",
        description="desc",
        acceptance_criteria=["a", "b", "c"],
        target_files=["src/a.py"],
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
