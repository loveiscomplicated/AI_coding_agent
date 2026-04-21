"""
backend/config.py — 환경 변수 설정

.env 파일 또는 환경 변수에서 API 키/토큰을 읽는다.
프론트엔드에 시크릿을 노출하지 않기 위해 백엔드에서만 관리한다.
"""

import os
from dotenv import load_dotenv
from agents.roles import (
    MODEL_ROLE_KEYS,
    ROLE_IMPLEMENTER,
    ROLE_INTERVENTION,
    ROLE_MERGE_AGENT,
    ROLE_ORCHESTRATOR,
    ROLE_REVIEWER,
    ROLE_TEST_WRITER,
)

load_dotenv()

# LLM 프로바이더/일반 채팅 기본 모델
LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "claude")
LLM_DEFAULT_MODEL: str = os.environ.get("LLM_DEFAULT_MODEL", "claude-opus-4-6")
LLM_TITLE_MODEL: str = os.environ.get("LLM_TITLE_MODEL", "claude-haiku-4-5-20251001")

# API 키 (provider에 따라 필요 여부 다름)
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
ZAI_API_KEY: str = os.environ.get("ZAI_API_KEY", "")
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")

# provider별 API 키 필수 확인
if LLM_PROVIDER == "claude" and not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.\n"
        "프로젝트 루트의 .env 파일에 ANTHROPIC_API_KEY=sk-ant-... 를 추가하세요.\n"
        "다른 프로바이더를 사용하려면 LLM_PROVIDER 환경 변수를 설정하세요."
    )
if LLM_PROVIDER == "openai" and not OPENAI_API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.\n"
        "프로젝트 루트의 .env 파일에 OPENAI_API_KEY=sk-... 를 추가하세요."
    )
if LLM_PROVIDER == "glm" and not ZAI_API_KEY:
    raise RuntimeError(
        "ZAI_API_KEY 환경 변수가 설정되지 않았습니다.\n"
        "프로젝트 루트의 .env 파일에 ZAI_API_KEY=... 를 추가하세요."
    )
if LLM_PROVIDER == "gemini" and not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.\n"
        "프로젝트 루트의 .env 파일에 GEMINI_API_KEY=... 를 추가하세요."
    )

# Discord 핫라인 (Step 5). 미설정 시 Discord 기능 비활성화.
DISCORD_BOT_TOKEN: str = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID: int | None = (
    int(os.environ["DISCORD_GUILD_ID"])
    if os.environ.get("DISCORD_GUILD_ID")
    else None
)


# ── 역할별 기본 모델 설정 ────────────────────────────────────────────────────
# 모든 에이전트 역할은 공통된 per-role 맵을 기준으로 해석한다.
# 환경 변수 override 형식: LLM_ROLE_TEST_WRITER=openai:gpt-5-mini


def _parse_model_ref_env(env_name: str) -> tuple[str, str] | None:
    raw = os.environ.get(env_name)
    if not raw or ":" not in raw:
        return None
    provider, model = raw.split(":", 1)
    provider = provider.strip()
    model = model.strip()
    if not provider or not model:
        return None
    return provider, model


def _role_model(provider: str, model: str) -> dict[str, str]:
    return {"provider": provider, "model": model}


DEFAULT_ROLE_MODEL_MAP: dict[str, dict[str, str]] = {
    ROLE_TEST_WRITER: _role_model("claude", "claude-haiku-4-5-20251001"),
    ROLE_IMPLEMENTER: _role_model("claude", "claude-haiku-4-5-20251001"),
    ROLE_REVIEWER: _role_model("claude", "claude-haiku-4-5-20251001"),
    ROLE_MERGE_AGENT: _role_model("claude", "claude-haiku-4-5-20251001"),
    ROLE_ORCHESTRATOR: _role_model("claude", "claude-opus-4-6"),
    ROLE_INTERVENTION: _role_model("claude", "claude-opus-4-6"),
}

for _role in MODEL_ROLE_KEYS:
    _override = _parse_model_ref_env(f"LLM_ROLE_{_role.upper()}")
    if _override:
        _p, _m = _override
        DEFAULT_ROLE_MODEL_MAP[_role] = _role_model(_p, _m)


# ── 복잡도 기반 역할별 모델 매핑 ────────────────────────────────────────────
# 태스크의 `complexity` 라벨(simple/standard/complex)에 따라 역할별 모델을
# 자동으로 선택한다. 환경 변수 override 형식:
# COMPLEXITY_SIMPLE_ROLE_TEST_WRITER=openai:gpt-4.1-mini

COMPLEXITY_ROLE_MODEL_MAP: dict[str, dict[str, dict[str, str]]] = {
    "simple": {
        ROLE_TEST_WRITER: _role_model("openai", "gpt-4.1-mini"),
        ROLE_IMPLEMENTER: _role_model("openai", "gpt-4.1-mini"),
        ROLE_REVIEWER: _role_model("openai", "gpt-4.1-mini"),
        ROLE_MERGE_AGENT: _role_model("openai", "gpt-4.1-mini"),
        ROLE_ORCHESTRATOR: _role_model("gemini", "gemini-2.5-flash-lite"),
        ROLE_INTERVENTION: _role_model("gemini", "gemini-2.5-flash-lite"),
    },
    "standard": {
        ROLE_TEST_WRITER: _role_model("openai", "gpt-5-mini"),
        ROLE_IMPLEMENTER: _role_model("openai", "gpt-5-mini"),
        ROLE_REVIEWER: _role_model("openai", "gpt-5-mini"),
        ROLE_MERGE_AGENT: _role_model("openai", "gpt-5-mini"),
        ROLE_ORCHESTRATOR: _role_model("gemini", "gemini-2.5-flash"),
        ROLE_INTERVENTION: _role_model("gemini", "gemini-2.5-flash"),
    },
    "complex": {
        ROLE_TEST_WRITER: _role_model("openai", "gpt-5"),
        ROLE_IMPLEMENTER: _role_model("openai", "gpt-5"),
        ROLE_REVIEWER: _role_model("openai", "gpt-5"),
        ROLE_MERGE_AGENT: _role_model("openai", "gpt-5"),
        ROLE_ORCHESTRATOR: _role_model("gemini", "gemini-3-pro-preview"),
        ROLE_INTERVENTION: _role_model("gemini", "gemini-3-pro-preview"),
    },
}

for _tier in ("simple", "standard", "complex"):
    for _role in MODEL_ROLE_KEYS:
        _override = _parse_model_ref_env(f"COMPLEXITY_{_tier.upper()}_ROLE_{_role.upper()}")
        if _override:
            _p, _m = _override
            COMPLEXITY_ROLE_MODEL_MAP[_tier][_role] = _role_model(_p, _m)
