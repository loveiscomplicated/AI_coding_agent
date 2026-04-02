"""
backend/routers/tasks.py — 태스크 목록 CRUD + 초안 생성 API

GET  /api/tasks            태스크 목록 조회
POST /api/tasks            태스크 목록 전체 저장 (덮어쓰기)
GET  /api/tasks/{id}       단일 태스크 조회
POST /api/tasks/draft      context_doc → Sonnet → 태스크 초안 반환
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import LLM_PROVIDER, LLM_MODEL_CAPABLE
from llm import LLMConfig, Message, create_client
from orchestrator.task import Task, load_tasks, save_tasks

# ── 초안 생성 잡 저장소 ──────────────────────────────────────────────────────
_draft_jobs: dict[str, dict] = {}
_draft_lock = threading.Lock()

_DRAFT_SYSTEM_PROMPT = """\
당신은 소프트웨어 개발 태스크를 설계하는 전문가입니다.

프로젝트 컨텍스트 문서를 읽고 구현 태스크 목록을 생성하세요.

[규칙]
- 태스크 하나 = 파일 3개 이하. target_files가 4개 이상 필요하면 반드시 여러 태스크로 분할할 것
- 인터페이스/모델 정의, 구현 로직, 테스트는 가능한 한 별도 태스크로 분리할 것
- acceptance_criteria: 테스트 프레임워크로 직접 검증 가능한 구체적 조건 3~5개
- target_files: 생성 또는 수정할 파일 경로 목록 (반드시 3개 이하)
- depends_on: 먼저 완료되어야 하는 태스크 id 목록 (없으면 빈 배열)
- 컨텍스트 문서에 언급되지 않은 기능을 임의로 추가하지 말 것
- id는 "task-001", "task-002", ... 형식
- task_type: "backend" 또는 "frontend" 중 하나
  - "frontend": HTML/CSS/JS/React/Vue 등 브라우저에서 실행되는 UI 코드. 멀티 에이전트 파이프라인이 실행하지 않으므로 수락 기준을 자동으로 검증할 수 없음. 이 경우에도 태스크를 생성하되, task_type을 "frontend"로 설정할 것.
  - "backend": 서버, CLI, 라이브러리, 테스트, 인프라 등 나머지 모든 것

[분할 예시]
나쁜 예 — 파일 7개를 한 태스크에:
  task-001: MapService 전체 (Coordinate.kt, Place.kt, Route.kt, RouteStep.kt, MapService.kt, FakeMapService.kt, MapServiceTest.kt)

좋은 예 — 태스크 3개로 분리:
  task-001: 도메인 모델 정의 (Coordinate.kt, Place.kt, Route.kt)
  task-002: MapService 인터페이스 (MapService.kt, RouteStep.kt) — depends_on: [task-001]
  task-003: 테스트 스텁 구현 (FakeMapService.kt, MapServiceTest.kt) — depends_on: [task-002]

