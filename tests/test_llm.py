"""
tests/test_llm.py

llm/ 레이어 동작 확인용 테스트.

# Ollama
Ollama가 실행 중이어야 하고 모델이 설치돼 있어야 함.

# OpenAI
OPENAI_API_KEY가 .env에 존재해야 함.

# Anthropic (Claude)
ANTHROPIC_API_KEY가 .env에 존재해야 함.

실행:
    python tests/test_llm.py
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

if os.environ.get("RUN_LLM_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "실 LLM 연결이 필요한 수동 통합 테스트이므로 기본 pytest 스위트에서는 건너뜁니다.",
        allow_module_level=True,
    )

from llm import create_client, LLMConfig, BaseLLMClient
from core.loop import ReactLoop


config_dict = {
    "ollama": LLMConfig(
        model="qwen2.5-coder:14b",  # 7b로 변경 시 client_kwargs_dict도 함께 수정
        temperature=0.0,
        # default system prompt includes tool-use guidance; keep it for smaller models
    ),
    "openai": LLMConfig(
        model="gpt-5-nano-2025-08-07",
        # passing temperature is blocked in OpenaiClient and fixed to 1.0 in the internal logic
        # no support for temperature in openai's latest model anymore
        temperature=None,
        system_prompt="You are a helpful coding assistant. Be concise.",
    ),
    "claude": LLMConfig(
        model="claude-sonnet-4-6",
        temperature=0.0,
        system_prompt="You are a helpful coding assistant. Be concise.",
    ),
}
provider_dict = {"ollama": "ollama", "openai": "openai", "claude": "claude"}

# OllamaClient 전용 추가 kwargs.
# native_tool_role: 14b 이상은 True(기본값), 7b처럼 tool role을 무시하는 소형 모델은 False.
_ollama_client_kwargs: dict = {
    "native_tool_role": True,  # 14b: True / 7b로 바꿀 땐 False
}


def client_builder(model_name) -> BaseLLMClient:
    config = config_dict[model_name]
    extra = _ollama_client_kwargs if model_name == "ollama" else {}
    client = create_client(provider=provider_dict[model_name], config=config, **extra)
    return client


def test_connection(client: BaseLLMClient):
    """LLM 서버 연결 및 모델 확인"""
    print("=" * 50)
    print(f"1. {model_name} 연결 테스트")

    if client.is_available():
        print(f"   ✅ {model_name} 연결 성공, 모델 확인됨")
    else:
        models = client.list_models()
        print(f"   ❌ 모델을 찾을 수 없어요.")
        print(f"   설치된 모델: {models if models else '없음'}")
        print(f"   만약 ollama라면, 터미널에서 다음 명령어 실행:")
        print(f"   ollama pull qwen2.5-coder:7b")
        return False
    return True


def test_chat(client: BaseLLMClient):
    """단순 채팅 테스트"""
    print("\n2. 채팅 테스트")

    messages = client.build_messages(
        "파이썬으로 'hello world'를 출력하는 코드 한 줄만 써줘"
    )
    response = client.chat(messages)

    print(f"   모델: {response.model}")
    print(f"   응답: {response.content}")
    print(f"   토큰: input={response.input_tokens}, output={response.output_tokens}")


def test_stream(client: BaseLLMClient):
    """스트리밍 테스트"""
    print("\n3. 스트리밍 테스트")

    messages = client.build_messages("1부터 5까지 숫자를 출력하는 파이썬 코드 짜줘")

    print("   응답 스트리밍: ", end="", flush=True)
    for token in client.stream(messages):
        print(token, end="", flush=True)
    print()


def test_file_tool(client: BaseLLMClient):
    """file_tool 사용 테스트"""
    print("file_tool 사용 테스트")

    loop = ReactLoop(llm=client, max_iterations=5)
    result = loop.run("test_file_tools.py에서 import된 라이브러리들을 알려줘")
    print("응답:")
    print(f"   response: \n   {result.answer}")
    print(f"   iterations: {result.iterations}")
    print(f"   stop_reason: {result.stop_reason}")
    print(f"   succeeded: {result.succeeded}")
    print(f"   total_tool_calls: {result.total_tool_calls}")


def test_shell_tool(client: BaseLLMClient):
    """execute_command 도구 사용 테스트"""
    print("shell_tool 사용 테스트")

    loop = ReactLoop(llm=client, max_iterations=5)
    result = loop.run("현재 디렉토리의 파일 목록을 ls 명령어로 알려줘")
    print("응답:")
    print(f"   response: \n   {result.answer}")
    print(f"   iterations: {result.iterations}")
    print(f"   stop_reason: {result.stop_reason}")
    print(f"   succeeded: {result.succeeded}")
    print(f"   total_tool_calls: {result.total_tool_calls}")


def test_code_tool(client: BaseLLMClient):
    """code_tool 사용 테스트"""
    print("code_tool 사용 테스트")

    loop = ReactLoop(llm=client, max_iterations=5)
    result = loop.run("tools/code_tools.py의 함수 구조를 파악하고 각 함수가 어떤 역할인지 설명해줘")
    print("응답:")
    print(f"   response: \n   {result.answer}")
    print(f"   iterations: {result.iterations}")
    print(f"   stop_reason: {result.stop_reason}")
    print(f"   succeeded: {result.succeeded}")
    print(f"   total_tool_calls: {result.total_tool_calls}")


def test_main(model_name: str):
    client = client_builder(model_name)

    if test_connection(client):
        # test_chat(client)
        # test_stream(client)
        # test_file_tool(client)
        # test_shell_tool(client)
        test_code_tool(client)

    print(f"\n{model_name} 완료!")


if __name__ == "__main__":
    model_list = ["ollama", "openai", "claude"]
    for model_name in model_list:
        test_main(model_name)
