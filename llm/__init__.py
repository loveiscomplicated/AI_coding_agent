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
from .claude_client import ClaudeClient
from .glm_client import GlmClient
from .openai_client import OpenaiClient

# 나중에 provider 추가할 때 여기에 등록하면 됨
_PROVIDERS: dict[str, str] = {
    "ollama": "OllamaClient",
    "openai": "OpenaiClient",
    "claude": "ClaudeClient",
    "glm": "GlmClient",
    # "lmstudio": "LMStudioClient",
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

    if provider == "ollama":
        # 선택된 provider가 ollama일 때만 import한다.
        from .ollama_client import OllamaClient

        client_class = OllamaClient
    elif provider == "openai":
        client_class = OpenaiClient
    elif provider == "claude":
        client_class = ClaudeClient
    elif provider == "glm":
        client_class = GlmClient
    else:
        # if provider not in _PROVIDERS
        supported = ", ".join(_PROVIDERS.keys())
        raise ValueError(
            f"지원하지 않는 provider: '{provider}'. 가능한 것: {supported}"
        )
    return client_class(config, **kwargs)


def list_providers() -> list[str]:
    """등록된 provider 목록 반환"""
    return list(_PROVIDERS.keys())


def __getattr__(name: str):
    """OllamaClient를 지연 로딩한다."""
    if name == "OllamaClient":
        from .ollama_client import OllamaClient

        return OllamaClient
    raise AttributeError(name)


__all__ = [
    "BaseLLMClient",
    "LLMConfig",
    "LLMResponse",
    "Message",
    # -- model re-exporting --
    "OllamaClient",
    "OpenaiClient",
    "ClaudeClient",
    "GlmClient",
    "create_client",
    "list_providers",
]
