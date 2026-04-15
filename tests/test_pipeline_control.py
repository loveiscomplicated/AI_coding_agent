from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.routers import pipeline as pipeline_router


class _DummyPauseCtrl:
    def handle_command(self, text: str) -> str | None:
        if text == "멈춰":
            return "paused"
        if text == "계속":
            return "resumed"
        if text == "중단":
            return "stopped"
        return None


@pytest.fixture(autouse=True)
def _clear_jobs():
    with pipeline_router._lock:
        pipeline_router._jobs.clear()
    yield
    with pipeline_router._lock:
        pipeline_router._jobs.clear()


@pytest.mark.parametrize("status", ["done", "error"])
def test_control_pipeline_rejects_non_running_job(status):
    job_id = "job-1"
    with pipeline_router._lock:
        pipeline_router._jobs[job_id] = {
            "job_id": job_id,
            "status": status,
            "paused": False,
            "pause_ctrl": _DummyPauseCtrl(),
        }

    with pytest.raises(HTTPException) as exc:
        pipeline_router.control_pipeline(
            job_id, pipeline_router.ControlRequest(action="pause")
        )

    assert exc.value.status_code == 409
    assert "종료된 잡은 제어할 수 없습니다" in str(exc.value.detail)


def test_control_pipeline_applies_pause_for_running_job():
    job_id = "job-running"
    with pipeline_router._lock:
        pipeline_router._jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "paused": False,
            "pause_ctrl": _DummyPauseCtrl(),
        }

    result = pipeline_router.control_pipeline(
        job_id, pipeline_router.ControlRequest(action="pause")
    )

    assert result["applied"] is True
    with pipeline_router._lock:
        assert pipeline_router._jobs[job_id]["paused"] is True
