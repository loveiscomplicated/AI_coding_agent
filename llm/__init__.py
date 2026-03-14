"""
llm/__init__.py

LLM 클라이언트 팩토리.
설정 파일의 provider 이름만 보고 알맞은 클라이언트를 돌려줌.

사용 예시:
    from llm import create_client, LLMConfig

    config = LLMConfig(model="qwen2.5-coder:7b")
    client = create_client(provider="ollama", config=config)
    response = client.chat(client.build_messages("파이썬으로 피보나치 짜줘"))
    print(response)
"""

from .base import BaseLLMClient, LLMConfig, LLMResponse, Message
from .ollama_client import OllamaClient
from .openai_client import OpenaiClient
from .claude_client import ClaudeClient

# 나중에 provider 추가할 때 여기에 등록하면 됨
_PROVIDERS: dict[str, type[BaseLLMClient]] = {
    "ollama": OllamaClient,
    "openai": OpenaiClient,
    "claude": ClaudeClient,
    # "lmstudio": LMStudioClient,
}


def create_client(provider: str, config: LLMConfig, **kwargs) -> BaseLLMClient:
    """
    provider 이름으로 LLM 클라이언트를 생성해서 반환.

    Args:
        provider: "ollama" 등 (config.yaml의 provider 값과 일치)
        config: LLMConfig 설정
        **kwargs: 클라이언트별 추가 인자 (예: host="http://...")

    Raises:
        ValueError: 등록되지 않은 provider일 때
    """
    if provider not in _PROVIDERS:
        supported = ", ".join(_PROVIDERS.keys())
        raise ValueError(
            f"지원하지 않는 provider: '{provider}'. 가능한 것: {supported}"
        )

    client_class = _PROVIDERS[provider]
    return client_class(config, **kwargs)


def list_providers() -> list[str]:
    """등록된 provider 목록 반환"""
    return list(_PROVIDERS.keys())


__all__ = [
    "BaseLLMClient",
    "LLMConfig",
    "LLMResponse",
    "Message",
    # -- model re-exporting --
    "OllamaClient",
    "OpenaiClient",
    "create_client",
    "list_providers",
]
