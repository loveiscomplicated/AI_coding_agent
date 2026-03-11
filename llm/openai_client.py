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
        load_dotenv()
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def chat(self)