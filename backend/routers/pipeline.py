"""
backend/routers/pipeline.py — 파이프라인 실행 API

POST /api/pipeline/run           백그라운드 잡 시작 → job_id 반환
GET  /api/pipeline/status/{id}   잡 상태 조회
GET  /api/pipeline/jobs          실행 중 / 완료된 잡 목록
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orchestrator.run import run_pipeline

router = APIRouter()

# ── 잡 저장소 (인메모리, 단일 프로세스) ──────────────────────────────────────
# { job_id: { "status": "running"|"done"|"error", "result": ..., "started_at": ..., ... } }
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


# ── 요청/응답 스키마 ──────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    tasks_path: str = "data/tasks.yaml"
    repo_path: str = "."
    base_branch: str = "dev"
    task_id: str | None = None
    no_pr: bool = False
    verbose: bool = False


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/pipeline/run")
def run_pipeline_endpoint(body: RunRequest) -> dict:
    """
    파이프라인을 백그라운드 스레드에서 실행하고 job_id를 즉시 반환한다.
    클라이언트는 /api/pipeline/status/{job_id}로 진행 상태를 폴링한다.
    """
    job_id = str(uuid.uuid4())

    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "result": None,
            "error": None,
            "request": body.model_dump(),
        }

    def _worker():
        try:
            result = run_pipeline(
                tasks_path=Path(body.tasks_path),
                repo_path=Path(body.repo_path).resolve(),
                base_branch=body.base_branch,
                task_id=body.task_id,
                no_pr=body.no_pr,
                verbose=body.verbose,
            )
            with _lock:
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["result"] = result
        except Exception as e:
            with _lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = str(e)
        finally:
            with _lock:
                _jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    return {"job_id": job_id, "status": "running"}


@router.get("/pipeline/status/{job_id}")
def get_job_status(job_id: str) -> dict:
    """잡 상태와 결과를 반환한다."""
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"잡 '{job_id}'를 찾을 수 없습니다.")
    return job


@router.get("/pipeline/jobs")
def list_jobs() -> dict:
    """모든 잡의 요약 목록을 반환한다."""
    with _lock:
        jobs = list(_jobs.values())
    # 최신순 정렬
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return {"jobs": jobs}
