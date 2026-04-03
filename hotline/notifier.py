"""
hotline/notifier.py — Discord 핫라인 클라이언트

Discord REST API를 직접 호출하여 알림 전송 및 사용자 답변 폴링을 수행한다.
discord.py 라이브러리 없이 httpx만 사용한다.

주요 기능:
  - send(content)              채널에 메시지 전송, message_id 반환
  - wait_for_reply(...)        사용자 답변 폴링 (봇 메시지 제외)
  - create_channel(name)       길드에 텍스트 채널 생성 (이미 있으면 재사용)
  - validate()                 길드 접근 가능 여부 검증
  - from_env(channel_id)       환경 변수에서 인스턴스 생성 (미설정 시 None)
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
_POLL_INTERVAL = 1   # 초
_DEFAULT_TIMEOUT = 300  # 초 (5분)


class DiscordNotifier:
    def __init__(self, token: str, guild_id: int, channel_id: int | None = None) -> None:
        self._guild_id = guild_id
        self._channel_id = channel_id
        self._headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        }

    # ── 채널 관리 ──────────────────────────────────────────────────────────────

    def create_channel(self, name: str) -> str:
        """
        길드에 텍스트 채널을 생성한다.
        같은 이름의 채널이 이미 있으면 기존 채널 ID를 반환한다.

        Args:
            name: 채널 이름 (소문자, 공백 없음 권장)

        Returns:
            채널 ID 문자열
        """
        # 채널 이름을 Discord 규칙에 맞게 정규화 (소문자, 공백→하이픈)
        safe_name = name.lower().replace(" ", "-")[:100]

        url = f"{_DISCORD_API}/guilds/{self._guild_id}/channels"
        with httpx.Client(timeout=10.0) as client:
            # 기존 채널 목록 조회
            resp = client.get(url, headers=self._headers)
            if resp.is_success:
                existing = [
                    c for c in resp.json()
                    if c.get("name") == safe_name and c.get("type") == 0
                ]
                if existing:
                    channel_id = existing[0]["id"]
                    self._channel_id = int(channel_id)
                    logger.info("Discord 기존 채널 재사용: #%s (id=%s)", safe_name, channel_id)
                    return channel_id

            # 새 채널 생성
            resp = client.post(
                url, headers=self._headers,
                json={"name": safe_name, "type": 0},
            )
            if not resp.is_success:
                logger.error(
                    "Discord 채널 생성 실패 (status=%d, body=%s)",
                    resp.status_code, resp.text,
                )
                resp.raise_for_status()
            channel_id = resp.json()["id"]
            self._channel_id = int(channel_id)
            logger.info("Discord 채널 생성 완료: #%s (id=%s)", safe_name, channel_id)
            return channel_id

    def validate(self) -> dict:
        """
        길드(서버) 접근 가능 여부를 검증한다.

        Returns:
            {"ok": True, "guild_name": "..."}
            {"ok": False, "status": 404, "error": "...", "hint": "..."}
        """
        url = f"{_DISCORD_API}/guilds/{self._guild_id}"
        _HINTS = {
            401: "DISCORD_BOT_TOKEN이 잘못됐거나 만료됐습니다.",
            403: "봇이 해당 서버에 접근 권한이 없습니다.",
            404: "서버를 찾을 수 없습니다. DISCORD_GUILD_ID가 올바른지, 봇이 서버에 초대됐는지 확인하세요.",
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=self._headers)
                if resp.is_success:
                    data = resp.json()
                    return {"ok": True, "guild_name": data.get("name", ""), "guild_id": str(self._guild_id)}
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                hint = _HINTS.get(resp.status_code, "Discord API 오류")
                logger.error(
                    "Discord 길드 검증 실패 (status=%d, body=%s)",
                    resp.status_code, resp.text,
                )
                return {
                    "ok": False,
                    "status": resp.status_code,
                    "error": body.get("message", resp.reason_phrase),
                    "discord_code": body.get("code"),
                    "hint": hint,
                }
        except httpx.TimeoutException:
            return {"ok": False, "status": None, "error": "요청 타임아웃", "hint": "네트워크 연결을 확인하세요."}
        except httpx.HTTPError as e:
            return {"ok": False, "status": None, "error": str(e), "hint": "네트워크 오류"}

    # ── 메시지 전송 ────────────────────────────────────────────────────────────

    def send(self, content: str) -> str:
        """
        채널에 텍스트 메시지를 전송한다.

        Returns:
            전송된 메시지의 ID (wait_for_reply에서 after 파라미터로 사용)

        Raises:
            RuntimeError: channel_id가 설정되지 않은 경우
            httpx.HTTPStatusError: API 오류 시
        """
        if self._channel_id is None:
            raise RuntimeError("channel_id가 설정되지 않았습니다. create_channel()을 먼저 호출하세요.")

        # Discord 메시지 길이 제한: 2000자
        if len(content) > 2000:
            content = content[:1997] + "…"

        url = f"{_DISCORD_API}/channels/{self._channel_id}/messages"
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=self._headers, json={"content": content})
            if not resp.is_success:
                logger.error(
                    "Discord 메시지 전송 실패 (status=%d, body=%s)",
                    resp.status_code, resp.text,
                )
                resp.raise_for_status()
            message_id: str = resp.json()["id"]
            logger.info("Discord 메시지 전송 완료 (id=%s)", message_id)
            return message_id

    # ── 답변 폴링 ──────────────────────────────────────────────────────────────

    def wait_for_reply(
        self,
        after_message_id: str,
        timeout: int = _DEFAULT_TIMEOUT,
        stop_check: Callable[[], bool] | None = None,
    ) -> tuple[str | None, str]:
        """
        after_message_id 이후에 사용자(봇 제외)가 보낸 첫 메시지를 기다린다.

        Args:
            after_message_id: 이 메시지 ID 이후의 메시지만 탐색
            timeout: 최대 대기 시간 (초). 초과 시 None 반환
            stop_check: 폴링 루프마다 호출되는 콜백. True 반환 시 즉시 None 반환
                        (예: lambda: pause_ctrl.is_stopped)

        Returns:
            (사용자 메시지 내용 또는 None, 마지막으로 확인한 메시지 ID)
            타임아웃/stop_check 시 (None, last_id) 반환 — last_id를 다음 호출에 재사용 가능
        """
        if self._channel_id is None:
            return None, after_message_id

        url = f"{_DISCORD_API}/channels/{self._channel_id}/messages"
        deadline = time.monotonic() + timeout
        last_id = after_message_id

        while time.monotonic() < deadline:
            if stop_check and stop_check():
                logger.info("wait_for_reply: stop_check 트리거 — 조기 종료")
                return None, last_id
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(
                        url,
                        headers=self._headers,
                        params={"after": last_id, "limit": 10},
                    )
                    if not resp.is_success:
                        # 429 Rate Limit: retry_after 만큼 대기
                        if resp.status_code == 429:
                            try:
                                retry_after = resp.json().get("retry_after", 1)
                                time.sleep(float(retry_after))
                            except Exception:
                                time.sleep(_POLL_INTERVAL)
                        continue

                    messages = resp.json()
                    # 봇 메시지 제외, 오래된 것부터 정렬
                    user_msgs = [
                        m for m in messages
                        if not m.get("author", {}).get("bot", False)
                        and m.get("content", "").strip()  # 빈 메시지(첨부파일·스티커 전용) 제외
                    ]
                    user_msgs.sort(key=lambda m: int(m["id"]))
                    if user_msgs:
                        reply = user_msgs[0]["content"].strip()
                        logger.info("Discord 답변 수신: %r", reply[:100])
                        return reply, last_id
                    if messages:
                        # 숫자 기준 max (문자열 비교 오류 방지)
                        last_id = str(max(int(m["id"]) for m in messages))
            except httpx.HTTPError as e:
                logger.warning("Discord 폴링 오류: %s", e)

            time.sleep(_POLL_INTERVAL)

        logger.info("Discord 답변 대기 타임아웃 (%ds)", timeout)
        return None, last_id

    # ── 명령 리스너 ────────────────────────────────────────────────────────────

    def get_latest_message_id(self) -> str | None:
        """채널의 최신 메시지 ID를 반환한다 (listen_for_commands의 시작점으로 사용)."""
        if self._channel_id is None:
            return None
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
        skip_check: Callable[[], bool] | None = None,
        urgent_callback: Callable[[str], bool] | None = None,
    ) -> None:
        """
        Discord 채널을 폴링하며 사용자 메시지 수신 시 callback(content)을 호출한다.
        stop_event가 set되면 루프를 종료한다.
        skip_check가 주어지고 True를 반환하면 일반 메시지를 건너뛴다 (핫라인 대화 중 경쟁 방지).
        urgent_callback이 주어지면 skip_check와 관계없이 모든 사용자 메시지에 대해 먼저 호출한다.
          True 반환 시 해당 메시지는 처리 완료로 간주하고 callback은 호출하지 않는다.
        블로킹 함수이므로 별도 스레드에서 호출해야 한다.
        """
        if self._channel_id is None:
            logger.warning("[listener] channel_id가 None — 리스너 시작하지 않음")
            return

        url = f"{_DISCORD_API}/channels/{self._channel_id}/messages"
        last_id = after_message_id
        logger.info("[listener] Discord 명령 리스너 시작 (channel=%s, after=%s)", self._channel_id, last_id)
        poll_count = 0

        while not (stop_event and stop_event.is_set()):
            try:
                with httpx.Client(timeout=10.0) as client:
                    params: dict = {"limit": 10}
                    if last_id:
                        params["after"] = last_id
                    resp = client.get(url, headers=self._headers, params=params)

                    if not resp.is_success:
                        logger.warning(
                            "[listener] Discord API 오류 (status=%d, body=%s)",
                            resp.status_code, resp.text[:200],
                        )
                        # 429 Rate Limit: retry_after 만큼 대기
                        if resp.status_code == 429:
                            try:
                                retry_after = resp.json().get("retry_after", 1)
                                time.sleep(float(retry_after))
                            except Exception:
                                time.sleep(_POLL_INTERVAL)
                        else:
                            time.sleep(_POLL_INTERVAL)
                        continue

                    messages = resp.json()
                    user_msgs = [
                        m for m in messages
                        if not m.get("author", {}).get("bot", False)
                    ]
                    user_msgs.sort(key=lambda m: int(m["id"]))

                    skipping = skip_check and skip_check()

                    for msg in user_msgs:
                        content = msg["content"].strip()
                        logger.info("[listener] 사용자 메시지 수신: %r (skip=%s)", content[:100], skipping)
                        try:
                            # urgent_callback은 skip 상태와 무관하게 항상 호출 (중단 등)
                            if urgent_callback and urgent_callback(content):
                                logger.info("[listener] urgent_callback 처리 완료: %r", content[:100])
                                continue  # urgent가 처리함 — 일반 callback 건너뜀
                            if not skipping:
                                callback(content)
                        except Exception as e:
                            # callback/urgent_callback 예외가 리스너 스레드를 죽이지 않도록 보호
                            logger.error("[listener] 콜백 예외 (무시): %s", e, exc_info=True)

                    if messages:
                        last_id = str(max(int(m["id"]) for m in messages))

            except httpx.TimeoutException as e:
                logger.debug("[listener] 폴링 타임아웃 (무시): %s", e)
            except httpx.HTTPError as e:
                logger.warning("[listener] HTTP 오류: %s", e)
            except Exception as e:
                # JSON 파싱 오류, KeyError 등 예상치 못한 예외로 스레드가 죽는 것을 방지
                logger.error("[listener] 예상치 못한 예외 (리스너 계속 실행): %s", e, exc_info=True)

            poll_count += 1
            if poll_count % 60 == 0:
                logger.info("[listener] heartbeat — %d회 폴링 완료 (last_id=%s)", poll_count, last_id)

            time.sleep(_POLL_INTERVAL)

        logger.info("[listener] Discord 명령 리스너 종료 (stop_event set)")

    # ── 팩토리 ─────────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, channel_id: int | None = None) -> "DiscordNotifier | None":
        """
        환경 변수 DISCORD_BOT_TOKEN, DISCORD_GUILD_ID에서 인스턴스를 생성한다.
        둘 중 하나라도 없으면 None을 반환한다 (Discord 기능 비활성화).

        Args:
            channel_id: 사용할 채널 ID. None이면 channel 미설정 상태로 생성.
        """
        token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        guild_id_str = os.getenv("DISCORD_GUILD_ID", "").strip()
        if not token or not guild_id_str:
            return None
        try:
            return cls(token, int(guild_id_str), channel_id)
        except ValueError:
            logger.warning("DISCORD_GUILD_ID가 정수가 아닙니다: %r", guild_id_str)
            return None

    @property
    def channel_id(self) -> int | None:
        return self._channel_id

    @property
    def guild_id(self) -> int:
        return self._guild_id
