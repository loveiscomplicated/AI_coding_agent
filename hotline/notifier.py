"""
hotline/notifier.py — Discord 핫라인 클라이언트

Discord REST API를 직접 호출하여 알림 전송 및 사용자 답변 폴링을 수행한다.
discord.py 라이브러리 없이 httpx만 사용한다.

주요 기능:
  - send(content)          채널에 메시지 전송, message_id 반환
  - wait_for_reply(...)    사용자 답변 폴링 (봇 메시지 제외)
  - from_env()             환경 변수에서 인스턴스 생성 (미설정 시 None)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

_DISCORD_API = "https://discord.com/api/v10"
_POLL_INTERVAL = 3   # 초
_DEFAULT_TIMEOUT = 300  # 초 (5분)


class DiscordNotifier:
    def __init__(self, token: str, channel_id: int) -> None:
        self._channel_id = channel_id
        self._headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        }

    # ── 메시지 전송 ────────────────────────────────────────────────────────────

    def send(self, content: str) -> str:
        """
        채널에 텍스트 메시지를 전송한다.

        Returns:
            전송된 메시지의 ID (wait_for_reply에서 after 파라미터로 사용)

        Raises:
            httpx.HTTPStatusError: API 오류 시
        """
        # Discord 메시지 길이 제한: 2000자
        if len(content) > 2000:
            content = content[:1997] + "…"

        url = f"{_DISCORD_API}/channels/{self._channel_id}/messages"
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=self._headers, json={"content": content})
            resp.raise_for_status()
            message_id: str = resp.json()["id"]
            logger.info("Discord 메시지 전송 완료 (id=%s)", message_id)
            return message_id

    # ── 답변 폴링 ──────────────────────────────────────────────────────────────

    def wait_for_reply(
        self,
        after_message_id: str,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> str | None:
        """
        after_message_id 이후에 사용자(봇 제외)가 보낸 첫 메시지를 기다린다.

        Args:
            after_message_id: 이 메시지 ID 이후의 메시지만 탐색
            timeout: 최대 대기 시간 (초). 초과 시 None 반환

        Returns:
            사용자 메시지 내용, 타임아웃 시 None
        """
        url = f"{_DISCORD_API}/channels/{self._channel_id}/messages"
        deadline = time.monotonic() + timeout
        last_id = after_message_id

        while time.monotonic() < deadline:
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(
                        url,
                        headers=self._headers,
                        params={"after": last_id, "limit": 10},
                    )
                    if resp.is_success:
                        messages = resp.json()
                        # 봇 메시지 제외, 오래된 것부터 정렬
                        user_msgs = [
                            m for m in messages
                            if not m.get("author", {}).get("bot", False)
                        ]
                        user_msgs.sort(key=lambda m: int(m["id"]))
                        if user_msgs:
                            reply = user_msgs[0]["content"].strip()
                            logger.info("Discord 답변 수신: %r", reply[:100])
                            return reply
                        if messages:
                            last_id = max(m["id"] for m in messages)
            except httpx.HTTPError as e:
                logger.warning("Discord 폴링 오류: %s", e)

            time.sleep(_POLL_INTERVAL)

        logger.info("Discord 답변 대기 타임아웃 (%ds)", timeout)
        return None

    # ── 명령 리스너 ────────────────────────────────────────────────────────────

    def get_latest_message_id(self) -> str | None:
        """채널의 최신 메시지 ID를 반환한다 (listen_for_commands의 시작점으로 사용)."""
        url = f"{_DISCORD_API}/channels/{self._channel_id}/messages"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=self._headers, params={"limit": 1})
                if resp.is_success:
                    messages = resp.json()
                    if messages:
                        return messages[0]["id"]
        except httpx.HTTPError as e:
            logger.warning("최신 메시지 ID 조회 실패: %s", e)
        return None

    def listen_for_commands(
        self,
        callback: Callable[[str], None],
        after_message_id: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        """
        Discord 채널을 폴링하며 사용자 메시지 수신 시 callback(content)을 호출한다.
        stop_event가 set되면 루프를 종료한다.
        블로킹 함수이므로 별도 스레드에서 호출해야 한다.

        Args:
            callback: 사용자 메시지 내용을 받는 콜백
            after_message_id: 이 ID 이후 메시지부터 폴링 (None이면 현재 최신부터)
            stop_event: 이 이벤트가 set되면 폴링 종료
        """
        url = f"{_DISCORD_API}/channels/{self._channel_id}/messages"
        last_id = after_message_id

        while not (stop_event and stop_event.is_set()):
            try:
                with httpx.Client(timeout=10.0) as client:
                    params: dict = {"limit": 10}
                    if last_id:
                        params["after"] = last_id
                    resp = client.get(url, headers=self._headers, params=params)
                    if resp.is_success:
                        messages = resp.json()
                        user_msgs = [
                            m for m in messages
                            if not m.get("author", {}).get("bot", False)
                        ]
                        user_msgs.sort(key=lambda m: int(m["id"]))
                        for msg in user_msgs:
                            callback(msg["content"].strip())
                        # last_id를 가장 최신 메시지로 전진 (봇 포함)
                        if messages:
                            last_id = max(m["id"] for m in messages)
            except httpx.HTTPError as e:
                logger.warning("Discord 명령 폴링 오류: %s", e)

            time.sleep(_POLL_INTERVAL)

    # ── 팩토리 ─────────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "DiscordNotifier | None":
        """
        환경 변수 DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID에서 인스턴스를 생성한다.
        둘 중 하나라도 없으면 None을 반환한다 (Discord 기능 비활성화).
        """
        token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        channel_id_str = os.getenv("DISCORD_CHANNEL_ID", "").strip()
        if not token or not channel_id_str:
            return None
        try:
            return cls(token, int(channel_id_str))
        except ValueError:
            logger.warning("DISCORD_CHANNEL_ID가 정수가 아닙니다: %r", channel_id_str)
            return None

    @property
    def channel_id(self) -> int:
        return self._channel_id
