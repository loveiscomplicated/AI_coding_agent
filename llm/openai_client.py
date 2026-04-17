"""
llm/openai_client.py

OpenAI LLM 연동 클라이언트 (Chat Completions API).
base.py의 BaseLLMClient를 구현함.

사전 준비:
    uv add openai
    uv add python-dotenv
"""

import json
import logging
import os
import random
import re
import time
from typing import Generator

try:
    from openai import OpenAI, RateLimitError, BadRequestError
except ImportError:
    raise ImportError("openai 패키지가 없어요. 실행: uv add openai")

logger = logging.getLogger(__name__)

_MAX_RETRIES = 6
_BASE_DELAY = 2.0  # 초 (지수 백오프 기준값)

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("dotenv 패키지가 없어요. 실행: uv add python-dotenv")

from .base import BaseLLMClient, LLMConfig, LLMResponse, Message
from .rate_limiter import estimate_tokens_from_messages, get_bucket

load_dotenv()


def _parse_retry_after(e: RateLimitError) -> float | None:
    """RateLimitError에서 권장 대기 시간(초)을 추출한다.

    우선순위:
      1. Retry-After 응답 헤더
      2. 에러 메시지 내 "try again in X.XXXs" / "X.XXXms" 패턴
    """
    try:
        header = e.response.headers.get("retry-after")  # type: ignore[union-attr]
        if header:
            return float(header)
    except Exception:
        pass
    match = re.search(r"try again in (\d+(?:\.\d+)?)(ms|s)", str(e))
    if match:
        val, unit = float(match.group(1)), match.group(2)
        return val / 1000.0 if unit == "ms" else val
    return None


def _rate_limit_delay(attempt: int, e: RateLimitError) -> float:
    """재시도 대기 시간을 계산한다.

    - 에러가 권장 대기 시간을 포함하면 그 값을 사용한다.
    - 권장 시간이 없으면 지수 백오프(+소량 jitter).
    - 동기화는 rate_limiter.poison() 이 담당하므로 여기서는 큰 jitter 를 쓰지 않는다.
    """
    suggested = _parse_retry_after(e)
    if suggested is not None:
        return suggested
    base = _BASE_DELAY * (2**attempt)
    return base + random.uniform(0.0, base * 0.2)


def _to_openai_messages(messages: list[Message]) -> list[dict]:
    """
    정규화된 Message 리스트 → OpenAI Chat Completions 형식으로 변환.

    - system 메시지는 건너뜀 (chat()에서 별도로 추가)
    - assistant 메시지의 tool_use 블록 → tool_calls 필드
    - user 메시지의 tool_result 블록 → role="tool" 메시지
    """
    result = []
    for msg in messages:
        if msg.role == "system":
            continue

        if isinstance(msg.content, str):
            result.append({"role": msg.role, "content": msg.content})
            continue

        # list content
        if msg.role == "assistant":
            text_parts = [b["text"] for b in msg.content if b.get("type") == "text"]
            tool_calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        "arguments": json.dumps(b["input"]),
                    },
                }
                for b in msg.content
                if b.get("type") == "tool_use"
            ]
            entry: dict = {
                "role": "assistant",
                "content": "\n".join(text_parts) or "",
            }
            if tool_calls:
                entry["tool_calls"] = tool_calls
            result.append(entry)

        elif msg.role == "user":
            tool_results = [b for b in msg.content if b.get("type") == "tool_result"]
            if tool_results:
                for tr in tool_results:
                    result.append(
                        {
                            "role": "tool",
                            "tool_call_id": tr["tool_use_id"],
                            "content": tr["content"],
                        }
                    )
            else:
                text = "\n".join(b.get("text", "") for b in msg.content)
                result.append({"role": "user", "content": text})

    return result


