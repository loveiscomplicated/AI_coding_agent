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

    def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        """동기 방식 채팅 (과부하·속도제한 시 지수 백오프 재시도)"""
        delay = _BASE_DELAY
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = self._client.messages.create(
                    model=self.config.model,
                    system=self.config.system_prompt,
                    messages=[m.to_dict() for m in messages if m.role != "system"],  # type: ignore
                    tools=kwargs.get("tools", omit),
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,  # type: ignore
                )
                return LLMResponse(
                    content=response.content,
                    model=response.model,
                    stop_reason=response.stop_reason,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
            except (anthropic.RateLimitError, anthropic.InternalServerError) as e:
                if attempt == _MAX_RETRIES:
                    raise
                logger.warning(
                    "API 일시 오류 (시도 %d/%d) — %.0f초 후 재시도: %s",
                    attempt + 1, _MAX_RETRIES, delay, e,
                )
                time.sleep(delay)
                delay *= 2

    def stream(self, messages: list[Message], **kwargs) -> Generator[str, None, None]:
        """스트리밍 방식 채팅 — CLI에서 실시간 출력할 때 사용"""
        with self._client.messages.stream(
            model=self.config.model,
            system=self.config.system_prompt,
            messages=[m.to_dict() for m in messages if m.role != "system"],  # type: ignore
            tools=kwargs.get("TOOLS_SCHEMA", omit),
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
