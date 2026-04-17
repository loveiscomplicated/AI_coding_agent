"""
llm/gemini_client.py

Google Gemini LLM 연동 클라이언트 (google-genai SDK 기반).
base.py의 BaseLLMClient를 구현함.

사전 준비:
    uv add google-genai
    uv add python-dotenv

환경변수:
    GEMINI_API_KEY  (또는 GOOGLE_API_KEY)
"""

import base64
import json
import logging
import os
import time
from typing import Generator

try:
    from google import genai
    from google.genai import errors, types
except ImportError:
    raise ImportError("google-genai 패키지가 없어요. 실행: uv add google-genai")

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("dotenv 패키지가 없어요. 실행: uv add python-dotenv")

from .base import BaseLLMClient, LLMConfig, LLMResponse, Message
from .rate_limiter import estimate_tokens_from_messages, get_bucket

logger = logging.getLogger(__name__)

_MAX_RETRIES = 4
_BASE_DELAY = 2.0  # 초 (2 → 4 → 8 → 16)

load_dotenv()


# ── OpenAI/JSON-Schema → Gemini FunctionDeclaration 변환 ──────────────────────

_TYPE_MAP = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def _convert_schema(schema: dict) -> dict:
    """JSON Schema(소문자 type) → Gemini Schema(대문자 type) 재귀 변환."""
    if not isinstance(schema, dict):
        return schema
    out: dict = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            out[key] = _TYPE_MAP.get(value.lower(), value.upper())
        elif key == "properties" and isinstance(value, dict):
            out[key] = {k: _convert_schema(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = _convert_schema(value)
        else:
            out[key] = value
    return out


def _openai_tools_to_gemini(tools: list[dict]) -> list[dict]:
    """OpenAI 형식 tools → Gemini Tool(function_declarations=[...]) 형식."""
    declarations: list[dict] = []
    for t in tools:
        fn = t.get("function") or t
        declarations.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": _convert_schema(fn.get("parameters", {})),
            }
        )
    return [{"function_declarations": declarations}]


# ── Message → Gemini Contents 변환 ────────────────────────────────────────────

# Gemini 3 계열(thinking 모델)은 Part.function_call/text 에 thought_signature 가
# 붙어 오며, 같은 대화를 이어갈 때 이 signature 를 그대로 되돌려 보내지 않으면
# 400 INVALID_ARGUMENT 가 발생한다.
# 정규화된 block 안에 base64 문자열 형태로 보관했다가 재주입한다.
_SIG_KEY = "_gemini_thought_signature"