class OpenaiClient(BaseLLMClient):
    """
    OpenAI Chat Completions API 클라이언트.

    사용 예시:
        config = LLMConfig(model="gpt-4.1-mini", temperature=0.0)
        client = OpenaiClient(config)
        response = client.chat([Message("user", "hello")])
        print(response.content)
    """

    def __init__(self, config: LLMConfig):
        super().__init__(config)

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY 환경변수가 설정되지 않았어요. .env 파일을 확인해주세요."
            )
        self._client = OpenAI(api_key=api_key)

    def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        """동기 방식 채팅 (Chat Completions API)"""
        create_kwargs: dict = {
            "model": self.config.model,
            "messages": [  # type: ignore[arg-type]
                {"role": "system", "content": self.config.system_prompt},
                *_to_openai_messages(messages),
            ],
            "max_completion_tokens": self.config.max_tokens,
        }
        tools = kwargs.get("tools")
        if tools:
            create_kwargs["tools"] = tools
        if self.config.temperature is not None:
            create_kwargs["temperature"] = self.config.temperature

        bucket = get_bucket("openai", self.config.model)
        estimate = estimate_tokens_from_messages(
            create_kwargs["messages"], self.config.max_tokens
        )
        handle = bucket.reserve(estimate)
        response = None
        try:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = self._client.chat.completions.create(**create_kwargs)  # type: ignore[arg-type]
                    break
                except RateLimitError as e:
                    delay = _rate_limit_delay(attempt, e)
                    bucket.poison(delay)
                    if attempt == _MAX_RETRIES:
                        raise
                    logger.warning(
                        "OpenAI RateLimitError (시도 %d/%d) — %.2f초 후 재시도: %s",
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                        e,
                    )
                    time.sleep(delay)
                except BadRequestError as e:
                    # reasoning 모델(o1/o3)은 temperature 파라미터 자체를 거부함
                    if "temperature" in str(e) and "temperature" in create_kwargs:
                        logger.warning(
                            "모델 %s이 temperature를 지원하지 않습니다. temperature 없이 재시도합니다.",
                            self.config.model,
                        )
                        del create_kwargs["temperature"]
                        response = self._client.chat.completions.create(**create_kwargs)  # type: ignore[arg-type]
                        break
                    raise
        except Exception:
            bucket.reconcile(handle, 0)
            raise

        usage = response.usage  # type: ignore[union-attr]
        actual = (usage.prompt_tokens + usage.completion_tokens) if usage else estimate
        bucket.reconcile(handle, actual)

        msg = response.choices[0].message  # type: ignore[union-attr]
        blocks: list = []
        if msg.content:
            blocks.append({"type": "text", "text": msg.content})
        for tc in msg.tool_calls or []:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.id,  # type: ignore[union-attr]
                    "name": tc.function.name,  # type: ignore[union-attr]
                    "input": json.loads(tc.function.arguments),  # type: ignore[union-attr]
                }
            )

        _cached_read = 0
        if (
            usage
            and hasattr(usage, "prompt_tokens_details")
            and usage.prompt_tokens_details
        ):
            _cached_read = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0

        return LLMResponse(
            content=blocks,
            model=response.model,  # type: ignore[union-attr]
            stop_reason="tool_use" if msg.tool_calls else "end_turn",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cached_read_tokens=_cached_read,
            cached_write_tokens=0,
        )

    def stream(self, messages: list[Message], **kwargs) -> Generator[str, None, None]:
        """스트리밍 방식 채팅"""
        create_kwargs: dict = {
            "model": self.config.model,
            "messages": [  # type: ignore[arg-type]
                {"role": "system", "content": self.config.system_prompt},
                *_to_openai_messages(messages),
            ],
            "max_completion_tokens": self.config.max_tokens,
            "stream": True,
        }
        tools = kwargs.get("tools")
        if tools:
            create_kwargs["tools"] = tools
        if self.config.temperature is not None:
            create_kwargs["temperature"] = self.config.temperature

        bucket = get_bucket("openai", self.config.model)
        estimate = estimate_tokens_from_messages(
            create_kwargs["messages"], self.config.max_tokens
        )
        handle = bucket.reserve(estimate)
        stream = None
        try:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    stream = self._client.chat.completions.create(**create_kwargs)  # type: ignore[arg-type]
                    break
                except RateLimitError as e:
                    delay = _rate_limit_delay(attempt, e)
                    bucket.poison(delay)
                    if attempt == _MAX_RETRIES:
                        raise
                    logger.warning(
                        "OpenAI RateLimitError/stream (시도 %d/%d) — %.2f초 후 재시도: %s",
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                        e,
                    )
                    time.sleep(delay)
                except BadRequestError as e:
                    if "temperature" in str(e) and "temperature" in create_kwargs:
                        logger.warning(
                            "모델 %s이 temperature를 지원하지 않습니다. temperature 없이 재시도합니다.",
                            self.config.model,
                        )
                        del create_kwargs["temperature"]
                        stream = self._client.chat.completions.create(**create_kwargs)  # type: ignore[arg-type]
                        break
                    raise
        except Exception:
            bucket.reconcile(handle, 0)
            raise
        # 스트리밍은 usage 를 내려주지 않으므로 예약(estimate) 을 그대로 유지한다.
        bucket.reconcile(handle, estimate)
        for chunk in stream:  # type: ignore[union-attr]
            delta = chunk.choices[0].delta.content  # type: ignore[union-attr]
            if delta:
                yield delta

    def is_available(self) -> bool:
        """OpenAI API에서 사용 가능한 모델이 있는지 확인"""
        try:
            models = self._client.models.list()
            available = [m.id for m in models.data]

            return any(
                self.config.model in m or m.startswith(self.config.model)
                for m in available
            )
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """현재 OpenAI API에서 이용할 수 있는 모델 목록 반환"""
        try:
            models = self._client.models.list()
            return [m.id for m in models]
        except Exception:
            return []
