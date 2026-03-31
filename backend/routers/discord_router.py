"""
backend/routers/discord_router.py — Discord 핫라인 상태 API

GET  /api/discord/status   Discord 설정 및 서버 연결 상태 확인
GET  /api/discord/guilds   봇이 참여 중인 서버 목록
POST /api/discord/test     테스트 메시지 전송 (channel_id 필요)
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from backend.config import DISCORD_BOT_TOKEN, DISCORD_GUILD_ID
from hotline.notifier import DiscordNotifier

router = APIRouter()


def _get_notifier(channel_id: int | None = None) -> DiscordNotifier:
    """설정된 notifier를 반환. 미설정 시 422 에러."""
    notifier = DiscordNotifier.from_env(channel_id=channel_id)
    if notifier is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Discord가 설정되지 않았습니다. "
                ".env에 DISCORD_BOT_TOKEN과 DISCORD_GUILD_ID를 추가하세요."
            ),
        )
    return notifier


@router.get("/discord/status")
def discord_status() -> dict[str, Any]:
    """Discord 핫라인 설정 여부 및 서버 연결 상태를 반환한다."""
    configured = bool(DISCORD_BOT_TOKEN and DISCORD_GUILD_ID)
    if not configured:
        return {"configured": False, "guild_id": None, "connection": None}

    notifier = DiscordNotifier.from_env()
    if notifier is None:
        return {"configured": False, "guild_id": None, "connection": None}

    connection = notifier.validate()
    return {
        "configured": True,
        "guild_id": str(DISCORD_GUILD_ID),
        "connection": connection,
    }


@router.get("/discord/guilds")
def discord_guilds() -> dict[str, Any]:
    """봇이 참여 중인 서버(길드) 목록을 반환한다."""
    if not DISCORD_BOT_TOKEN:
        raise HTTPException(status_code=422, detail="DISCORD_BOT_TOKEN이 설정되지 않았습니다.")
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get("https://discord.com/api/v10/users/@me/guilds", headers=headers)
            if not resp.is_success:
                raise HTTPException(status_code=502, detail=f"Discord API 오류: {resp.status_code} {resp.text}")
            guilds = [{"id": g["id"], "name": g["name"]} for g in resp.json()]
            return {"guilds": guilds}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/discord/test")
def discord_test(channel_id: int | None = None) -> dict[str, Any]:
    """Discord 채널에 연결 테스트 메시지를 전송한다."""
    notifier = _get_notifier(channel_id)
    if notifier.channel_id is None:
        raise HTTPException(
            status_code=422,
            detail="channel_id가 필요합니다. 쿼리 파라미터로 ?channel_id=... 를 전달하세요.",
        )
    try:
        message_id = notifier.send("🔔 Multi-Agent Dev System — Discord 핫라인 연결 테스트")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Discord 전송 실패: {e}")
    return {"ok": True, "message_id": message_id}
