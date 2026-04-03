"""
tests/test_notifier.py — DiscordNotifier 단위 테스트

httpx 호출을 mock하여 실제 Discord API 없이 테스트한다.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from hotline.notifier import DiscordNotifier


# ── 픽스처 ───────────────────────────────────────────────────────────────────

def make_notifier() -> DiscordNotifier:
    return DiscordNotifier(token="test-token", guild_id=987654321, channel_id=123456789)


def make_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.is_success = (status_code < 400)
    resp.raise_for_status = MagicMock(
        side_effect=None if status_code < 400 else Exception(f"HTTP {status_code}")
    )
    return resp


# ── from_env ──────────────────────────────────────────────────────────────────

class TestFromEnv:
    def test_returns_notifier_when_both_vars_set(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "tok", "DISCORD_GUILD_ID": "999"}):
            n = DiscordNotifier.from_env(channel_id=123)
        assert n is not None
        assert n.channel_id == 123
        assert n.guild_id == 999

    def test_returns_none_when_token_missing(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "", "DISCORD_GUILD_ID": "999"}):
            n = DiscordNotifier.from_env()
        assert n is None

    def test_returns_none_when_guild_missing(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "tok", "DISCORD_GUILD_ID": ""}):
            n = DiscordNotifier.from_env()
        assert n is None

    def test_returns_none_when_guild_not_integer(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "tok", "DISCORD_GUILD_ID": "not-a-number"}):
            n = DiscordNotifier.from_env()
        assert n is None


# ── send ──────────────────────────────────────────────────────────────────────

class TestSend:
    def test_returns_message_id_on_success(self):
        notifier = make_notifier()
        mock_resp = make_response({"id": "msg-001"})

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.return_value = mock_resp
            result = notifier.send("테스트 메시지")

        assert result == "msg-001"

    def test_sends_to_correct_channel(self):
        notifier = make_notifier()
        mock_resp = make_response({"id": "msg-002"})

        with patch("httpx.Client") as MockClient:
            mock_client = MockClient.return_value.__enter__.return_value
            mock_client.post.return_value = mock_resp
            notifier.send("hello")

            call_args = mock_client.post.call_args
            assert "123456789" in call_args[0][0]  # URL에 channel_id 포함

    def test_truncates_long_message(self):
        notifier = make_notifier()
        long_msg = "a" * 3000
        mock_resp = make_response({"id": "msg-003"})

        with patch("httpx.Client") as MockClient:
            mock_client = MockClient.return_value.__enter__.return_value
            mock_client.post.return_value = mock_resp
            notifier.send(long_msg)

            sent_json = mock_client.post.call_args[1]["json"]
            assert len(sent_json["content"]) <= 2000

    def test_raises_on_api_error(self):
        import httpx as _httpx
        notifier = make_notifier()
        error_resp = make_response({}, status_code=401)
        error_resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock()
        )

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.return_value = error_resp
            with pytest.raises(_httpx.HTTPStatusError):
                notifier.send("test")


# ── wait_for_reply ────────────────────────────────────────────────────────────

class TestWaitForReply:
    def test_returns_user_message_content(self):
        notifier = make_notifier()
        messages = [
            {"id": "200", "content": "401로 해줘", "author": {"bot": False}},
        ]
        mock_resp = make_response(messages)

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.get.return_value = mock_resp
            with patch("time.sleep"):
                reply, last_id = notifier.wait_for_reply("100", timeout=10)

        assert reply == "401로 해줘"

    def test_skips_bot_messages(self):
        notifier = make_notifier()
        # 첫 번째 호출: 봇 메시지만, 두 번째 호출: 사용자 메시지
        bot_resp = make_response([
            {"id": "200", "content": "봇 메시지", "author": {"bot": True}},
        ])
        user_resp = make_response([
            {"id": "300", "content": "사용자 답변", "author": {"bot": False}},
        ])

        with patch("httpx.Client") as MockClient:
            mock_client = MockClient.return_value.__enter__.return_value
            mock_client.get.side_effect = [bot_resp, user_resp]
            with patch("time.sleep"):
                reply, last_id = notifier.wait_for_reply("100", timeout=30)

        assert reply == "사용자 답변"

    def test_returns_none_on_timeout(self):
        notifier = make_notifier()
        empty_resp = make_response([])

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.get.return_value = empty_resp
            with patch("time.sleep"):
                with patch("time.monotonic", side_effect=[0, 0, 999]):
                    reply, last_id = notifier.wait_for_reply("100", timeout=1)

        assert reply is None

    def test_ignores_http_errors_during_polling(self):
        import httpx as _httpx
        notifier = make_notifier()

        error_resp = MagicMock()
        error_resp.is_success = False
        user_resp = make_response([
            {"id": "200", "content": "괜찮아요", "author": {"bot": False}},
        ])

        with patch("httpx.Client") as MockClient:
            mock_client = MockClient.return_value.__enter__.return_value
            mock_client.get.side_effect = [error_resp, user_resp]
            with patch("time.sleep"):
                reply, last_id = notifier.wait_for_reply("100", timeout=30)

        assert reply == "괜찮아요"

    def test_returns_advanced_last_id(self):
        """타임아웃 시 bot 메시지를 지나간 last_id가 반환되는지 확인."""
        notifier = make_notifier()
        bot_resp = make_response([
            {"id": "500", "content": "봇 메시지", "author": {"bot": True}},
        ])
        empty_resp = make_response([])

        with patch("httpx.Client") as MockClient:
            mock_client = MockClient.return_value.__enter__.return_value
            mock_client.get.side_effect = [bot_resp, empty_resp]
            with patch("time.sleep"):
                with patch("time.monotonic", side_effect=[0, 0, 0, 999]):
                    reply, last_id = notifier.wait_for_reply("100", timeout=1)

        assert reply is None
        assert last_id == "500"
