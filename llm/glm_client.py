"""
llm/glm_client.py

Zai LLM 연동 클라이언트 (Chat Completions API).
Openai python SDK를 지원하므로 이를 사용.
base.py의 BaseLLMClient를 구현함.

사전 준비:
    uv add openai
    uv add python-dotenv

----------------------------------------------------------------------
GLM (Zhipu Z.ai) Context Caching 용 messages 배열 레이아웃
----------------------------------------------------------------------

Z.ai 도 동일한 prefix 기반 context caching 을 제공한다 (반복되는 prefix
토큰을 더 싼 단가로 청구). 따라서 openai_client.py 와 동일한 결정성
규칙을 따른다:

    messages[0]   system       — self.config.system_prompt (파이프라인 단위 불변)
    messages[1]   user         — 첫 태스크 기술 (루프 내 불변)
    messages[2..] assistant /  — tool_calls / tool_results 턴 (가변)
                  tool

GLM 특이 사항 (검토 필요):
  - 본 클라이언트는 기존부터 assistant 메시지에서 tool_calls 가 있을 때
    content 키를 생략해왔다. Z.ai 공식 문서로는 이것이 필수 제약인지
    명확히 확인되지 않았으며, 현재 코드의 기존 동작을 보존하기 위한 것이다.
    추후 공식 스키마로 검증하여 필요 없다면 openai_client 와 동일한 형태로
    통일할 것 (분기는 입력의 결정적 함수이므로 prefix 안정성 자체는 유지됨).

determinism 규칙은 openai_client 와 동일 (role → content → tool_calls →
tool_call_id 순, 원본 content 보존, tool_call arguments 는 sort_keys=True).
"""

import json
import logging
import os
import time
from typing import Generator

try:
    from openai import OpenAI, RateLimitError, InternalServerError, APIStatusError
except ImportError:
    raise ImportError("openai 패키지가 없어요. 실행: uv add openai")

logger = logging.getLogger(__name__)

_MAX_RETRIES = 4
_BASE_DELAY = 2.0  # 초 (2 → 4 → 8 → 16)

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("dotenv 패키지가 없어요. 실행: uv add python-dotenv")

from .base import BaseLLMClient, LLMConfig, LLMResponse, Message
from .rate_limiter import estimate_tokens_from_messages, get_bucket

load_dotenv()


def _to_openai_messages(messages: list[Message]) -> list[dict]:
    """
    정규화된 Message 리스트 → OpenAI Chat Completions 형식으로 변환.

    - system 메시지는 건너뜀 (chat()에서 별도로 추가)
    - assistant 메시지의 tool_use 블록 → tool_calls 필드
    - user 메시지의 tool_result 블록 → role="tool" 메시지

    Determinism (prompt caching prefix 안정화):
      - dict 키 삽입 순서 고정: role → content → tool_calls → tool_call_id
      - content 는 원본 그대로 유지 (strip() / normalize 금지)
      - tool_call arguments 는 sort_keys=True 로 canonicalize
      - tool_calls 가 있는 assistant 메시지에서 content 키는 생략(기존 동작 보존)
        — Z.ai 공식 스키마 상 필수 제약인지 확인되지 않았으므로 주석을
        사실처럼 박아두지 않는다. 분기 자체는 입력의 결정적 함수이므로
        prefix 안정성은 유지된다.
    """
    result: list[dict] = []
    for msg in messages:
        if msg.role == "system":
            continue

        if isinstance(msg.content, str):
            # key order: role, content
            result.append({"role": msg.role, "content": msg.content})
            continue

        # list content
        if msg.role == "assistant":
            text_parts = [b["text"] for b in msg.content if b.get("type") == "text"]
            tool_calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        # sort_keys=True: canonicalization (prompt caching prefix 안정화)
                        "arguments": json.dumps(b["input"], sort_keys=True),
                    },
                }
                for b in msg.content
                if b.get("type") == "tool_use"
            ]
            # 기존 동작 보존: tool_calls 가 있으면 content 키를 생략.
            # (Z.ai API 가 실제로 공존을 금지하는지는 공식 스키마로 재확인 필요 — 주석 참조)
            if tool_calls:
                # key order: role, tool_calls
                entry: dict = {"role": "assistant", "tool_calls": tool_calls}
            else:
                # key order: role, content
                entry = {"role": "assistant", "content": "\n".join(text_parts) or ""}
            result.append(entry)

        elif msg.role == "user":
            tool_results = [b for b in msg.content if b.get("type") == "tool_result"]
            if tool_results:
                for tr in tool_results:
                    # key order: role, content, tool_call_id
                    result.append({
                        "role": "tool",
                        "content": tr["content"],
                        "tool_call_id": tr["tool_use_id"],
                    })
            else:
                text = "\n".join(b.get("text", "") for b in msg.content)
                result.append({"role": "user", "content": text})

    return result


