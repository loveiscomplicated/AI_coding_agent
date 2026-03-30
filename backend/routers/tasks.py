"""
backend/routers/tasks.py — 태스크 목록 CRUD + 초안 생성 API

GET  /api/tasks            태스크 목록 조회
POST /api/tasks            태스크 목록 전체 저장 (덮어쓰기)
GET  /api/tasks/{id}       단일 태스크 조회
POST /api/tasks/draft      context_doc → Sonnet → 태스크 초안 반환
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import ANTHROPIC_API_KEY
from orchestrator.task import Task, load_tasks, save_tasks

_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

_DRAFT_SYSTEM_PROMPT = """\
당신은 소프트웨어 개발 태스크를 설계하는 전문가입니다.

프로젝트 컨텍스트 문서를 읽고 구현 태스크 목록을 생성하세요.

[규칙]
- 태스크 하나 = Python 모듈 1~2개(파일 1~3개) 수준으로 분할
- acceptance_criteria: pytest로 직접 테스트 가능한 구체적 조건 3~5개
- target_files: 생성 또는 수정할 파일 경로 목록 (예: "src/metrics/collector.py")
- depends_on: 먼저 완료되어야 하는 태스크 id 목록 (없으면 빈 배열)
- 컨텍스트 문서에 언급되지 않은 기능을 임의로 추가하지 말 것
- id는 "task-001", "task-002", ... 형식

[출력 형식]
다음 JSON만 출력하세요. 마크다운 코드블록, 설명 텍스트 없이 순수 JSON만:
{"tasks": [{"id": "task-001", "title": "...", "description": "...", "acceptance_criteria": ["..."], "target_files": ["..."], "depends_on": []}]}
"""

router = APIRouter()


# ── 요청/응답 스키마 ──────────────────────────────────────────────────────────

class DraftRequest(BaseModel):
    context_doc: str


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/tasks/draft")
async def generate_tasks_draft(body: DraftRequest) -> dict:
    """
    context_doc 마크다운을 Sonnet에 전달하여 태스크 초안을 생성한다.
    생성된 초안은 저장하지 않고 JSON으로 반환한다.
    프론트엔드에서 편집 후 POST /api/tasks 로 저장한다.
    """
    response = await _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=_DRAFT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": body.context_doc}],
    )
    raw = response.content[0].text if response.content else ""

    # LLM이 가끔 ```json ... ``` 블록으로 감싸는 경우 처리
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        data: Any = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"Sonnet 응답 파싱 실패: {e}\n응답:\n{raw[:300]}")

    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        raise HTTPException(status_code=502, detail="Sonnet 응답에 'tasks' 배열이 없습니다.")

    return {"tasks": tasks}


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
