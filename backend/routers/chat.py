"""
backend/routers/chat.py — LLM API 프록시

프론트엔드 대신 백엔드에서 LLM API를 호출하여
API 키를 서버 측에서만 관리한다.

엔드포인트:
  POST /api/chat/stream    — 스트리밍 채팅 (SSE)
  POST /api/chat/complete  — 논스트리밍 (제목 생성, 컨텍스트 문서 등)
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import urllib.request
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.config import LLM_PROVIDER, LLM_MODEL_CAPABLE, LLM_MODEL_FAST, ANTHROPIC_API_KEY, OPENAI_API_KEY, ZAI_API_KEY
from llm import LLMConfig, Message, create_client

router = APIRouter()

# ── 모델 목록 ────────────────────────────────────────────────────────────────

_KNOWN_MODELS: dict[str, list[dict[str, str]]] = {
    "claude": [
        {"id": "claude-haiku-4-5-20251001", "name": "Haiku 4.5"},
        {"id": "claude-sonnet-4-6", "name": "Sonnet 4.6"},
        {"id": "claude-opus-4-6", "name": "Opus 4.6"},
    ],
    "openai": [
        {"id": "gpt-4.1-mini", "name": "GPT-4.1 Mini"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
        {"id": "gpt-4o", "name": "GPT-4o"},
        {"id": "o3-mini", "name": "o3 Mini"},
        {"id": "o3", "name": "o3"},
    ],
}


def _fetch_glm_models(api_key: str) -> list[dict[str, str]]:
    """Zai API에서 사용 가능한 GLM 모델 목록을 조회한다."""
    try:
        import urllib.request as _req
        req = _req.Request(
            "https://api.z.ai/api/paas/v4/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with _req.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return [{"id": m["id"], "name": m["id"], "provider": "glm"} for m in data.get("data", [])]
    except Exception:
        return []


def _fetch_ollama_models(host: str) -> list[dict[str, str]]:
    """Ollama 로컬 서버에서 설치된 모델 목록을 조회한다."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
        return [
            {"id": m["name"], "name": m["name"], "provider": "ollama"}
            for m in data.get("models", [])
        ]
    except Exception:
        return []


@router.get("/models")
async def list_models() -> dict[str, Any]:
    """API 키가 설정된 모든 provider의 모델 목록을 반환한다."""
    result: list[dict[str, str]] = []

    # Claude — API 키가 있거나 현재 provider인 경우
    if ANTHROPIC_API_KEY or LLM_PROVIDER == "claude":
        for m in _KNOWN_MODELS["claude"]:
            result.append({**m, "provider": "claude"})

    # OpenAI — API 키가 있거나 현재 provider인 경우
    if OPENAI_API_KEY or LLM_PROVIDER == "openai":
        for m in _KNOWN_MODELS["openai"]:
            result.append({**m, "provider": "openai"})

    # GLM (Zai) — API 키가 있거나 현재 provider인 경우, 동적으로 모델 목록 조회
    if ZAI_API_KEY or LLM_PROVIDER == "glm":
        glm_models = await asyncio.to_thread(_fetch_glm_models, ZAI_API_KEY)
        result.extend(glm_models)

    # Ollama — 로컬 서버 응답 시 포함 (키 불필요)
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    result.extend(await asyncio.to_thread(_fetch_ollama_models, host))

    return {
        "models": result,
        "default": LLM_MODEL_CAPABLE,
        "default_provider": LLM_PROVIDER,
    }


# ── 요청 모델 ─────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    max_tokens: int
    messages: list[dict[str, Any]]
    system: str | None = None
    model: str | None = None      # None이면 백엔드 기본 모델 사용
    provider: str | None = None   # None이면 LLM_PROVIDER 환경변수 사용
    use_fast_model: bool = False  # True면 LLM_MODEL_FAST 사용


def _resolve_model(req: ChatRequest) -> str:
    """요청에서 사용할 모델명을 결정한다."""
    if req.model:
        return req.model
    return LLM_MODEL_FAST if req.use_fast_model else LLM_MODEL_CAPABLE


def _resolve_provider(req: ChatRequest) -> str:
    """요청에서 사용할 provider를 결정한다."""
    return req.provider or LLM_PROVIDER


def _to_messages(raw: list[dict[str, Any]]) -> list[Message]:
    """프론트엔드 메시지 리스트를 Message 객체로 변환한다."""
    return [Message(role=m["role"], content=m.get("content", "")) for m in raw]


# ── 스트리밍 엔드포인트 ───────────────────────────────────────────────────────

@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """
    LLM 스트리밍 호출을 SSE로 프록시한다.

    SSE 이벤트 형식:
      data: {"type": "text_delta", "text": "..."}
      data: {"type": "done"}
      data: {"type": "error", "message": "..."}
    """
    model = _resolve_model(req)
    provider = _resolve_provider(req)
    system_prompt = req.system or ""
    messages = _to_messages(req.messages)

    async def generate():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _stream_thread():
            try:
                client = create_client(
                    provider,
                    LLMConfig(model=model, system_prompt=system_prompt, max_tokens=req.max_tokens),
                )
                for chunk in client.stream(messages):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, f"__ERROR__:{e}")
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=_stream_thread, daemon=True).start()

        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                if isinstance(chunk, str) and chunk.startswith("__ERROR__:"):
                    err = json.dumps({"type": "error", "message": chunk[10:]})
                    yield f"data: {err}\n\n"
                    return
                data = json.dumps({"type": "text_delta", "text": chunk})
                yield f"data: {data}\n\n"
        finally:
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── 논스트리밍 엔드포인트 ─────────────────────────────────────────────────────

@router.post("/complete")
async def chat_complete(req: ChatRequest):
    """
    LLM 단일 응답 호출을 프록시한다.
    제목 생성, 컨텍스트 문서 생성(비스트리밍) 등에 사용.
    """
    model = _resolve_model(req)
    provider = _resolve_provider(req)
    system_prompt = req.system or ""
    messages = _to_messages(req.messages)

    client = create_client(
        provider,
        LLMConfig(model=model, system_prompt=system_prompt, max_tokens=req.max_tokens),
    )
    response = await asyncio.to_thread(client.chat, messages)

    text = ""
    for block in response.content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block["text"]
            break
        if hasattr(block, "type") and block.type == "text":
            text = block.text
            break

    return {"text": text}
