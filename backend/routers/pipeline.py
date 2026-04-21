"""
backend/routers/pipeline.py — 파이프라인 실행 API

POST /api/pipeline/run              백그라운드 잡 시작 → job_id 반환
GET  /api/pipeline/status/{id}      잡 상태 조회
GET  /api/pipeline/stream/{id}      SSE 실시간 이벤트 스트림
GET  /api/pipeline/jobs             실행 중 / 완료된 잡 목록
"""

from __future__ import annotations

import json
import logging
import queue as _queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.roles import ROLE_COMPACTION_PRESET_BALANCED, RoleModelConfig
from backend.config import DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, DEFAULT_ROLE_MODEL_MAP
from hotline.notifier import DiscordNotifier
from orchestrator.run import PauseController, run_pipeline
from project_paths import resolve_tasks_path

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
    tasks_path: str = "agent-data/tasks.yaml"
    repo_path: str = "."
    base_branch: str = "main"
    task_id: str | None = None
    no_pr: bool = False
    no_push: bool = False
    verbose: bool = False
    reports_dir: str | None = None      # None → run_pipeline 기본값 (repo_path/agent-data/reports)
    logs_dir: str | None = None         # None → run_pipeline 기본값 (repo_path/agent-data/logs)
    max_workers: int = 1                # 병렬 에이전트 수 (1=순차)
    discord_channel_id: str | None = None  # 프로젝트 Discord 채널 ID (없으면 자동 생성)
    max_orchestrator_retries: int = 3   # 오케스트레이터 자동 재시도 최대 횟수 (총 시도 = 이 값 + 1)
    intervention_auto_split: bool = False
    # True이면 최종 실패 직전에 LLM 을 호출해 태스크를 2~3개 하위 태스크로 자동 분해한다.
    # 분해된 하위 태스크는 tasks.yaml 에 기록만 되며, 다음 파이프라인 실행 시 픽업된다.
    auto_merge: bool = False            # 그룹 완료 후 base_branch 에 자동 머지
    default_role_models: dict[str, dict[str, str | None]] | None = None
    # 역할별 기본 모델 설정. 지정하지 않으면 서버 DEFAULT_ROLE_MODEL_MAP 사용.
    role_models: dict[str, dict[str, str | None]] | None = None
    # 역할별 모델 오버라이드. 예: {"reviewer": {"provider": "claude", "model": "claude-sonnet-4-20250514"}}
    # 지원 키: "test_writer", "implementer", "reviewer", "orchestrator", "merge_agent", "intervention"
    role_compaction_tuning_enabled: bool = False
    role_compaction_tuning_preset: str = ROLE_COMPACTION_PRESET_BALANCED
    role_compaction_tuning_overrides: dict[str, str] | None = None
    # 예: {"implementer": "aggressive", "reviewer": "default"}
    auto_select_by_complexity: bool = False
    # True이면 각 태스크의 complexity 라벨로 모델을 자동 선택한다.
    # role_models 는 complexity mapping 의 상위 override로 유지된다 — 명시된 역할만
    # role_models로 덮어쓰고, 나머지 역할은 complexity mapping 적용.


def _parse_role_models(raw: dict[str, dict] | None) -> dict[str, RoleModelConfig] | None:
    if not raw:
        return None
    return {
        role: RoleModelConfig(
            provider=cfg.get("provider"),
            model=cfg.get("model"),
        )
        for role, cfg in raw.items()
    }


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

    # Discord 채널 ID — 이미 알고 있으면 그대로, 모르면 워커에서 비동기 생성
    preset_channel_id: str | None = body.discord_channel_id

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
            "discord_channel_id": preset_channel_id,  # 워커에서 생성 후 갱신될 수 있음
            "pause_ctrl": pause_ctrl,   # 제어용 (직렬화 제외)
        }
        _event_queues[job_id] = q

    def on_progress(event: dict) -> None:
        _emit(job_id, event)

    def _worker() -> None:
        try:
            # Discord 채널 확보 — 백그라운드에서 실행하여 run 응답을 즉시 반환
            resolved_channel_id: str | None = preset_channel_id
            if not resolved_channel_id and DISCORD_BOT_TOKEN and DISCORD_GUILD_ID:
                try:
                    notifier = DiscordNotifier.from_env()
                    if notifier:
                        project_name = Path(body.repo_path).name or "project"
                        resolved_channel_id = notifier.create_channel(project_name)
                        with _lock:
                            _jobs[job_id]["discord_channel_id"] = resolved_channel_id
                except Exception as exc:
                    logger.warning("Discord 채널 생성 실패 (무시): %s", exc)

            result = run_pipeline(
                tasks_path=resolve_tasks_path(body.tasks_path, base=Path(body.repo_path).resolve()),
                repo_path=Path(body.repo_path).resolve(),
                base_branch=body.base_branch,
                task_id=body.task_id,
                no_pr=body.no_pr,
                no_push=body.no_push,
                verbose=body.verbose,
                reports_dir=Path(body.reports_dir) if body.reports_dir else None,
                logs_dir=Path(body.logs_dir) if body.logs_dir else None,
                on_progress=on_progress,
                pause_controller=pause_ctrl,
                max_workers=body.max_workers,
                discord_channel_id=int(resolved_channel_id) if resolved_channel_id else None,
                max_orchestrator_retries=body.max_orchestrator_retries,
                intervention_auto_split=body.intervention_auto_split,
                auto_merge=body.auto_merge,
                default_role_models=_parse_role_models(body.default_role_models) or _parse_role_models(DEFAULT_ROLE_MODEL_MAP),
                role_models=_parse_role_models(body.role_models),
                role_compaction_tuning_enabled=body.role_compaction_tuning_enabled,
                role_compaction_tuning_preset=body.role_compaction_tuning_preset,
                role_compaction_tuning_overrides=body.role_compaction_tuning_overrides,
                auto_select_by_complexity=body.auto_select_by_complexity,
            )
            with _lock:
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["result"] = result
        except Exception as e:
            with _lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = str(e)
            _emit(job_id, {"type": "error", "message": str(e)})
            logger.exception("파이프라인 워커 예외: %s", e)
        finally:
            with _lock:
                _jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            # SSE 구독자에게 스트림 종료를 알림 (크래시 여부와 무관하게 항상 실행)
            q.put(None)

    threading.Thread(target=_worker, daemon=True).start()
    return {
        "job_id": job_id,
        "status": "running",
        "discord_channel_id": preset_channel_id,  # 이미 알고 있으면 즉시 반환, 없으면 null
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
    if job.get("status") != "running":
        raise HTTPException(
            status_code=409,
            detail=f"종료된 잡은 제어할 수 없습니다. status={job.get('status')!r}",
        )

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
