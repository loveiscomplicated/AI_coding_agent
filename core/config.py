"""
core/config.py — 에이전트 설정 파일

AgentConfig dataclass:
  - provider: str  (claude | openai | ollama)
  - model: str
  - max_iterations: int
  - max_tokens: int
  - auto_approve: bool

load_config(path): TOML 파일에서 설정 로드 (없으면 기본값)
save_config(config, path): TOML 파일로 설정 저장
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_VALID_PROVIDERS = {"claude", "openai", "ollama"}
_DEFAULT_PROVIDER = "claude"
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_ITERATIONS = 15
_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_AUTO_APPROVE = False


@dataclass
class AgentConfig:
    provider: str = _DEFAULT_PROVIDER
    model: str = _DEFAULT_MODEL
    max_iterations: int = _DEFAULT_MAX_ITERATIONS
    max_tokens: int = _DEFAULT_MAX_TOKENS
    auto_approve: bool = _DEFAULT_AUTO_APPROVE

    def __post_init__(self):
        if self.provider not in _VALID_PROVIDERS:
            raise ValueError(
                f"지원하지 않는 provider: {self.provider!r}. "
                f"지원: {_VALID_PROVIDERS}"
            )
        if self.max_iterations <= 0:
            raise ValueError(f"max_iterations 는 양수여야 합니다: {self.max_iterations}")
        if self.max_tokens <= 0:
            raise ValueError(f"max_tokens 는 양수여야 합니다: {self.max_tokens}")


def load_config(path: str) -> AgentConfig:
    """TOML 설정 파일을 로드합니다. 파일이 없거나 파싱 오류 시 기본값 반환."""
    p = Path(path)
    if not p.exists():
        return AgentConfig()

    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return AgentConfig()

    # 기본값으로 시작해서 파일에서 읽은 값으로 덮어씁니다
    defaults = AgentConfig()
    kwargs: dict = {
        "provider": defaults.provider,
        "model": defaults.model,
        "max_iterations": defaults.max_iterations,
        "max_tokens": defaults.max_tokens,
        "auto_approve": defaults.auto_approve,
    }

    # 각 필드를 읽어서 검증 후 적용
    if "provider" in data:
        val = data["provider"]
        if isinstance(val, str) and val in _VALID_PROVIDERS:
            kwargs["provider"] = val
        # 유효하지 않으면 기본값 유지

    if "model" in data:
        val = data["model"]
        if isinstance(val, str):
            kwargs["model"] = val

    if "max_iterations" in data:
        val = data["max_iterations"]
        if isinstance(val, int) and val > 0:
            kwargs["max_iterations"] = val
        # 유효하지 않으면 기본값 유지

    if "max_tokens" in data:
        val = data["max_tokens"]
        if isinstance(val, int) and val > 0:
            kwargs["max_tokens"] = val

    if "auto_approve" in data:
        val = data["auto_approve"]
        if isinstance(val, bool):
            kwargs["auto_approve"] = val

    try:
        return AgentConfig(**kwargs)
    except Exception:
        return AgentConfig()


def save_config(config: AgentConfig, path: str) -> None:
    """AgentConfig 를 TOML 파일로 저장합니다. 부모 디렉토리가 없으면 생성합니다."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    auto_approve_str = "true" if config.auto_approve else "false"
    content = (
        f'provider = "{config.provider}"\n'
        f'model = "{config.model}"\n'
        f"max_iterations = {config.max_iterations}\n"
        f"max_tokens = {config.max_tokens}\n"
        f"auto_approve = {auto_approve_str}\n"
    )
    p.write_text(content, encoding="utf-8")
