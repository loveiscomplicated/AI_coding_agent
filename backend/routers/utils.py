"""
backend/routers/utils.py — 로컬 유틸리티 엔드포인트

GET /api/utils/browse?type=folder   → macOS 파인더에서 폴더 선택
GET /api/utils/browse?type=file     → macOS 파인더에서 파일 선택
"""

import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.config import LLM_PROVIDER, LLM_MODEL_FAST, LLM_MODEL_CAPABLE
from tools.hotline_tools import (
    get_conv_model, set_conv_model,
    get_redesign_model, set_redesign_model,
    get_task_draft_model, set_task_draft_model,
)

router = APIRouter()


@router.get("/config")
def get_config() -> dict:
    """현재 백엔드 LLM 설정을 반환한다."""
    return {
        "llm_provider": LLM_PROVIDER,
        "model_fast": LLM_MODEL_FAST,
        "model_capable": LLM_MODEL_CAPABLE,
    }


class LLMSettingsRequest(BaseModel):
    hotline_conv_model: str
    hotline_conv_provider: str
    redesign_model: str = ""
    redesign_provider: str = ""
    task_draft_model: str = ""
    task_draft_provider: str = ""


@router.get("/config/llm")
def get_llm_settings() -> dict:
    """런타임 LLM 설정을 반환한다."""
    conv_info = get_conv_model()
    redesign_info = get_redesign_model()
    draft_info = get_task_draft_model()
    return {
        "hotline_conv_model": conv_info["model"] or LLM_MODEL_CAPABLE,
        "hotline_conv_provider": conv_info["provider"] or LLM_PROVIDER,
        "redesign_model": redesign_info["model"] or LLM_MODEL_CAPABLE,
        "redesign_provider": redesign_info["provider"] or LLM_PROVIDER,
        "task_draft_model": draft_info["model"] or LLM_MODEL_CAPABLE,
        "task_draft_provider": draft_info["provider"] or LLM_PROVIDER,
    }


@router.patch("/config/llm")
def update_llm_settings(body: LLMSettingsRequest) -> dict:
    """Discord 대화용, 태스크 재설계용, 태스크 초안 생성용 LLM 모델을 런타임에 변경한다."""
    model = body.hotline_conv_model.strip()
    provider = body.hotline_conv_provider.strip()
    if not model:
        raise HTTPException(status_code=422, detail="모델명이 비어 있습니다.")
    if not provider:
        raise HTTPException(status_code=422, detail="provider가 비어 있습니다.")
    set_conv_model(model, provider)

    redesign_m = body.redesign_model.strip()
    redesign_p = body.redesign_provider.strip()
    if redesign_m and redesign_p:
        set_redesign_model(redesign_m, redesign_p)

    draft_m = body.task_draft_model.strip()
    draft_p = body.task_draft_provider.strip()
    if draft_m and draft_p:
        set_task_draft_model(draft_m, draft_p)

    return {
        "hotline_conv_model": model,
        "hotline_conv_provider": provider,
        "redesign_model": redesign_m or model,
        "redesign_provider": redesign_p or provider,
        "task_draft_model": draft_m or model,
        "task_draft_provider": draft_p or provider,
    }


class SaveContextDocRequest(BaseModel):
    repo_path: str
    filename: str = "spec.md"
    content: str


@router.get("/utils/context-docs")
def list_context_docs(repo_path: str = ".") -> dict:
    """
    {repo_path}/agent-data/context/ 안의 파일 목록을 반환한다.
    """
    context_dir = Path(repo_path).expanduser().resolve() / "agent-data" / "context"
    if not context_dir.exists():
        return {"docs": []}
    docs = sorted(
        [{"name": f.name, "size": f.stat().st_size} for f in context_dir.iterdir() if f.is_file()],
        key=lambda d: d["name"],
    )
    return {"docs": docs}


@router.get("/utils/context-docs/{filename}")
def get_context_doc(filename: str, repo_path: str = ".") -> dict:
    """
    {repo_path}/agent-data/context/{filename}의 내용을 반환한다.
    """
    path = Path(repo_path).expanduser().resolve() / "agent-data" / "context" / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"문서 '{filename}'를 찾을 수 없습니다.")
    return {"filename": filename, "content": path.read_text(encoding="utf-8")}


@router.post("/utils/save-context-doc")
def save_context_doc(body: SaveContextDocRequest) -> dict:
    """
    content를 {repo_path}/agent-data/context/{filename}에 저장한다.
    디렉토리가 없으면 자동 생성한다.
    """
    dest = Path(body.repo_path).expanduser().resolve() / "agent-data" / "context" / body.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body.content, encoding="utf-8")
    return {"saved": str(dest)}


def _resolve_initial(initial: str) -> str:
    """
    초기 경로를 절대경로로 변환한다.
    절대경로가 아니거나 존재하지 않으면 홈 디렉토리를 반환한다.
    """
    p = Path(initial).expanduser()
    if not p.is_absolute():
        return str(Path.home())
    # 존재하는 상위 디렉토리까지 올라감
    while p != p.parent:
        if p.exists():
            return str(p)
        p = p.parent
    return str(Path.home())


@router.get("/utils/browse")
def browse_path(
    type: str = Query("folder", pattern="^(folder|file)$"),
    initial: str = Query("~"),
):
    """
    macOS osascript를 통해 네이티브 파인더 다이얼로그를 열고
    선택된 경로를 반환한다.
    """
    expanded = _resolve_initial(initial)

    if type == "folder":
        script = (
            f'POSIX path of (choose folder '
            f'with prompt "폴더 선택" '
            f'default location POSIX file "{expanded}")'
        )
    else:
        script = (
            f'POSIX path of (choose file '
            f'with prompt "파일 선택" '
            f'default location POSIX file "{expanded}")'
        )

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=120,  # 2분 이상 선택 안 하면 취소로 간주
        )
    except subprocess.TimeoutExpired:
        return {"path": None, "cancelled": True}

    if result.returncode != 0:
        # 사용자가 취소하면 returncode=1, stderr에 "User canceled" 포함
        stderr = result.stderr.strip()
        if "canceled" in stderr.lower() or "cancelled" in stderr.lower():
            return {"path": None, "cancelled": True}
        raise HTTPException(status_code=500, detail=stderr or "osascript 실행 실패")

    path = result.stdout.strip().rstrip("/")
    return {"path": path, "cancelled": False}