[출력 형식]
다음 JSON만 출력하세요. 마크다운 코드블록, 설명 텍스트 없이 순수 JSON만:
{"tasks": [{"id": "task-001", "title": "...", "description": "...", "acceptance_criteria": ["..."], "target_files": ["..."], "depends_on": [], "task_type": "backend"}]}
"""

router = APIRouter()


# ── 요청/응답 스키마 ──────────────────────────────────────────────────────────

class DraftRequest(BaseModel):
    context_doc: str


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

def _run_draft(job_id: str, context_doc: str) -> None:
    """백그라운드 스레드에서 LLM 초안 생성을 실행한다."""
    try:
        client = create_client(
            LLM_PROVIDER,
            LLMConfig(model=LLM_MODEL_CAPABLE, max_tokens=16000, system_prompt=_DRAFT_SYSTEM_PROMPT),
        )
        llm_response = client.chat([Message(role="user", content=context_doc)])
        raw = ""
        for block in llm_response.content:
            if isinstance(block, dict) and block.get("type") == "text":
                raw = block["text"]
                break
            if hasattr(block, "type") and block.type == "text":
                raw = block.text
                break

        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            data: Any = json.loads(cleaned)
        except json.JSONDecodeError as e:
            if llm_response.stop_reason == "max_tokens":
                error = "태스크가 너무 많아 응답이 잘렸습니다. 컨텍스트 문서를 줄이거나 태스크를 분할하세요."
            else:
                error = f"LLM 응답 파싱 실패: {e}\n응답:\n{raw[:300]}"
            with _draft_lock:
                _draft_jobs[job_id]["status"] = "error"
                _draft_jobs[job_id]["error"] = error
            return

        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            with _draft_lock:
                _draft_jobs[job_id]["status"] = "error"
                _draft_jobs[job_id]["error"] = "LLM 응답에 'tasks' 배열이 없습니다."
            return

        for task in tasks:
            warnings: list[str] = []
            if len(task.get("target_files") or []) > 3:
                warnings.append(f"target_files {len(task['target_files'])}개 — 3개 이하로 태스크를 분할하세요")
            if len(task.get("acceptance_criteria") or []) > 5:
                warnings.append(f"acceptance_criteria {len(task['acceptance_criteria'])}개 — 5개 이하로 줄이세요")
            if warnings:
                task["warnings"] = warnings

        with _draft_lock:
            _draft_jobs[job_id]["status"] = "done"
            _draft_jobs[job_id]["tasks"] = tasks

    except Exception as e:
        with _draft_lock:
            _draft_jobs[job_id]["status"] = "error"
            _draft_jobs[job_id]["error"] = str(e)


@router.post("/tasks/draft")
def generate_tasks_draft(body: DraftRequest) -> dict:
    """
    context_doc 마크다운을 Sonnet에 전달하여 태스크 초안 생성을 시작한다.
    생성은 백그라운드에서 실행되며 job_id를 즉시 반환한다.
    GET /api/tasks/draft/{job_id} 로 상태 및 결과를 조회한다.
    """
    job_id = str(uuid.uuid4())
    with _draft_lock:
        _draft_jobs[job_id] = {"status": "running", "tasks": None, "error": None}

    threading.Thread(target=_run_draft, args=(job_id, body.context_doc), daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@router.get("/tasks/draft/{job_id}")
def get_draft_status(job_id: str) -> dict:
    """태스크 초안 생성 잡의 상태와 결과를 반환한다."""
    with _draft_lock:
        job = _draft_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"초안 잡 '{job_id}'를 찾을 수 없습니다.")
    return {"job_id": job_id, **job}


@router.get("/tasks")
def list_tasks(tasks_path: str = "data/tasks.yaml") -> dict:
    """tasks.yaml에서 태스크 목록을 반환한다."""
    path = Path(tasks_path)
    if not path.exists():
        return {"tasks": []}
    try:
        tasks = load_tasks(path)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"tasks": [t.to_dict() for t in tasks]}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, tasks_path: str = "data/tasks.yaml") -> dict:
    """특정 ID의 태스크를 반환한다."""
    path = Path(tasks_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="tasks 파일을 찾을 수 없습니다.")
    try:
        tasks = load_tasks(path)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    for task in tasks:
        if task.id == task_id:
            return task.to_dict()
    raise HTTPException(status_code=404, detail=f"태스크 '{task_id}'를 찾을 수 없습니다.")


class PatchTaskRequest(BaseModel):
    description: str | None = None
    acceptance_criteria: list[str] | None = None
    tasks_path: str = "data/tasks.yaml"


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, body: PatchTaskRequest) -> dict:
    """특정 태스크의 description/acceptance_criteria를 부분 업데이트한다."""
    path = Path(body.tasks_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="tasks 파일을 찾을 수 없습니다.")
    try:
        tasks = load_tasks(path)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    for task in tasks:
        if task.id == task_id:
            if body.description is not None:
                task.description = body.description
            if body.acceptance_criteria is not None:
                task.acceptance_criteria = body.acceptance_criteria
            save_tasks(tasks, path)
            return task.to_dict()
    raise HTTPException(status_code=404, detail=f"태스크 '{task_id}'를 찾을 수 없습니다.")


class SaveTasksRequest(BaseModel):
    tasks: list[dict]
    tasks_path: str = "data/tasks.yaml"


@router.post("/tasks")
def save_tasks_endpoint(body: SaveTasksRequest) -> dict:
    """태스크 목록을 YAML 파일로 저장한다."""
    try:
        task_objs = [Task.from_dict(t) for t in body.tasks]
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"태스크 형식 오류: {e}")
    path = Path(body.tasks_path)
    save_tasks(task_objs, path)
    return {"saved": len(task_objs), "path": str(path)}
