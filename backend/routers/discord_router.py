"""
backend/routers/discord_router.py — Discord 핫라인 상태 API

GET  /api/discord/status   Discord 설정 여부 확인
POST /api/discord/test     테스트 메시지 전송
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.config import DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID
from hotline.notifier import DiscordNotifier

router = APIRouter()


def _get_notifier() -> DiscordNotifier:
    """설정된 notifier를 반환. 미설정 시 422 에러."""
    notifier = DiscordNotifier.from_env()
    if notifier is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Discord가 설정되지 않았습니다. "
                ".env에 DISCORD_BOT_TOKEN과 DISCORD_CHANNEL_ID를 추가하세요."
            ),
        )
    return notifier


@router.get("/discord/status")
def discord_status() -> dict[str, Any]:
    """Discord 핫라인 설정 여부를 반환한다."""
    configured = bool(DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID)
    return {
        "configured": configured,
        "channel_id": str(DISCORD_CHANNEL_ID) if DISCORD_CHANNEL_ID else None,
    }


@router.post("/discord/test")
def discord_test() -> dict[str, Any]:
    """Discord 채널에 연결 테스트 메시지를 전송한다."""
    notifier = _get_notifier()
    try:
        message_id = notifier.send("🔔 Multi-Agent Dev System — Discord 핫라인 연결 테스트")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Discord 전송 실패: {e}")
    return {"ok": True, "message_id": message_id}
