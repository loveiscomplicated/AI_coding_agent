"""
llm/base.py

모든 LLM provider가 구현해야 하는 베이스 인터페이스.
Ollama든 OpenAI든 이 클래스를 상속하면 orchestrator가 신경 안 써도 됨.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generator


@dataclass
class Message:
    """대화 메시지 단위"""

    role: str  # "system" | "user" | "assistant"
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    """LLM 응답 래퍼"""

    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0

    def __str__(self):
        return self.content


@dataclass
class LLMConfig:
    """LLM 설정값"""

    model: str
    temperature: float = 0.0
    max_tokens: int = 4096
    system_prompt: str = ""
    extra: dict = field(default_factory=dict)  # provider별 추가 옵션


class BaseLLMClient(ABC):
    """
    모든 LLM 클라이언트의 추상 베이스 클래스.

    새로운 provider를 추가하려면:
    1. 이 클래스를 상속
    2. chat() 과 stream() 을 구현
    3. llm/registry에 등록

    Example:
        class MyLLMClient(BaseLLMClient):
            def chat(self, messages, config):
                ...
    """

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    def chat(self, messages: list[Message]) -> LLMResponse:
        """
        동기 방식으로 LLM에 메시지를 보내고 응답을 받음.

        Args:
            messages: 대화 히스토리 (system + user + assistant 메시지들)

        Returns:
            LLMResponse: 응답 내용 + 메타데이터
        """
        pass

    @abstractmethod
    def stream(self, messages: list[Message]) -> Generator[str, None, None]:
        """
        스트리밍 방식으로 응답을 토큰 단위로 받음.
        CLI에서 실시간으로 출력할 때 사용.

        Args:
            messages: 대화 히스토리

        Yields:
            str: 토큰 단위 텍스트 조각
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """LLM 서버가 현재 연결 가능한지 확인"""
        pass

    def build_messages(
        self,
        user_input: str,
        history: list[Message] | None = None,
    ) -> list[Message]:
        """
        시스템 프롬프트 + 히스토리 + 새 입력을 합쳐서 메시지 리스트 생성.
        모든 클라이언트가 공통으로 사용.
        """
        messages = []

        if self.config.system_prompt:
            messages.append(Message(role="system", content=self.config.system_prompt))

        if history:
            messages.extend(history)

        messages.append(Message(role="user", content=user_input))
        return messages
