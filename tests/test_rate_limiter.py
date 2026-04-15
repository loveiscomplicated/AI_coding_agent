from __future__ import annotations

import threading
import time

import pytest

from llm import rate_limiter
from llm.rate_limiter import (
    RateLimit,
    _Bucket,
    estimate_tokens_from_messages,
    get_bucket,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def _bucket(tpm=1000, rpm=None):
    return _Bucket("test", "model", RateLimit(tpm=tpm, rpm=rpm))


def test_reserve_allows_when_under_limit():
    b = _bucket(tpm=1000)
    h = b.reserve(200)
    assert h.tokens == 200
    snap = b.snapshot()
    assert snap["used_tokens"] == 200


def test_reconcile_reduces_overcommit():
    b = _bucket(tpm=1000)
    h = b.reserve(800)
    b.reconcile(h, 100)
    assert b.snapshot()["used_tokens"] == 100


def test_reconcile_zero_refunds_reservation():
    b = _bucket(tpm=1000)
    h = b.reserve(500)
    b.reconcile(h, 0)
    assert b.snapshot()["used_tokens"] == 0


def test_reserve_blocks_when_over_tpm_and_unblocks_on_reconcile():
    b = _bucket(tpm=1000)
    h1 = b.reserve(900)

    result = {}

    def worker():
        t0 = time.monotonic()
        h = b.reserve(500)
        result["handle"] = h
        result["elapsed"] = time.monotonic() - t0

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.1)
    assert t.is_alive(), "worker should be blocked since 900 + 500 > 1000"

    # reconcile frees budget → worker proceeds
    b.reconcile(h1, 100)
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert result["handle"].tokens == 500
    assert result["elapsed"] < 1.5


def test_poison_delays_new_reservations():
    b = _bucket(tpm=100_000)
    b.poison(0.3)
    t0 = time.monotonic()
    b.reserve(10)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.25, f"expected >=0.25s wait, got {elapsed}"


def test_poison_broadcast_to_multiple_waiters():
    b = _bucket(tpm=100_000)
    b.poison(0.4)
    elapsed: list[float] = []

    def worker():
        t0 = time.monotonic()
        b.reserve(10)
        elapsed.append(time.monotonic() - t0)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=2.0)
    assert len(elapsed) == 4
    # 모두 약 0.4s 근처에서 풀려야 함
    assert max(elapsed) < 1.0
    assert min(elapsed) >= 0.3


def test_registry_returns_same_bucket():
    a = get_bucket("openai", "gpt-4.1")
    b = get_bucket("openai", "gpt-4.1")
    assert a is b


def test_env_override(monkeypatch):
    monkeypatch.setenv("LLM_TPM_OPENAI_GPT_4_1", "10000")
    monkeypatch.setenv("LLM_RATE_LIMIT_SAFETY_RATIO", "1.0")
    reset_registry()
    bucket = get_bucket("openai", "gpt-4.1")
    assert bucket.limit.tpm == 10000


def test_rpm_limit_blocks():
    b = _bucket(tpm=1_000_000, rpm=2)
    b.reserve(1)
    b.reserve(1)

    def worker():
        b.reserve(1)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.1)
    assert t.is_alive(), "third reserve should block due to RPM"
    t.join(timeout=0.1)  # cleanup; test still asserted the block


def test_estimate_tokens_handles_str_and_list_content():
    messages = [
        {"role": "user", "content": "hello" * 100},  # 500 chars
        {"role": "assistant", "content": [{"type": "text", "text": "x" * 200}]},
    ]
    est = estimate_tokens_from_messages(messages, max_completion_tokens=1000)
    # 대략 (500+200)/4 + 50 + 1000 ~= 1225
    assert 1100 <= est <= 1400


def test_oversized_single_request_caps_to_tpm():
    b = _bucket(tpm=500)
    h = b.reserve(10_000)  # must not deadlock
    assert h.tokens == 500