def _decode_sig(value: object) -> bytes | None:
    """block 에 저장된 base64 문자열(혹은 raw bytes) → bytes 복원."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, str) and value:
        try:
            return base64.b64decode(value)
        except (ValueError, TypeError):
            return None
    return None


def _to_gemini_contents(messages: list[Message]) -> list[dict]:
    """
    정규화된 Message 리스트 → Gemini contents 형식으로 변환.

    - system 메시지는 건너뜀 (GenerateContentConfig.system_instruction 으로 전달)
    - assistant → role="model": text → Part(text), tool_use → Part(function_call)
    - user     → role="user" : text → Part(text), tool_result → Part(function_response)

    block 에 `_gemini_thought_signature` 가 있으면 같은 Part 에 thought_signature
    를 주입해 Gemini 3 thinking 모델의 무결성 검사를 통과시킨다.
    """
    contents: list[dict] = []
    tool_name_by_id: dict[str, str] = {}  # tool_use_id → 함수명 복원용

    for msg in messages:
        if msg.role == "system":
            continue

        if isinstance(msg.content, str):
            role = "model" if msg.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg.content}]})
            continue

        if msg.role == "assistant":
            parts: list[dict] = []
            for b in msg.content:
                btype = b.get("type")
                sig = _decode_sig(b.get(_SIG_KEY))
                if btype == "text" and b.get("text"):
                    part: dict = {"text": b["text"]}
                    if sig is not None:
                        part["thought_signature"] = sig
                    parts.append(part)
                elif btype == "tool_use":
                    tool_name_by_id[b["id"]] = b["name"]
                    fc: dict = {
                        "name": b["name"],
                        "args": b.get("input") or {},
                    }
                    if b.get("id"):
                        fc["id"] = b["id"]
                    part = {"function_call": fc}
                    if sig is not None:
                        part["thought_signature"] = sig
                    parts.append(part)
            if parts:
                contents.append({"role": "model", "parts": parts})

        elif msg.role == "user":
            tool_results = [b for b in msg.content if b.get("type") == "tool_result"]
            if tool_results:
                parts = []
                for tr in tool_results:
                    tr_id = tr.get("tool_use_id", "")
                    name = tool_name_by_id.get(tr_id, tr.get("name", "tool"))
                    raw = tr.get("content", "")
                    if isinstance(raw, dict):
                        response_obj = raw
                    else:
                        response_obj = {"content": raw if isinstance(raw, str) else str(raw)}
                    if tr.get("is_error"):
                        response_obj = {**response_obj, "error": True}
                    fr: dict = {"name": name, "response": response_obj}
                    if tr_id:
                        fr["id"] = tr_id
                    parts.append({"function_response": fr})
                contents.append({"role": "user", "parts": parts})
            else:
                text = "\n".join(b.get("text", "") for b in msg.content if b.get("type") == "text")
                if text:
                    contents.append({"role": "user", "parts": [{"text": text}]})

    return contents


# ── 재시도 판정 ───────────────────────────────────────────────────────────────


def _is_retryable(e: Exception) -> bool:
    """429 / 500 / 502 / 503 / 504 등 일시적 서버 오류 판정."""
    if isinstance(e, errors.ServerError):
        return True
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code in (429, 500, 502, 503, 504):
        return True
    return False


class GeminiClient(BaseLLMClient):
    """
    Google Gemini API 클라이언트 (google-genai SDK).

    사용 예시:
        config = LLMConfig(model="gemini-2.5-flash", temperature=0.0)
        client = GeminiClient(config)
        response = client.chat([Message("user", "hello")])
        print(response.content)
    """

    def __init__(self, config: LLMConfig):
        super().__init__(config)

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY 환경변수가 설정되지 않았어요. .env 파일을 확인해주세요."
            )
        self._client = genai.Client(api_key=api_key)

    def _build_config(self, tools: list[dict] | None) -> types.GenerateContentConfig:
        kwargs: dict = {"max_output_tokens": self.config.max_tokens}
        if self.config.system_prompt:
            kwargs["system_instruction"] = self.config.system_prompt
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        if tools:
            kwargs["tools"] = _openai_tools_to_gemini(tools)
        return types.GenerateContentConfig(**kwargs)

    def _estimate_messages(self, messages: list[Message]) -> list[dict]:
        """rate_limiter 용 토큰 추정 입력 생성."""
        out: list[dict] = []
        if self.config.system_prompt:
            out.append({"role": "system", "content": self.config.system_prompt})
        for m in messages:
            if m.role == "system":
                continue
            if isinstance(m.content, str):
                out.append({"role": m.role, "content": m.content})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        """동기 방식 채팅 (과부하·속도제한 시 지수 백오프 재시도)."""
        tools = kwargs.get("tools")
        contents = _to_gemini_contents(messages)
        config = self._build_config(tools)

        bucket = get_bucket("gemini", self.config.model)
        estimate = estimate_tokens_from_messages(
            self._estimate_messages(messages), self.config.max_tokens
        )
        handle = bucket.reserve(estimate)

        response = None
        try:
            delay = _BASE_DELAY
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = self._client.models.generate_content(
                        model=self.config.model,
                        contents=contents,
                        config=config,
                    )
                    break
                except Exception as e:
                    if not _is_retryable(e) or attempt == _MAX_RETRIES:
                        raise
                    bucket.poison(delay)
                    logger.warning(
                        "Gemini API 일시 오류 (시도 %d/%d) — %.0f초 후 재시도: %s",
                        attempt + 1, _MAX_RETRIES, delay, e,
                    )
                    time.sleep(delay)
                    delay *= 2
        except Exception:
            bucket.reconcile(handle, 0)
            raise

        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0
        actual = (prompt_tokens + output_tokens) if usage else estimate
        bucket.reconcile(handle, actual)

        blocks: list = []
        has_tool_use = False
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) or []
            for idx, p in enumerate(parts):
                text = getattr(p, "text", None)
                fc = getattr(p, "function_call", None)
                raw_sig = getattr(p, "thought_signature", None)
                sig_b64 = (
                    base64.b64encode(raw_sig).decode("ascii")
                    if isinstance(raw_sig, (bytes, bytearray)) and raw_sig
                    else None
                )
                if text:
                    block: dict = {"type": "text", "text": text}
                    if sig_b64 and not fc:
                        # text-only Part 의 signature 는 text block 에 보관
                        block[_SIG_KEY] = sig_b64
                    blocks.append(block)
                if fc:
                    has_tool_use = True
                    fc_id = getattr(fc, "id", None) or f"call_{idx}_{getattr(fc, 'name', '')}"
                    fc_name = getattr(fc, "name", "") or ""
                    fc_args = getattr(fc, "args", None) or {}
                    if isinstance(fc_args, str):
                        try:
                            fc_args = json.loads(fc_args)
                        except json.JSONDecodeError:
                            fc_args = {}
                    if not isinstance(fc_args, dict):
                        fc_args = dict(fc_args)
                    block = {
                        "type": "tool_use",
                        "id": fc_id,
                        "name": fc_name,
                        "input": fc_args,
                    }
                    if sig_b64:
                        block[_SIG_KEY] = sig_b64
                    blocks.append(block)

        model_name = (
            getattr(response, "model_version", None)
            or getattr(response, "model", None)
            or self.config.model
        )

        return LLMResponse(
            content=blocks,
            model=model_name,
            stop_reason="tool_use" if has_tool_use else "end_turn",
            input_tokens=prompt_tokens,
            output_tokens=output_tokens,
            cached_read_tokens=cached_tokens,
            cached_write_tokens=0,
        )

    def stream(self, messages: list[Message], **kwargs) -> Generator[str, None, None]:
        """스트리밍 방식 채팅 — CLI에서 실시간 출력할 때 사용."""
        tools = kwargs.get("tools")
        contents = _to_gemini_contents(messages)
        config = self._build_config(tools)

        stream = self._client.models.generate_content_stream(
            model=self.config.model,
            contents=contents,
            config=config,
        )
        for chunk in stream:
            text = getattr(chunk, "text", None)
            if text:
                yield text

    def is_available(self) -> bool:
        """Gemini API에서 사용 가능한 모델이 있는지 확인."""
        try:
            available = self.list_models()
            target = self.config.model
            return any(target in m or m.endswith(target) for m in available)
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """현재 Gemini API에서 이용할 수 있는 모델 목록 반환."""
        try:
            models = self._client.models.list()
            return [getattr(m, "name", "") for m in models]
        except Exception:
            return []
