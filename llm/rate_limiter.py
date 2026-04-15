"""
llm/rate_limiter.py

클라이언트 측 TPM/RPM rate limiter.

사용 이유:
    OpenAI / Anthropic / Zai API는 분당 토큰(TPM)과 분당 요청(RPM) 한도를 가진다.
    멀티 에이전트가 ThreadPoolExecutor로 병렬 호출하면 한도를 빠르게 초과해 429가
    쏟아진다. 기존 반응형 재시도는 thundering herd 문제를 완화할 뿐 해결하지 못한다.

    이 모듈은 호출 직전에 60초 sliding window 기반으로 토큰 예산을 *예약*하고,
    한도를 넘을 것 같으면 가장 오래된 이벤트가 창 밖으로 밀려날 때까지 block한다.

설계 요점:
    - (provider, model) 키별로 독립 버킷. 프로세스 전역 singleton.
    - reserve(estimate) → 예약 후 handle 반환. reconcile(handle, actual) 로 실 사용량
      반영. API 호출 실패시에는 reconcile(handle, 0) 으로 예약 회수.
    - 429 발생시 poison(delay) 로 모든 대기 스레드를 동일 시각까지 공통 지연.
    - 단일 프로세스 전제. 분산 조율은 현재 범위 밖.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


WINDOW_SECONDS = 60.0
_DEFAULT_SAFETY_RATIO = 0.85


@dataclass(frozen=True)
class RateLimit:
    """(provider, model) 쌍의 분당 한도."""
    tpm: int
    rpm: Optional[int] = None


# OpenAI Tier 1 기준 보수적 기본값. env 로 override 가능.
# 실제 한도는 https://platform.openai.com/account/limits 에서 확인.
DEFAULTS: dict[tuple[str, str], RateLimit] = {
    ("openai", "gpt-4.1"):      RateLimit(tpm=30_000,  rpm=500),
    ("openai", "gpt-4.1-mini"): RateLimit(tpm=200_000, rpm=500),
    ("openai", "gpt-4.1-nano"): RateLimit(tpm=200_000, rpm=500),
    ("openai", "gpt-4o"):       RateLimit(tpm=30_000,  rpm=500),
    ("openai", "gpt-4o-mini"):  RateLimit(tpm=200_000, rpm=500),
    ("openai", "o1"):           RateLimit(tpm=30_000,  rpm=500),
    ("openai", "o1-mini"):      RateLimit(tpm=200_000, rpm=500),
    ("openai", "o3-mini"):      RateLimit(tpm=200_000, rpm=500),
    # Claude / GLM 은 공식 한도 정책이 더 관대하므로 별도 지정이 없으면 미적용.
}

_FALLBACK = RateLimit(tpm=200_000, rpm=1_000)  # 미등록 모델 기본값(사실상 미적용)


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", s.upper()).strip("_")


def _resolve_limit(provider: str, model: str) -> RateLimit:
    """DEFAULTS + 환경변수 override 를 합쳐 최종 한도를 계산한다.

    env 키: LLM_TPM_<PROVIDER>_<MODEL>, LLM_RPM_<PROVIDER>_<MODEL>
    예: LLM_TPM_OPENAI_GPT_4_1=20000
    """
    base = DEFAULTS.get((provider, model), _FALLBACK)
    key = f"{_sanitize(provider)}_{_sanitize(model)}"
    tpm_env = os.environ.get(f"LLM_TPM_{key}")
    rpm_env = os.environ.get(f"LLM_RPM_{key}")
    tpm = int(tpm_env) if tpm_env else base.tpm
    rpm = int(rpm_env) if rpm_env else base.rpm

    # 안전 마진: 공식 한도의 N% 까지만 사용.
    try:
        ratio = float(os.environ.get("LLM_RATE_LIMIT_SAFETY_RATIO", _DEFAULT_SAFETY_RATIO))
    except ValueError:
        ratio = _DEFAULT_SAFETY_RATIO
    ratio = max(0.1, min(ratio, 1.0))
    tpm = max(1, int(tpm * ratio))
    if rpm is not None:
        rpm = max(1, int(rpm * ratio))
    return RateLimit(tpm=tpm, rpm=rpm)


@dataclass
class ReservationHandle:
    """reserve() 가 반환하는 예약 핸들. reconcile() 에 그대로 넘긴다."""
    timestamp: float
    tokens: int
    released: bool = field(default=False)


class _Bucket:
    """(provider, model) 단위 sliding-window rate bucket. 스레드 안전."""

    def __init__(self, provider: str, model: str, limit: RateLimit):
        self.provider = provider
        self.model = model
        self.limit = limit
        self._events: deque[tuple[float, int]] = deque()  # (timestamp, tokens)
        self._requests: deque[float] = deque()            # RPM 추적용
        self._poison_until: float = 0.0
        self._cond = threading.Condition()

    def _evict_locked(self, now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()
        while self._requests and self._requests[0] <= cutoff:
            self._requests.popleft()

    def _used_tokens_locked(self) -> int:
        return sum(tokens for _, tokens in self._events)

    def reserve(self, estimate: int) -> ReservationHandle:
        """예산이 확보될 때까지 block 후 예약."""
        estimate = max(0, int(estimate))
        with self._cond:
            while True:
                now = time.monotonic()

                # 429 poison: 공통 해제 시각까지 모두 대기
                if now < self._poison_until:
                    wait_for = self._poison_until - now
                    logger.debug(
                        "rate_limiter[%s/%s]: poisoned, waiting %.2fs",
                        self.provider, self.model, wait_for,
                    )
                    self._cond.wait(timeout=wait_for)
                    continue

                self._evict_locked(now)

                # RPM 체크
                if self.limit.rpm is not None and len(self._requests) >= self.limit.rpm:
                    oldest = self._requests[0]
                    wait_for = max(0.05, (oldest + WINDOW_SECONDS) - now)
                    logger.info(
                        "rate_limiter[%s/%s]: RPM full (%d/%d), waiting %.2fs",
                        self.provider, self.model, len(self._requests), self.limit.rpm, wait_for,
                    )
                    self._cond.wait(timeout=wait_for)
                    continue

                # TPM 체크
                used = self._used_tokens_locked()
                if used + estimate <= self.limit.tpm:
                    # 예약 성공
                    self._events.append((now, estimate))
                    self._requests.append(now)
                    return ReservationHandle(timestamp=now, tokens=estimate)

                # 예상 사용량이 한도 초과: 가장 오래된 이벤트가 창 밖으로
                # 밀려날 시각까지 대기. 단일 호출이 한도보다 크면 최대치까지
                # 압축해 내보낸다(무한 block 방지).
                if estimate > self.limit.tpm:
                    logger.warning(
                        "rate_limiter[%s/%s]: single request estimate %d > tpm %d; "
                        "capping reservation to tpm",
                        self.provider, self.model, estimate, self.limit.tpm,
                    )
                    estimate = self.limit.tpm
                    continue

                oldest_ts, _ = self._events[0]
                wait_for = max(0.05, (oldest_ts + WINDOW_SECONDS) - now)
                logger.info(
                    "rate_limiter[%s/%s]: TPM near limit (used=%d, want=%d, cap=%d), "
                    "waiting %.2fs",
                    self.provider, self.model, used, estimate, self.limit.tpm, wait_for,
                )
                self._cond.wait(timeout=wait_for)

    def reconcile(self, handle: ReservationHandle, actual: int) -> None:
        """실 사용량으로 예약을 보정한다.

        actual == 0 이면 호출 실패로 간주하고 예약을 통째로 회수한다.
        overcommit(예약 > 실사용)이 흔하므로 양의 차이는 돌려받는다.
        """
        if handle.released:
            return
        actual = max(0, int(actual))
        with self._cond:
            # 예약 시점 이벤트를 찾아 tokens 값을 actual 로 교체.
            # 일반적으로 동일 timestamp 는 유일하지만, 충돌 방지용으로 첫 매치만.
            for i, (ts, tokens) in enumerate(self._events):
                if ts == handle.timestamp and tokens == handle.tokens:
                    if actual == 0:
                        del self._events[i]
                    else:
                        self._events[i] = (ts, actual)
                    handle.released = True
                    self._cond.notify_all()
                    return
            # 이미 창 밖으로 밀려난 경우(드물지만 가능): 아무것도 하지 않음.
            handle.released = True

    def poison(self, retry_after: float) -> None:
        """429 발생시 호출. 모든 대기 스레드를 공통 시각까지 지연시킨다."""
        retry_after = max(0.0, float(retry_after))
        with self._cond:
            target = time.monotonic() + retry_after
            if target > self._poison_until:
                self._poison_until = target
            self._cond.notify_all()
        logger.warning(
            "rate_limiter[%s/%s]: poisoned for %.2fs after 429",
            self.provider, self.model, retry_after,
        )

    def snapshot(self) -> dict:
        """디버깅/테스트용 내부 상태 스냅샷."""
        with self._cond:
            now = time.monotonic()
            self._evict_locked(now)
            return {
                "provider": self.provider,
                "model": self.model,
                "tpm_limit": self.limit.tpm,
                "rpm_limit": self.limit.rpm,
                "used_tokens": self._used_tokens_locked(),
                "used_requests": len(self._requests),
                "poisoned": now < self._poison_until,
            }


class _Registry:
    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()

    def get(self, provider: str, model: str) -> _Bucket:
        key = (provider, model)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(provider, model, _resolve_limit(provider, model))
                self._buckets[key] = bucket
                logger.info(
                    "rate_limiter: registered bucket %s/%s tpm=%d rpm=%s",
                    provider, model, bucket.limit.tpm, bucket.limit.rpm,
                )
            return bucket

    def reset(self) -> None:
        """테스트용: 등록된 버킷을 모두 제거."""
        with self._lock:
            self._buckets.clear()


_registry = _Registry()


def get_bucket(provider: str, model: str) -> _Bucket:
    """프로세스 전역 rate bucket 을 반환. 없으면 생성."""
    return _registry.get(provider, model)


def reset_registry() -> None:
    """테스트 전용."""
    _registry.reset()


def estimate_tokens_from_messages(messages: list[dict], max_completion_tokens: int) -> int:
    """보수적 토큰 추정. 실제 tiktoken 호출을 피하기 위한 휴리스틱.

    - 메시지 내용을 모두 문자열화해 총 문자 수 / 4 로 근사.
    - 출력측은 max_completion_tokens 를 상한으로 가정.
    - 실제보다 과대평가 → 429 방지 우선. reconcile 에서 환수.
    """
    char_count = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            char_count += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("content") or ""
                    if isinstance(text, str):
                        char_count += len(text)
                    else:
                        char_count += len(str(text))
                else:
                    char_count += len(str(block))
        elif content is not None:
            char_count += len(str(content))
        # tool_calls 추가 비용(함수명·인자)도 대략 반영
        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            char_count += len(str(tc))
    input_estimate = char_count // 4 + 50  # 메시지 구조 오버헤드
    return input_estimate + max(0, int(max_completion_tokens))
