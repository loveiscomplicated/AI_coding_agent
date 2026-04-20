"""
backend/tests/test_tasks_router.py

_DRAFT_SYSTEM_PROMPT가 complexity 섹션을 올바르게 포함하고,
draft sanitizer가 complexity 누락/비정상 값을 경고로 표시하는지 검증한다.
"""

from __future__ import annotations

import os
import threading

# backend.config는 import 시점에 provider별 API 키를 요구하므로 테스트용 더미 값을 세팅한다.
if not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "test-key"


from backend.routers import tasks as tasks_router  # noqa: E402
from backend.routers.tasks import _DRAFT_SYSTEM_PROMPT  # noqa: E402


# ── _DRAFT_SYSTEM_PROMPT 구조 ─────────────────────────────────────────────────


def test_draft_includes_complexity_field() -> None:
    """프롬프트에 complexity 평가 섹션과 필드 언급이 존재한다."""
    assert "complexity" in _DRAFT_SYSTEM_PROMPT
    assert "[복잡도 평가 (complexity)]" in _DRAFT_SYSTEM_PROMPT


def test_draft_complexity_valid_values() -> None:
    """simple / standard / complex 세 값이 프롬프트 가이드라인 표에 모두 명시되어 있다."""
    for value in ("simple", "standard", "complex"):
        assert value in _DRAFT_SYSTEM_PROMPT, (
            f"complexity tier '{value}'가 프롬프트에 언급되지 않음"
        )
    # 수치 기준 표의 열 헤더로 세 tier가 등장해야 한다.
    assert "| simple" in _DRAFT_SYSTEM_PROMPT
    assert "| standard" in _DRAFT_SYSTEM_PROMPT
    assert "| complex" in _DRAFT_SYSTEM_PROMPT


def test_draft_json_example_contains_complexity() -> None:
    """JSON 예시가 complexity 필드를 포함하여 LLM이 스키마를 학습하도록 한다."""
    assert '"complexity": "standard"' in _DRAFT_SYSTEM_PROMPT


def test_draft_mentions_standard_as_default() -> None:
    """평가 불가 시 기본값이 standard임이 명시되어 있다."""
    # "애매하면 standard" 또는 "기본값 standard" 문구 중 하나라도 존재해야 함
    assert "standard" in _DRAFT_SYSTEM_PROMPT
    assert ("애매하면 `standard`" in _DRAFT_SYSTEM_PROMPT
            or "기본값은 `standard`" in _DRAFT_SYSTEM_PROMPT
            or "3단계" in _DRAFT_SYSTEM_PROMPT), (
        "standard를 기본값(fallback)으로 정하는 문구를 찾지 못함"
    )


# ── draft 실행 경로의 complexity 처리 ────────────────────────────────────────


class _FakeLLMResponse:
    def __init__(self, text: str) -> None:
        self.content = [{"type": "text", "text": text}]
        self.stop_reason = "end_turn"


class _FakeClient:
    def __init__(self, text: str) -> None:
        self._text = text

    def chat(self, _messages):  # noqa: ANN001
        return _FakeLLMResponse(self._text)


def _run_draft_and_wait(monkeypatch, response_text: str, timeout: float = 2.0) -> dict:
    """_run_draft를 동기적으로 실행하고 완료된 job 상태를 반환한다."""

    monkeypatch.setattr(
        tasks_router, "create_client", lambda provider, cfg: _FakeClient(response_text)
    )

    job_id = "test-job"
    with tasks_router._draft_lock:
        tasks_router._draft_jobs[job_id] = {"status": "running", "tasks": None, "error": None}

    # _run_draft는 스레드에서 실행되지만 여기서는 직접 동기 호출한다.
    tasks_router._run_draft(job_id, context_doc="dummy")

    with tasks_router._draft_lock:
        return tasks_router._draft_jobs[job_id]


def test_sanitizer_warns_on_missing_complexity(monkeypatch) -> None:
    """LLM 응답에 complexity 필드가 없으면 warnings에 경고가 추가된다."""
    response = (
        '{"tasks": [{'
        '"id": "task-001", "title": "t",'
        '"description": "### 목적과 배경\\n목적. ' + ("본문. " * 20) + '\\n'
        '\\n### 기술 요구사항\\n요구. ' + ("본문. " * 20) + '\\n'
        '\\n### 인접 컨텍스트\\n후속 태스크 없음. ' + ("본문. " * 20) + '\\n'
        '\\n### 비고려 항목\\n명시적 비범위 없음. ' + ("본문. " * 20) + '",'
        '"acceptance_criteria": ["c1"],'
        '"target_files": ["a.py"],'
        '"depends_on": [], "task_type": "backend", "language": "python"'
        '}]}'
    )
    job = _run_draft_and_wait(monkeypatch, response)

    assert job["status"] == "done", job.get("error")
    task = job["tasks"][0]
    # complexity 키는 추가되지 않는다 (경고만)
    assert "complexity" not in task
    assert any("complexity 누락" in w for w in task.get("warnings", []))


def test_sanitizer_accepts_valid_complexity(monkeypatch) -> None:
    response = (
        '{"tasks": [{'
        '"id": "task-001", "title": "t",'
        '"description": "### 목적과 배경\\n목적. ' + ("본문. " * 20) + '\\n'
        '\\n### 기술 요구사항\\n요구. ' + ("본문. " * 20) + '\\n'
        '\\n### 인접 컨텍스트\\n후속 태스크 없음. ' + ("본문. " * 20) + '\\n'
        '\\n### 비고려 항목\\n명시적 비범위 없음. ' + ("본문. " * 20) + '",'
        '"acceptance_criteria": ["c1"],'
        '"target_files": ["a.py"],'
        '"depends_on": [], "task_type": "backend", "language": "python",'
        '"complexity": "simple"'
        '}]}'
    )
    job = _run_draft_and_wait(monkeypatch, response)

    assert job["status"] == "done", job.get("error")
    task = job["tasks"][0]
    assert task["complexity"] == "simple"
    # 올바른 값이면 complexity 누락 경고는 없다
    assert not any("complexity 누락" in w for w in task.get("warnings", []))


def test_sanitizer_rejects_invalid_complexity(monkeypatch) -> None:
    response = (
        '{"tasks": [{'
        '"id": "task-001", "title": "t",'
        '"description": "### 목적과 배경\\n목적. ' + ("본문. " * 20) + '\\n'
        '\\n### 기술 요구사항\\n요구. ' + ("본문. " * 20) + '\\n'
        '\\n### 인접 컨텍스트\\n후속 태스크 없음. ' + ("본문. " * 20) + '\\n'
        '\\n### 비고려 항목\\n명시적 비범위 없음. ' + ("본문. " * 20) + '",'
        '"acceptance_criteria": ["c1"],'
        '"target_files": ["a.py"],'
        '"depends_on": [], "task_type": "backend", "language": "python",'
        '"complexity": "trivial"'
        '}]}'
    )
    job = _run_draft_and_wait(monkeypatch, response)

    assert job["status"] == "done", job.get("error")
    task = job["tasks"][0]
    # 비정상 값은 제거된다
    assert "complexity" not in task
    assert any("complexity 값 비정상" in w for w in task.get("warnings", []))
    # 제거 후 누락 경고도 추가된다
    assert any("complexity 누락" in w for w in task.get("warnings", []))
