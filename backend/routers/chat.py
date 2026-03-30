"""
backend/routers/chat.py — Anthropic API 프록시

프론트엔드 대신 백엔드에서 Anthropic API를 호출하여
API 키를 서버 측에서만 관리한다.

엔드포인트:
  POST /api/chat/stream    — 스트리밍 채팅 (SSE)
  POST /api/chat/complete  — 논스트리밍 (제목 생성, 컨텍스트 문서 등)
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.config import ANTHROPIC_API_KEY

router = APIRouter()

_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# ── 요청 모델 ─────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    model: str
    max_tokens: int
    messages: list[dict[str, Any]]
    system: str | None = None


# ── 스트리밍 엔드포인트 ───────────────────────────────────────────────────────

@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """
    Anthropic API 스트리밍 호출을 SSE로 프록시한다.

    SSE 이벤트 형식:
      data: {"type": "text_delta", "text": "..."}
      data: {"type": "done"}
      data: {"type": "error", "message": "..."}
    """
    async def generate():
        try:
            kwargs: dict[str, Any] = {
                "model": req.model,
                "max_tokens": req.max_tokens,
                "messages": req.messages,
            }
            if req.system:
                kwargs["system"] = req.system

            async with _client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                    ):
                        data = json.dumps({"type": "text_delta", "text": event.delta.text})
                        yield f"data: {data}\n\n"

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except anthropic.APIError as e:
            err = json.dumps({"type": "error", "message": str(e)})
            yield f"data: {err}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── 논스트리밍 엔드포인트 ─────────────────────────────────────────────────────

@router.post("/complete")
async def chat_complete(req: ChatRequest):
    """
    Anthropic API 단일 응답 호출을 프록시한다.
    제목 생성, 컨텍스트 문서 생성(비스트리밍) 등에 사용.
    """
    kwargs: dict[str, Any] = {
        "model": req.model,
        "max_tokens": req.max_tokens,
        "messages": req.messages,
    }
    if req.system:
        kwargs["system"] = req.system

    response = await _client.messages.create(**kwargs)
    text = response.content[0].text if response.content else ""
    return {"text": text}
