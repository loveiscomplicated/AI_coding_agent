from __future__ import annotations

import inspect

import pytest

pytest.importorskip("openai")
pytest.importorskip("anthropic")
pytest.importorskip("ollama")

from llm.claude_client import ClaudeClient
from llm.glm_client import GlmClient
from llm.ollama_client import OllamaClient
from llm.openai_client import OpenaiClient


def _has_var_keyword(method) -> bool:
    sig = inspect.signature(method)
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def test_all_clients_stream_accept_kwargs():
    """ReactLoop가 stream(..., tools=...)를 호출해도 provider별로 깨지지 않아야 한다."""
    assert _has_var_keyword(OpenaiClient.stream)
    assert _has_var_keyword(GlmClient.stream)
    assert _has_var_keyword(ClaudeClient.stream)
    assert _has_var_keyword(OllamaClient.stream)