class GlmClient(BaseLLMClient):
    """
    Zai (GLM) Chat Completions API 클라이언트.

    사용 예시:
        config = LLMConfig(model="glm-4.5-air", temperature=0.0)
        client = GlmClient(config)
        response = client.chat([Message("user", "hello")])
        print(response.content)
    """

    def __init__(self, config: LLMConfig):
        super().__init__(config)

        api_key = os.getenv("ZAI_API_KEY")
        if not api_key:
            raise ValueError(
                "ZAI_API_KEY 환경변수가 설정되지 않았어요. .env 파일을 확인해주세요."
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url="https://api.z.ai/api/paas/v4/",
        )

    def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        """동기 방식 채팅 (과부하·속도제한 시 지수 백오프 재시도)"""
        create_kwargs: dict = {
            "model": self.config.model,
            "messages": [  # type: ignore[arg-type]
                {"role": "system", "content": self.config.system_prompt},
                *_to_openai_messages(messages),
            ],
            "max_completion_tokens": self.config.max_tokens,
        }
        tools = kwargs.get("tools")
        if tools:
            create_kwargs["tools"] = tools
        # GLM은 temperature=0.0 미지원 (1만 허용) — 0.0은 기본값으로 처리
        if self.config.temperature is not None and self.config.temperature > 0:
            create_kwargs["temperature"] = self.config.temperature

        bucket = get_bucket("glm", self.config.model)
        estimate = estimate_tokens_from_messages(
            create_kwargs["messages"], self.config.max_tokens
        )
        handle = bucket.reserve(estimate)
        response = None
        try:
            delay = _BASE_DELAY
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = self._client.chat.completions.create(**create_kwargs)  # type: ignore[arg-type]
                    break
                except (RateLimitError, InternalServerError) as e:
                    bucket.poison(delay)
                    if attempt == _MAX_RETRIES:
                        raise
                    logger.warning(
                        "GLM API 일시 오류 (시도 %d/%d) — %.0f초 후 재시도: %s",
                        attempt + 1, _MAX_RETRIES, delay, e,
                    )
                    time.sleep(delay)
                    delay *= 2
                except APIStatusError as e:
                    # 1213/1214: 메시지 구조 문제 — 재시도해도 동일하게 실패하므로 즉시 raise
                    error_body = getattr(e, "body", {}) or {}
                    glm_code = str((error_body.get("error") or {}).get("code", ""))
                    if glm_code in ("1213", "1214"):
                        raise
                    # 그 외 400 오류 중 일시적 서버 문제는 재시도
                    if e.status_code == 400 and attempt < _MAX_RETRIES:
                        logger.warning(
                            "GLM 400 오류 (시도 %d/%d) — %.0f초 후 재시도: %s",
                            attempt + 1, _MAX_RETRIES, delay, e,
                        )
                        time.sleep(delay)
                        delay *= 2
                    else:
                        raise
        except Exception:
            bucket.reconcile(handle, 0)
            raise

        usage_pre = getattr(response, "usage", None)
        prompt_tokens_pre = getattr(usage_pre, "prompt_tokens", 0) or 0
        completion_tokens_pre = getattr(usage_pre, "completion_tokens", 0) or 0
        actual = (prompt_tokens_pre + completion_tokens_pre) if usage_pre else estimate
        bucket.reconcile(handle, actual)

        choices = getattr(response, "choices", None) or []
        msg = getattr(choices[0], "message", None) if choices else None
        blocks: list = []
        msg_content = getattr(msg, "content", None)
        if msg_content:
            blocks.append({"type": "text", "text": msg_content})
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.id,  # type: ignore[union-attr]
                    "name": tc.function.name,  # type: ignore[union-attr]
                    "input": json.loads(tc.function.arguments),  # type: ignore[union-attr]
                }
            )

        usage = getattr(response, "usage", None)
        _cached_read = 0
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        if prompt_details:
            _cached_read = getattr(prompt_details, "cached_tokens", 0) or 0

        return LLMResponse(
            content=blocks,
            model=getattr(response, "model", self.config.model),
            stop_reason="tool_use" if tool_calls else "end_turn",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cached_read_tokens=_cached_read,
            cached_write_tokens=0,
        )

    def stream(self, messages: list[Message], **kwargs) -> Generator[str, None, None]:
        """스트리밍 방식 채팅"""
        create_kwargs: dict = {
            "model": self.config.model,
            "messages": [  # type: ignore[arg-type]
                {"role": "system", "content": self.config.system_prompt},
                *_to_openai_messages(messages),
            ],
            "max_completion_tokens": self.config.max_tokens,
            "stream": True,
        }
        tools = kwargs.get("tools")
        if tools:
            create_kwargs["tools"] = tools
        if self.config.temperature is not None and self.config.temperature > 0:
            create_kwargs["temperature"] = self.config.temperature

        stream = self._client.chat.completions.create(**create_kwargs)  # type: ignore[arg-type]
        for chunk in stream:
            delta = chunk.choices[0].delta.content  # type: ignore[union-attr]
            if delta:
                yield delta

    def is_available(self) -> bool:
        """Zai API에서 사용 가능한 모델이 있는지 확인"""
        try:
            models = self._client.models.list()
            available = [m.id for m in models.data]

            return any(
                self.config.model in m or m.startswith(self.config.model)
                for m in available
            )
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """현재 Zai API에서 이용할 수 있는 모델 목록 반환"""
        try:
            models = self._client.models.list()
            return [m.id for m in models]
        except Exception:
            return []
