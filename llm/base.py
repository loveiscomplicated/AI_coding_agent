"""
llm/base.py

모든 LLM provider가 구현해야 하는 베이스 인터페이스.
Ollama든 OpenAI든 이 클래스를 상속하면 orchestrator가 신경 안 써도 됨.
"""

from enum import Enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generator


@dataclass
class Message:
    """대화 메시지 단위"""

    role: str  # "system" | "user" | "assistant"
    content: str | list  # list for structured content (tool_use, tool_result)

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


class StopReason(str, Enum):
    """ReAct loop의 StopReason"""

    END_TURN = "end_turn"  # LLM이 스스로 종료
    MAX_ITER = "max_iterations"  # 반복 한도 초과
    TOOL_ERROR = "tool_error"  # 도구 오류로 강제 종료
    LLM_ERROR = "llm_error"  # LLM 호출 실패


@dataclass
class LLMResponse:
    """LLM 응답 래퍼"""

    content: list  # list of normalized blocks (type/text/tool_use)
    model: str
    stop_reason: str | None = None  # "end_turn" | "tool_use" | "max_tokens" etc.
    input_tokens: int = 0
    output_tokens: int = 0

    def __str__(self):
        return str(self.content)


_DEFAULT_SYSTEM = """\
당신은 로컬 파일 시스템에서 작동하는 코딩 에이전트입니다.

작업 원칙:
- 파일을 읽기 전에 내용을 가정하지 마세요. 반드시 read_file로 먼저 확인하세요.
- 파일 수정 시 edit_file을 우선 사용하세요. write_file은 새 파일 생성에만 씁니다.
- 한 번의 응답에서 여러 도구를 병렬로 호출해도 됩니다.
- 오류가 발생하면 원인을 분석하고 다른 방법으로 재시도하세요.
- 최종 답변에는 어떤 작업을 했는지 간결하게 요약하세요.
"""


@dataclass
class LLMConfig:
    """LLM 설정값"""

    model: str
    temperature: None | float = 0.0
    max_tokens: int = 4096
    system_prompt: str = _DEFAULT_SYSTEM
    extra: dict = field(default_factory=dict)  # provider별 추가 옵션


class BaseLLMClient(ABC):
    """
    모든 LLM 클라이언트의 추상 베이스 클래스.

    새로운 provider를 추가하려면:
    1. 이 클래스를 상속
    2. chat(), stream() 과 is_available() 을 구현
    3. llm/registry에 등록

    Example:
        class MyLLMClient(BaseLLMClient):
            def chat(self, messages, config):
                ...
    """

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
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

    @abstractmethod
    def list_models(self) -> list[str]:
        """현재 이용 가능한 모델 목록 반환"""
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
