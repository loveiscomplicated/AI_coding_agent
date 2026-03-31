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

from backend.config import DISCORD_BOT_TOKEN, DISCORD_GUILD_ID
from hotline.notifier import DiscordNotifier
from orchestrator.run import PauseController, run_pipeline

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
    base_branch: str = "main"
    task_id: str | None = None
    no_pr: bool = False
    verbose: bool = False
    reports_dir: str | None = None      # None → run_pipeline 기본값 (repo_path/data/reports)
    max_workers: int = 1                # 병렬 에이전트 수 (1=순차)
    discord_channel_id: str | None = None  # 프로젝트 Discord 채널 ID (없으면 자동 생성)
    max_orchestrator_retries: int = 2   # 오케스트레이터 자동 재시도 최대 횟수
    auto_merge: bool = False            # 그룹 완료 후 base_branch 에 자동 머지


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/pipeline/run")
def run_pipeline_endpoint(body: RunRequest) -> dict:
    """
    파이프라인을 백그라운드 스레드에서 실행하고 job_id를 즉시 반환한다.
    GET /api/pipeline/stream/{job_id} 로 실시간 이벤트를 구독할 수 있다.
    """
    job_id = str(uuid.uuid4())
    q: _queue.Queue = _queue.Queue()

    pause_ctrl = PauseController()

    # Discord 채널 확보 (동기, 워커 시작 전)
    resolved_channel_id: str | None = body.discord_channel_id
    if not resolved_channel_id and DISCORD_BOT_TOKEN and DISCORD_GUILD_ID:
        try:
            notifier = DiscordNotifier.from_env()
            if notifier:
                project_name = Path(body.repo_path).name or "project"
                resolved_channel_id = notifier.create_channel(project_name)
        except Exception as exc:
            # 채널 생성 실패는 치명적이지 않음 — 알림 없이 진행
            import logging as _log
            _log.getLogger(__name__).warning("Discord 채널 생성 실패 (무시): %s", exc)

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
            "discord_channel_id": resolved_channel_id,
            "pause_ctrl": pause_ctrl,   # 제어용 (직렬화 제외)
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
                pause_controller=pause_ctrl,
                max_workers=body.max_workers,
                discord_channel_id=int(resolved_channel_id) if resolved_channel_id else None,
                max_orchestrator_retries=body.max_orchestrator_retries,
                auto_merge=body.auto_merge,
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
    return {
        "job_id": job_id,
        "status": "running",
        "discord_channel_id": resolved_channel_id,
    }


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
    return {k: v for k, v in job.items() if k != "pause_ctrl"}


class ControlRequest(BaseModel):
    action: str  # "pause" | "resume" | "stop"


@router.post("/pipeline/control/{job_id}")
def control_pipeline(job_id: str, body: ControlRequest) -> dict:
    """
    실행 중인 파이프라인에 제어 명령을 전송한다.

    action:
        pause  → 현재 태스크 완료 후 일시정지
        resume → 일시정지 해제
        stop   → 현재 태스크 완료 후 파이프라인 종료
    """
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"잡 '{job_id}'를 찾을 수 없습니다.")

    ctrl: PauseController | None = job.get("pause_ctrl")
    if ctrl is None:
        raise HTTPException(status_code=409, detail="이 잡은 제어를 지원하지 않습니다.")

    action = body.action.lower()
    if action == "pause":
        result_cmd = ctrl.handle_command("멈춰")
        if result_cmd:
            with _lock:
                _jobs[job_id]["paused"] = True
    elif action == "resume":
        result_cmd = ctrl.handle_command("계속")
        if result_cmd:
            with _lock:
                _jobs[job_id]["paused"] = False
    elif action == "stop":
        result_cmd = ctrl.handle_command("중단")
        if result_cmd:
            with _lock:
                _jobs[job_id]["paused"] = False
    else:
        raise HTTPException(status_code=422, detail=f"알 수 없는 action: {action!r}")

    return {"job_id": job_id, "action": action, "applied": result_cmd is not None}


@router.get("/pipeline/jobs")
def list_jobs() -> dict:
    """모든 잡의 요약 목록을 반환한다."""
    with _lock:
        raw = list(_jobs.values())
    # pause_ctrl은 Python 객체라 직렬화 불가 — 제외
    jobs = [{k: v for k, v in j.items() if k != "pause_ctrl"} for j in raw]
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return {"jobs": jobs}
