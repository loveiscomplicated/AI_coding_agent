"""
llm/ollama_client.py

Ollama 로컬 LLM 연동 클라이언트.
base.py의 BaseLLMClient를 구현함.

사전 준비:
    uv add ollama
    ollama pull qwen2.5-coder:7b
"""

import json
from typing import Generator

try:
    import ollama
except ImportError:
    raise ImportError("ollama 패키지가 없어요. 실행: uv add ollama")

from .base import BaseLLMClient, LLMConfig, LLMResponse, Message


def _strip_special_tokens(text: str) -> str:
    """
    <|im_start|>, <|im_end|> 등 채팅 포맷 특수 토큰 및 마크다운 코드 블록 제거.
    """
    import re
    # <|...|> 형태 특수 토큰 제거
    text = re.sub(r"<\|[^|>]+\|>", "", text)
    # ```json ... ``` 코드 블록에서 내용 추출
    code_block = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_block:
        return code_block.group(1).strip()
    return text.strip()


def _parse_text_tool_call(text: str) -> dict | None:
    """
    structured tool_calls를 지원하지 않는 모델이 텍스트로 도구 호출을 출력할 때
    JSON을 파싱해 tool_use 블록으로 변환하는 폴백.

    지원 형식:
        {"name": "tool_name", "arguments": {...}}

    특수 토큰(<|im_start|> 등), 마크다운 코드 블록, 주변 텍스트가 있어도 처리함.
    """
    cleaned = _strip_special_tokens(text)

    # 1차: 정리된 텍스트 전체를 JSON으로 파싱
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and "name" in data and "arguments" in data:
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # 2차: 텍스트 내에서 { ... } JSON 객체를 추출
    start = cleaned.find("{")
    if start != -1:
        end = cleaned.rfind("}")
        if end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
                if isinstance(data, dict) and "name" in data and "arguments" in data:
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

    return None


def _to_ollama_messages(messages: list[Message]) -> list[dict]:
    """
    정규화된 Message 리스트 → Ollama 메시지 형식으로 변환.

    - assistant 메시지의 tool_use 블록 → tool_calls 필드
    - user 메시지의 tool_result 블록 → role="tool" 메시지 (name 포함)
    """
    # tool_use_id → tool_name 역방향 조회 맵 (result에 name 필드 포함을 위해)
    id_to_name: dict[str, str] = {}
    for msg in messages:
        if msg.role == "assistant" and isinstance(msg.content, list):
            for b in msg.content:
                if b.get("type") == "tool_use":
                    id_to_name[b["id"]] = b["name"]

    result = []
    for msg in messages:
        if isinstance(msg.content, str):
            result.append({"role": msg.role, "content": msg.content})
            continue

        # list content
        if msg.role == "assistant":
            text_parts = [b["text"] for b in msg.content if b.get("type") == "text"]
            tool_calls = [
                {"function": {"name": b["name"], "arguments": b["input"]}}
                for b in msg.content
                if b.get("type") == "tool_use"
            ]
            if tool_calls:
                result.append(
                    {"role": "assistant", "content": "", "tool_calls": tool_calls}
                )
            else:
                result.append({"role": "assistant", "content": "\n".join(text_parts)})

        elif msg.role == "user":
            tool_results = [b for b in msg.content if b.get("type") == "tool_result"]
            if tool_results:
                # role="tool"은 소형 모델에서 잘 무시되므로,
                # 도구 결과를 user 메시지 텍스트로 포함해 신뢰성을 높임
                parts = []
                for tr in tool_results:
                    name = id_to_name.get(tr["tool_use_id"], "tool")
                    status = "오류" if tr.get("is_error") else "결과"
                    parts.append(f"[{name} {status}]\n{tr['content']}")
                combined = "\n\n".join(parts)
                result.append({"role": "user", "content": combined})
            else:
                text = "\n".join(b.get("text", "") for b in msg.content)
                result.append({"role": "user", "content": text})

        else:
            result.append({"role": msg.role, "content": str(msg.content)})

    return result


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

    def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        """동기 방식 채팅"""
        response = self._client.chat(
            model=self.config.model,
            messages=_to_ollama_messages(messages),  # type: ignore[arg-type]
            options={
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
                **self.config.extra,
            },
            tools=kwargs.get("tools", None),
        )

        # Normalize → unified block format
        msg = response["message"]
        blocks: list = []
        raw_content: str = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        for i, tc in enumerate(tool_calls):
            blocks.append({
                "type": "tool_use",
                "id": f"call_{i}",
                "name": tc["function"]["name"],
                "input": tc["function"]["arguments"],
            })

        if not tool_calls and raw_content:
            # structured tool_calls 미지원 모델이 JSON 텍스트로 도구 호출을 출력하는 경우 폴백
            parsed = _parse_text_tool_call(raw_content)
            if parsed:
                blocks.append({
                    "type": "tool_use",
                    "id": "call_0",
                    "name": parsed["name"],
                    "input": parsed["arguments"],
                })
            else:
                blocks.append({"type": "text", "text": raw_content})

        return LLMResponse(
            content=blocks,
            model=response.get("model", self.config.model),
            stop_reason="tool_use" if blocks and blocks[0]["type"] == "tool_use" else "end_turn",
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
