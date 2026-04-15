"""
llm/claude_client.py

claude LLM 연동 클라이언트.
base.py의 BaseLLMClient를 구현함.

사전 준비:
    uv add anthropic
    uv add python-dotenv
"""

import logging
import os
import time
from typing import Generator

try:
    import anthropic
    from anthropic import omit
except ImportError:
    raise ImportError("anthropic 패키지가 없어요. 실행: uv add anthropic")

logger = logging.getLogger(__name__)

# RateLimitError / InternalServerError(529) 재시도 설정
_MAX_RETRIES = 4
_BASE_DELAY  = 2.0  # 초 (2 → 4 → 8 → 16)

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("dotenv 패키지가 없어요. 실행: uv add python-dotenv")

from .base import BaseLLMClient, LLMConfig, LLMResponse, Message
from .rate_limiter import estimate_tokens_from_messages, get_bucket

load_dotenv()


class ClaudeClient(BaseLLMClient):
    """
    Claude API 사용 클라이언트

    사용 예시:

    """

    def __init__(self, config: LLMConfig):
        super().__init__(config)

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY 환경변수가 설정되지 않았어요. .env 파일을 확인해주세요."
            )
        self._client = anthropic.Anthropic(api_key=api_key)

    def _build_system(self) -> list[dict] | str:
        """
        시스템 프롬프트를 prompt cache 형식으로 래핑한다.

        cache_control을 붙이면 Anthropic이 시스템 프롬프트를 최대 5분간 캐시한다.
        동일 에이전트 루프에서 반복 호출 시 input 토큰 비용을 ~90% 절감할 수 있다.
        시스템 프롬프트가 없으면 빈 문자열 반환(omit과 동일하게 동작).
        """
        prompt = self.config.system_prompt
        if not prompt:
            return ""
        return [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]

    def _build_api_messages(self, messages: list[Message]) -> list[dict]:
        """
        system 메시지를 제외하고 API 형식으로 변환한다.
        첫 번째 user 메시지(초기 태스크 프롬프트)에 cache_control을 추가해
        반복 루프에서 동일한 태스크 설명이 재전송될 때 캐시 히트를 유도한다.
        """
        api_msgs = [m.to_dict() for m in messages if m.role != "system"]
        if not api_msgs:
            return api_msgs
        first = api_msgs[0]
        if first["role"] == "user" and isinstance(first["content"], str):
            api_msgs = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": first["content"],
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                },
                *api_msgs[1:],
            ]
        return api_msgs

    def _apply_tools_cache(self, tools) -> list[dict]:
        """
        tools 목록 마지막 항목에 cache_control을 추가한다.
        Anthropic은 마지막으로 표시된 항목까지 모든 tool 정의를 캐싱하므로
        매 루프 호출마다 반복 전송되는 tool 스키마 토큰을 ~90% 절감한다.
        """
        if tools is omit or not tools:
            return tools
        tools = list(tools)
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools

    def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        """동기 방식 채팅 (과부하·속도제한 시 지수 백오프 재시도)"""
        api_messages = self._build_api_messages(messages)
        tools = self._apply_tools_cache(kwargs.get("tools", omit))

        bucket = get_bucket("claude", self.config.model)
        estimate = estimate_tokens_from_messages(api_messages, self.config.max_tokens)
        handle = bucket.reserve(estimate)
        try:
            delay = _BASE_DELAY
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = self._client.messages.create(
                        model=self.config.model,
                        system=self._build_system(),
                        messages=api_messages,  # type: ignore
                        tools=tools,
                        max_tokens=self.config.max_tokens,
                        temperature=self.config.temperature,  # type: ignore
                    )
                    actual = response.usage.input_tokens + response.usage.output_tokens
                    bucket.reconcile(handle, actual)
                    return LLMResponse(
                        content=response.content,
                        model=response.model,
                        stop_reason=response.stop_reason,
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                    )
                except (anthropic.RateLimitError, anthropic.InternalServerError) as e:
                    bucket.poison(delay)
                    if attempt == _MAX_RETRIES:
                        raise
                    logger.warning(
                        "API 일시 오류 (시도 %d/%d) — %.0f초 후 재시도: %s",
                        attempt + 1, _MAX_RETRIES, delay, e,
                    )
                    time.sleep(delay)
                    delay *= 2
        except Exception:
            bucket.reconcile(handle, 0)
            raise

    def stream(self, messages: list[Message], **kwargs) -> Generator[str, None, None]:
        """스트리밍 방식 채팅 — CLI에서 실시간 출력할 때 사용"""
        tools = kwargs.get("tools", kwargs.get("TOOLS_SCHEMA", omit))
        with self._client.messages.stream(
            model=self.config.model,
            system=self._build_system(),
            messages=self._build_api_messages(messages),  # type: ignore
            tools=self._apply_tools_cache(tools),
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,  # type: ignore
        ) as stream:
            yield from stream.text_stream

    def is_available(self) -> bool:
        """Anthropic API에서 사용 가능한 모델이 있는지 확인"""
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
        """현재 Anthropic API에서 이용할 수 있는 모델 목록 반환"""
        try:
            models = self._client.models.list()
            return [m.id for m in models]
        except Exception:
            return []
