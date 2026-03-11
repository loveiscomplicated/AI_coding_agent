"""
llm/ollama_client.py

Ollama 로컬 LLM 연동 클라이언트.
base.py의 BaseLLMClient를 구현함.

사전 준비:
    uv add ollama
    ollama pull qwen2.5-coder:7b
"""

from typing import Generator

try:
    import ollama
except ImportError:
    raise ImportError("ollama 패키지가 없어요. 실행: uv add ollama")

from .base import BaseLLMClient, LLMConfig, LLMResponse, Message


class OllamaClient(BaseLLMClient):
    """
    Ollama 로컬 LLM 클라이언트.

    사용 예시:
        config = LLMConfig(model="qwen2.5-coder:7b", temperature=0.0)
        client = OllamaClient(config)
        response = client.chat([Message("user", "hello")])
        print(response.content)
    """

    def __init__(self, config: LLMConfig, host: str = "http://localhost:11434"):
        super().__init__(config)
        self.host = host
        self._client = ollama.Client(host=host)

    def chat(self, messages: list[Message]) -> LLMResponse:
        """동기 방식 채팅"""
        response = self._client.chat(
            model=self.config.model,
            messages=[m.to_dict() for m in messages],
            options={
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
                **self.config.extra,
            },
        )

        return LLMResponse(
            content=response["message"]["content"],
            model=response.get("model", self.config.model),
            input_tokens=response.get("prompt_eval_count", 0),
            output_tokens=response.get("eval_count", 0),
        )

    def stream(self, messages: list[Message]) -> Generator[str, None, None]:
        """스트리밍 방식 채팅 — CLI에서 실시간 출력할 때 사용"""
        stream = self._client.chat(
            model=self.config.model,
            messages=[m.to_dict() for m in messages],
            stream=True,
            options={
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
                **self.config.extra,
            },
        )

        for chunk in stream:
            token = chunk["message"]["content"]
            if token:
                yield token

    def is_available(self) -> bool:
        """Ollama 서버가 실행 중인지 + 모델이 있는지 확인"""
        try:
            models = self._client.list()
            available = [m["model"] for m in models.get("models", [])]

            # 모델명 prefix 매칭 (qwen2.5-coder:7b → qwen2.5-coder:7b 체크)
            return any(
                self.config.model in m or m.startswith(self.config.model)
                for m in available
            )
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """현재 Ollama에 설치된 모델 목록 반환"""
        try:
            models = self._client.list()
            return [m["model"] for m in models.get("models", [])]
        except Exception:
            return []
