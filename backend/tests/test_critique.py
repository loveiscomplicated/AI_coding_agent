"""
backend/tests/test_critique.py

POST /api/tasks/critique 및 GET /api/tasks/critique/{job_id} 엔드포인트 검증.
"""

from __future__ import annotations

import json
import os

if not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "test-key"

from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import tasks as tasks_router

client = TestClient(app)

_SAMPLE_TASKS = [
    {
        "id": "task-001",
        "title": "샘플 태스크",
        "description": "### 목적과 배경\n목적.\n\n### 기술 요구사항\n요구.\n\n### 인접 컨텍스트\n후속 태스크 없음.\n\n### 비고려 항목\n명시적 비범위 없음.",
        "acceptance_criteria": ["pytest로 검증 가능한 조건"],
        "target_files": ["a.py"],
        "depends_on": [],
        "task_type": "backend",
    }
]

_SAMPLE_CONTEXT = "회의에서 a.py 기능 구현을 결정하였습니다."


class _FakeLLMResponse:
    def __init__(self, text: str) -> None:
        self.content = [{"type": "text", "text": text}]
        self.stop_reason = "end_turn"


class _FakeClient:
    def __init__(self, text: str) -> None:
        self._text = text

    def chat(self, _messages):  # noqa: ANN001
        return _FakeLLMResponse(self._text)


def _run_critique_and_wait(monkeypatch, response_text: str) -> dict:
    """_run_critique를 동기적으로 실행하고 완료된 job 상태를 반환한다."""
    monkeypatch.setattr(
        tasks_router, "create_client", lambda provider, cfg: _FakeClient(response_text)
    )

    job_id = "test-critique-job"
    with tasks_router._critique_lock:
        tasks_router._critique_jobs[job_id] = {"status": "running", "result": None}

    tasks_router._run_critique(job_id, _SAMPLE_TASKS, _SAMPLE_CONTEXT)

    with tasks_router._critique_lock:
        return tasks_router._critique_jobs[job_id]


def _valid_critique_json(verdict: str, issues: list[dict]) -> str:
    return json.dumps({
        "verdict": verdict,
        "summary": "검토 완료",
        "issues": issues,
        "suggestions": [],
    }, ensure_ascii=False)


# ── 1. POST /api/tasks/critique → job_id 즉시 반환 ────────────────────────────

def test_critique_returns_job_id(monkeypatch) -> None:
    """POST /api/tasks/critique 가 즉시 job_id를 반환한다."""
    monkeypatch.setattr(
        tasks_router, "create_client", lambda provider, cfg: _FakeClient(_valid_critique_json("APPROVED", []))
    )

    resp = client.post("/api/tasks/critique", json={
        "tasks": _SAMPLE_TASKS,
        "context_doc": _SAMPLE_CONTEXT,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert isinstance(data["job_id"], str)
    assert len(data["job_id"]) > 0


# ── 2. LLM mock 유효 JSON → status "done" ─────────────────────────────────────

def test_critique_polling_done(monkeypatch) -> None:
    """유효한 CritiqueResult JSON 반환 시 status가 'done'이 된다."""
    job = _run_critique_and_wait(monkeypatch, _valid_critique_json("APPROVED", []))

    assert job["status"] == "done"
    assert job["result"] is not None
    assert job["result"]["verdict"] == "APPROVED"


# ── 3. issues 없음 → verdict APPROVED ────────────────────────────────────────

def test_critique_approved_when_no_errors(monkeypatch) -> None:
    """issues가 없는 CritiqueResult는 verdict가 APPROVED이다."""
    job = _run_critique_and_wait(monkeypatch, _valid_critique_json("APPROVED", []))

    result = job["result"]
    assert result["verdict"] == "APPROVED"
    assert result["issues"] == []


# ── 4. severity ERROR 포함 → verdict NEEDS_REVISION ──────────────────────────

def test_critique_needs_revision_when_error_exists(monkeypatch) -> None:
    """severity ERROR가 포함된 CritiqueResult는 verdict가 NEEDS_REVISION이다."""
    issues = [
        {
            "task_id": "task-001",
            "severity": "ERROR",
            "category": "sizing",
            "message": "target_files가 3개를 초과합니다.",
        }
    ]
    job = _run_critique_and_wait(monkeypatch, _valid_critique_json("NEEDS_REVISION", issues))

    result = job["result"]
    assert result["verdict"] == "NEEDS_REVISION"
    assert any(i["severity"] == "ERROR" for i in result["issues"])


# ── 5. malformed JSON → graceful fallback ─────────────────────────────────────

def test_critique_parse_failure_graceful(monkeypatch) -> None:
    """LLM이 malformed JSON을 반환해도 APPROVED + 파싱 실패 summary로 graceful 처리된다."""
    job = _run_critique_and_wait(monkeypatch, "이것은 JSON이 아닙니다 {broken}")

    assert job["status"] == "done"
    result = job["result"]
    assert result["verdict"] == "APPROVED"
    assert "파싱 실패" in result["summary"]
    assert result["issues"] == []
    assert result["suggestions"] == []
