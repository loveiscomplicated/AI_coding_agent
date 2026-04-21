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


def test_draft_complexity_binary_values() -> None:
    """simple / non-simple 두 값이 프롬프트에 명시되어 있다."""
    assert "simple" in _DRAFT_SYSTEM_PROMPT
    assert "non-simple" in _DRAFT_SYSTEM_PROMPT


def test_draft_json_example_contains_complexity() -> None:
    """JSON 예시가 complexity 필드를 포함하여 LLM이 스키마를 학습하도록 한다."""
    assert '"complexity": "non-simple"' in _DRAFT_SYSTEM_PROMPT


def test_draft_mentions_auto_compute_fallback() -> None:
    """평가 누락 시 자동 계산된다는 내용이 프롬프트에 있다."""
    assert "자동 계산" in _DRAFT_SYSTEM_PROMPT


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


def _make_task_response(extra_fields: str = "") -> str:
    return (
        '{"tasks": [{'
        '"id": "task-001", "title": "t",'
        '"description": "### 목적과 배경\\n목적. ' + ("본문. " * 20) + '\\n'
        '\\n### 기술 요구사항\\n요구. ' + ("본문. " * 20) + '\\n'
        '\\n### 인접 컨텍스트\\n후속 태스크 없음. ' + ("본문. " * 20) + '\\n'
        '\\n### 비고려 항목\\n명시적 비범위 없음. ' + ("본문. " * 20) + '",'
        '"acceptance_criteria": ["c1"],'
        '"target_files": ["a.py"],'
        '"depends_on": [], "task_type": "backend", "language": "python"'
        + (f', {extra_fields}' if extra_fields else "")
        + '}]}'
    )


def test_sanitizer_auto_computes_complexity_when_llm_omits(monkeypatch) -> None:
    """LLM 응답에 complexity 필드가 없으면 자동 계산하여 채우고 경고를 추가한다."""
    job = _run_draft_and_wait(monkeypatch, _make_task_response())

    assert job["status"] == "done", job.get("error")
    task = job["tasks"][0]
    # complexity가 자동 계산되어 채워진다 (simple or non-simple)
    assert task.get("complexity") in ("simple", "non-simple")
    assert any("자동 계산" in w for w in task.get("warnings", []))


def test_sanitizer_accepts_valid_complexity(monkeypatch) -> None:
    """'simple' 값은 그대로 보존되고 경고가 없다."""
    job = _run_draft_and_wait(monkeypatch, _make_task_response('"complexity": "simple"'))

    assert job["status"] == "done", job.get("error")
    task = job["tasks"][0]
    assert task["complexity"] == "simple"
    assert not any("자동 계산" in w for w in task.get("warnings", []))


def test_sanitizer_accepts_non_simple_complexity(monkeypatch) -> None:
    """'non-simple' 값은 그대로 보존되고 경고가 없다."""
    job = _run_draft_and_wait(monkeypatch, _make_task_response('"complexity": "non-simple"'))

    assert job["status"] == "done", job.get("error")
    task = job["tasks"][0]
    assert task["complexity"] == "non-simple"
    assert not any("자동 계산" in w for w in task.get("warnings", []))


def test_sanitizer_normalizes_legacy_standard_value(monkeypatch) -> None:
    """legacy 'standard' 값은 'non-simple'로 정규화되고 경고가 추가된다."""
    job = _run_draft_and_wait(monkeypatch, _make_task_response('"complexity": "standard"'))

    assert job["status"] == "done", job.get("error")
    task = job["tasks"][0]
    assert task["complexity"] == "non-simple"
    assert any("정규화" in w for w in task.get("warnings", []))


def test_sanitizer_normalizes_legacy_complex_value(monkeypatch) -> None:
    """legacy 'complex' 값은 'non-simple'로 정규화되고 경고가 추가된다."""
    job = _run_draft_and_wait(monkeypatch, _make_task_response('"complexity": "complex"'))

    assert job["status"] == "done", job.get("error")
    task = job["tasks"][0]
    assert task["complexity"] == "non-simple"
    assert any("정규화" in w for w in task.get("warnings", []))


def test_sanitizer_auto_computes_for_invalid_complexity(monkeypatch) -> None:
    """비정상 complexity 값은 자동 계산으로 교체되고 경고가 추가된다."""
    job = _run_draft_and_wait(monkeypatch, _make_task_response('"complexity": "trivial"'))

    assert job["status"] == "done", job.get("error")
    task = job["tasks"][0]
    assert task.get("complexity") in ("simple", "non-simple")
    assert any("자동 계산" in w for w in task.get("warnings", []))
