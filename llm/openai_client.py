"""
llm/openai_client.py

Openai 로컬 LLM 연동 클라이언트.
base.py의 BaseLLMClient를 구현함.

사전 준비:
    uv add openai
    uv add python-dotenv
"""

import os
from typing import Generator

try:
    from openai import OpenAI
except ImportError:
    raise ImportError("openai 패키지가 없어요. 실행: uv add openai")

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("dotenv 패키지가 없어요. 실행: uv add python-dotenv")

from .base import BaseLLMClient, LLMConfig, LLMResponse, Message

load_dotenv()


class OpenaiClient(BaseLLMClient):
    """
    Openai 로컬 LLM 클라이언트

    사용 예시:
        config = LLMConfig(model="", temperature=0.0)
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

    def chat(self, messages: list[Message]) -> LLMResponse:
        """동기 방식 채팅"""
        response = self._client.responses.create(
            model=self.config.model,  # ex) gpt-5-pro, gpt-5-nano-2025-08-07
            instructions=self.config.system_prompt,
            input=[m.to_dict() for m in messages if m.role != "system"],
            max_output_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        return LLMResponse(
            content=response.output_text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    def stream(self, messages: list[Message]) -> Generator[str, None, None]:
        stream = self._client.responses.create(
            model=self.config.model,
            instructions=self.config.system_prompt,
            input=[m.to_dict() for m in messages if m.role != "system"],  # type: ignore
            max_output_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            stream=True,
        )  # type: ignore
        for event in stream:
            if event.type == "response.output_text.delta":
                yield event.delta

    def is_available(self) -> bool:
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
        """현재 Openai API에서 이용할 수 있는 모델 목록 반환"""
        try:
            models = self._client.models.list()
            return [m.id for m in models]
        except Exception:
            return []
