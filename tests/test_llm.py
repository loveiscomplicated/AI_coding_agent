"""
tests/test_llm.py

llm/ 레이어 동작 확인용 테스트.
Ollama가 실행 중이어야 하고 모델이 설치돼 있어야 함.

실행:
    python tests/test_llm.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from llm import create_client, LLMConfig


config_dict = {
    "ollama": LLMConfig(
        model="qwen2.5-coder:7b",
        temperature=0.0,
        system_prompt="You are a helpful coding assistant. Be concise.",
    ),  # example model
    "openai": LLMConfig(
        model="gpt-5-nano-2025-08-07",
        temperature=None,
        system_prompt="You are a helpful coding assistant. Be concise.",
    ),  # example model
}
provider_dict = {"ollama": "ollama", "openai": "openai"}


def test_connection(model_name: str):
    """LLM 서버 연결 및 모델 확인"""
    print("=" * 50)
    print(f"1. {model_name} 연결 테스트")

    config = config_dict[model_name]
    client = create_client(provider=provider_dict[model_name], config=config)

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


def test_chat(model_name: str):
    """단순 채팅 테스트"""
    print("\n2. 채팅 테스트")

    config = config_dict[model_name]
    client = create_client(provider=provider_dict[model_name], config=config)

    messages = client.build_messages(
        "파이썬으로 'hello world'를 출력하는 코드 한 줄만 써줘"
    )
    response = client.chat(messages)

    print(f"   모델: {response.model}")
    print(f"   응답: {response.content}")
    print(f"   토큰: input={response.input_tokens}, output={response.output_tokens}")


def test_stream(model_name: str):
    """스트리밍 테스트"""
    print("\n3. 스트리밍 테스트")

    config = config_dict[model_name]
    client = create_client(provider=provider_dict[model_name], config=config)

    messages = client.build_messages("1부터 5까지 숫자를 출력하는 파이썬 코드 짜줘")

    print("   응답 스트리밍: ", end="", flush=True)
    for token in client.stream(messages):
        print(token, end="", flush=True)
    print()


if __name__ == "__main__":
    model_list = ["ollama", "openai"]
    for model_name in model_list:
        if test_connection(model_name):
            test_chat(model_name)
            test_stream(model_name)
        print(f"\n{model_name} 완료!")
