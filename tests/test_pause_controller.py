from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from orchestrator.run import PauseController


def test_handle_command_pause_resume_stop_and_ignore():
    ctrl = PauseController()

    assert ctrl.handle_command("무관한 대화") is None

    assert ctrl.handle_command("멈춰") == "paused"
    assert ctrl.is_paused is True

    assert ctrl.handle_command("계속") == "resumed"
    assert ctrl.is_paused is False

    assert ctrl.handle_command("중단") == "stopped"
    assert ctrl.is_stopped is True


def test_wait_if_paused_returns_false_on_resume():
    ctrl = PauseController()
    ctrl.handle_command("멈춰")

    t = threading.Thread(target=lambda: (time.sleep(0.01), ctrl.handle_command("계속")))
    t.start()
    stopped = ctrl.wait_if_paused()
    t.join(timeout=1)

    assert stopped is False
    assert ctrl.is_paused is False


def test_wait_if_paused_returns_true_on_stop():
    ctrl = PauseController()
    ctrl.handle_command("멈춰")

    t = threading.Thread(target=lambda: (time.sleep(0.01), ctrl.handle_command("중단")))
    t.start()
    stopped = ctrl.wait_if_paused()
    t.join(timeout=1)

    assert stopped is True
    assert ctrl.is_stopped is True


def test_direct_polling_detects_stop_keyword_and_sends_ack():
    class _NotifierStub:
        channel_id = 123
        _headers = {"Authorization": "Bot test"}

        def __init__(self):
            self.sent: list[str] = []

        def send(self, text: str):
            self.sent.append(text)

    ctrl = PauseController()
    notifier = _NotifierStub()
    ctrl.attach_notifier(notifier, after_message_id="100")

    resp = MagicMock()
    resp.is_success = True
    resp.json.return_value = [
        {"id": "101", "content": "중단", "author": {"bot": False}},
    ]

    with (
        patch("orchestrator.run.time.monotonic", return_value=100.0),
        patch("httpx.Client") as MockClient,
    ):
        MockClient.return_value.__enter__.return_value.get.return_value = resp
        assert ctrl.is_stopped is True

    assert notifier.sent


def test_direct_polling_respects_min_interval():
    class _NotifierStub:
        channel_id = 123
        _headers = {"Authorization": "Bot test"}

        def send(self, _text: str):
            return None

    ctrl = PauseController()
    ctrl.attach_notifier(_NotifierStub(), after_message_id="100")

    resp = MagicMock()
    resp.is_success = True
    resp.json.return_value = []

    with (
        patch("orchestrator.run.time.monotonic", side_effect=[100.0, 100.1]),
        patch("httpx.Client") as MockClient,
    ):
        MockClient.return_value.__enter__.return_value.get.return_value = resp
        assert ctrl.is_stopped is False
        assert ctrl.is_stopped is False

    # 두 번째 호출은 간격 제한으로 네트워크 요청이 나가지 않아야 함
    assert MockClient.return_value.__enter__.return_value.get.call_count == 1
