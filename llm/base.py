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
    WRITE_LOOP = "write_loop"  # write_deadline 내 쓰기 도구 미호출 → 탐색 루프 감지
    ABORTED = "aborted"  # 사용자 즉시 중단 요청


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

## 행동 원칙

- **말만 하지 말고 행동하세요.** 계획을 세웠으면 사용자 확인 없이 즉시 관련 도구를 호출하세요. 절대 "X를 하겠습니다"라고 선언만 하고 멈추지 마세요.
- **도구 호출 우선:** 모든 작업은 도구 호출을 통해 이루어져야 합니다. 파일을 읽거나 쓰거나 명령을 실행해야 할 때는 즉시 해당 도구를 호출하세요.
- 결론을 내리기 전에 관련 파일을 충분히 탐색하세요. 파일 하나만 보고 단정 짓지 마세요.
- 독립적인 도구 호출은 한 번의 응답에서 병렬로 실행하세요.
- 오류가 발생하면 원인을 분석하고 다른 방법으로 재시도하세요. 같은 도구를 같은 인자로 두 번 이상 호출하지 마세요.

## 도구 사용 형식 (중요)

도구를 호출할 때는 반드시 제공된 도구 정의에 따라 JSON 형식을 준수하세요. 만약 네이티브 도구 호출이 작동하지 않는다면, 다음과 같은 JSON 형식을 응답에 포함하세요:

```json
{
  "name": "도구_이름",
  "arguments": {
    "인자_이름": "값"
  }
}
```

## 도구 선택 우선순위

Python 파일 분석:
  1. get_outline      — 파일 구조 파악은 항상 이 도구로 시작하세요. read_file 금지.
  2. get_function_src — 특정 함수 내용이 필요할 때. 파일 전체를 읽지 마세요.
  3. get_imports      — import 목록만 필요할 때.
  4. read_file        — 위 세 도구로 해결이 안 될 때만 최후 수단으로 사용.

파일 수정:
  - 기존 파일 수정 → edit_file (old_str → new_str 방식)
  - 새 파일 생성   → write_file

## 코드 작성 원칙

- 코드를 작성하거나 수정한 뒤에는 반드시 execute_command로 실행해서 오류가 없는지 확인하세요.
- 실행 결과에 오류가 있으면 직접 수정 후 재실행하세요. 사용자에게 넘기지 마세요.
- edit_file 사용 전에 반드시 get_function_src 또는 read_file로 정확한 내용을 확인한 뒤 old_str을 구성하세요.

## 최종 답변

- 도구 실행 결과가 모두 나온 뒤에만 최종 답변을 작성하세요.
- 작업 결과를 간결하게 요약하세요.
- 언어는 한국어로 답변하세요.
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
