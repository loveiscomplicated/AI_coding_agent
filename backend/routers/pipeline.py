"""
backend/routers/pipeline.py — 파이프라인 실행 API

POST /api/pipeline/run              백그라운드 잡 시작 → job_id 반환
GET  /api/pipeline/status/{id}      잡 상태 조회
GET  /api/pipeline/stream/{id}      SSE 실시간 이벤트 스트림
GET  /api/pipeline/jobs             실행 중 / 완료된 잡 목록
"""

from __future__ import annotations

import json
import queue as _queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from orchestrator.run import run_pipeline

router = APIRouter()

# ── 잡 저장소 (인메모리, 단일 프로세스) ──────────────────────────────────────
_jobs: dict[str, dict[str, Any]] = {}
_event_queues: dict[str, _queue.Queue] = {}
_lock = threading.Lock()


# ── 이벤트 헬퍼 ───────────────────────────────────────────────────────────────

def _emit(job_id: str, event: dict) -> None:
    """thread-safe 이벤트 발행 — jobs에 누적하고 SSE 큐에도 넣는다."""
    event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].setdefault("events", []).append(event)
    q = _event_queues.get(job_id)
    if q is not None:
        q.put(event)


# ── 요청/응답 스키마 ──────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    tasks_path: str = "data/tasks.yaml"
    repo_path: str = "."
    base_branch: str = "dev"
    task_id: str | None = None
    no_pr: bool = False
    verbose: bool = False
    reports_dir: str | None = None   # None → run_pipeline 기본값 (repo_path/data/reports)


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/pipeline/run")
def run_pipeline_endpoint(body: RunRequest) -> dict:
    """
    파이프라인을 백그라운드 스레드에서 실행하고 job_id를 즉시 반환한다.
    GET /api/pipeline/stream/{job_id} 로 실시간 이벤트를 구독할 수 있다.
    """
    job_id = str(uuid.uuid4())
    q: _queue.Queue = _queue.Queue()

    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "result": None,
            "error": None,
            "events": [],
            "request": body.model_dump(),
        }
        _event_queues[job_id] = q

    def on_progress(event: dict) -> None:
        _emit(job_id, event)

    def _worker() -> None:
        try:
            result = run_pipeline(
                tasks_path=Path(body.tasks_path),
                repo_path=Path(body.repo_path).resolve(),
                base_branch=body.base_branch,
                task_id=body.task_id,
                no_pr=body.no_pr,
                verbose=body.verbose,
                reports_dir=Path(body.reports_dir) if body.reports_dir else None,
                on_progress=on_progress,
            )
            with _lock:
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["result"] = result
        except Exception as e:
            with _lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = str(e)
            _emit(job_id, {"type": "error", "message": str(e)})
        finally:
            with _lock:
                _jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            # SSE 구독자에게 스트림 종료를 알림
            q.put(None)

    threading.Thread(target=_worker, daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@router.get("/pipeline/stream/{job_id}")
def stream_pipeline_events(job_id: str) -> StreamingResponse:
    """
    SSE(Server-Sent Events) 실시간 이벤트 스트림.

    이미 완료된 잡이면 누적 이벤트를 모두 보내고 즉시 종료한다.
    실행 중인 잡이면 새 이벤트가 올 때마다 스트리밍한다.
    30초 무활동 시 keepalive 주석을 전송한다.
    """
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"잡 '{job_id}'를 찾을 수 없습니다.")

    def generate():
        # 이미 쌓인 과거 이벤트 먼저 전송
        with _lock:
            past = list(_jobs.get(job_id, {}).get("events", []))
            status = _jobs.get(job_id, {}).get("status", "done")

        for event in past:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        # 이미 종료된 잡이면 바로 end 전송
        if status in ("done", "error"):
            yield f"data: {json.dumps({'type': 'end', 'status': status}, ensure_ascii=False)}\n\n"
            return

        q = _event_queues.get(job_id)
        if q is None:
            yield f"data: {json.dumps({'type': 'end', 'status': 'done'}, ensure_ascii=False)}\n\n"
            return

        while True:
            try:
                event = q.get(timeout=30)
                if event is None:          # 파이프라인 종료 sentinel
                    with _lock:
                        final_status = _jobs.get(job_id, {}).get("status", "done")
                    yield f"data: {json.dumps({'type': 'end', 'status': final_status}, ensure_ascii=False)}\n\n"
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except _queue.Empty:
                yield ": keepalive\n\n"    # 커넥션 유지

    return StreamingResponse(generate(), media_type="text/event-stream")


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
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return {"jobs": jobs}
