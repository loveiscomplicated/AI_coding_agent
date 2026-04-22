"""
orchestrator/model_defaults.py — 역할별 기본 모델/복잡도 매핑

backend.config 와 agent CLI TDD가 동일한 기본 라우팅을 공유하도록 분리한 모듈.
환경 변수나 사용자 설정 파일 override 는 각 호출자가 별도로 적용한다.
"""

from __future__ import annotations

from agents.roles import (
    ROLE_IMPLEMENTER,
    ROLE_INTERVENTION,
    ROLE_MERGE_AGENT,
    ROLE_ORCHESTRATOR,
    ROLE_REVIEWER,
    ROLE_TEST_WRITER,
)


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


def clone_role_model_map(
    role_models: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, dict[str, str]]:
    return {
        role: {
            "provider": str(cfg.get("provider") or ""),
            "model": str(cfg.get("model") or ""),
        }
        for role, cfg in (role_models or {}).items()
    }


def clone_complexity_role_model_map(
    complexity_map: dict[str, dict[str, dict[str, str | None]]] | None = None,
) -> dict[str, dict[str, dict[str, str]]]:
    cloned: dict[str, dict[str, dict[str, str]]] = {}
    for tier, roles in (complexity_map or {}).items():
        cloned[tier] = clone_role_model_map(roles)
    return cloned
