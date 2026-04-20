from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import pipeline as pipeline_router


class _PauseCtrl:
    def handle_command(self, text: str) -> str | None:
        mapping = {"멈춰": "paused", "계속": "resumed", "중단": "stopped"}
        return mapping.get(text)


class _PauseCtrlNoopPause(_PauseCtrl):
    def handle_command(self, text: str) -> str | None:
        if text == "멈춰":
            return None
        return super().handle_command(text)


@pytest.fixture(autouse=True)
def _reset_jobs():
    with pipeline_router._lock:
        pipeline_router._jobs.clear()
    yield
    with pipeline_router._lock:
        pipeline_router._jobs.clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(pipeline_router.router, prefix="/api")
    return TestClient(app)


def _register_job(job_id: str, *, status: str = "running", ctrl=None):
    with pipeline_router._lock:
        pipeline_router._jobs[job_id] = {
            "job_id": job_id,
            "status": status,
            "paused": False,
            "pause_ctrl": ctrl or _PauseCtrl(),
        }


@pytest.mark.parametrize(
    ("action", "expected_paused"),
    [("pause", True), ("resume", False), ("stop", False)],
)
def test_pipeline_control_actions(client, action, expected_paused):
    job_id = f"job-{action}"
    _register_job(job_id)

    res = client.post(f"/api/pipeline/control/{job_id}", json={"action": action})
    assert res.status_code == 200
    body = res.json()
    assert body["job_id"] == job_id
    assert body["action"] == action
    assert body["applied"] is True

    with pipeline_router._lock:
        assert pipeline_router._jobs[job_id]["paused"] is expected_paused


@pytest.mark.parametrize("status", ["done", "error"])
def test_pipeline_control_rejects_finished_jobs(client, status):
    job_id = f"job-{status}"
    _register_job(job_id, status=status)

    res = client.post(f"/api/pipeline/control/{job_id}", json={"action": "pause"})
    assert res.status_code == 409
    assert "종료된 잡은 제어할 수 없습니다" in res.json()["detail"]


def test_pipeline_control_applied_false_when_command_not_matched(client):
    job_id = "job-noop"
    _register_job(job_id, ctrl=_PauseCtrlNoopPause())

    res = client.post(f"/api/pipeline/control/{job_id}", json={"action": "pause"})
    assert res.status_code == 200
    assert res.json()["applied"] is False
    with pipeline_router._lock:
        assert pipeline_router._jobs[job_id]["paused"] is False


def test_pipeline_run_passes_role_compaction_config_to_worker(client, monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run_pipeline(**kwargs):
        captured.update(kwargs)
        return {"success": 1, "fail": 0, "tasks": []}

    class _ImmediateThread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(pipeline_router, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(pipeline_router.threading, "Thread", _ImmediateThread)

    res = client.post("/api/pipeline/run", json={
        "tasks_path": str(tmp_path / "tasks.yaml"),
        "repo_path": str(tmp_path),
        "logs_dir": str(tmp_path / "logs"),
        "role_compaction_tuning_enabled": True,
        "role_compaction_tuning_preset": "aggressive",
        "role_compaction_tuning_overrides": {
            "implementer": "default",
            "reviewer": "conservative",
        },
    })
    assert res.status_code == 200
    assert captured["role_compaction_tuning_enabled"] is True
    assert captured["role_compaction_tuning_preset"] == "aggressive"
    assert captured["role_compaction_tuning_overrides"] == {
        "implementer": "default",
        "reviewer": "conservative",
    }
    assert str(captured["logs_dir"]).endswith("/logs")
