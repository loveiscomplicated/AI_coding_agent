"""
cli/config.py — CLI TDD 전용 설정 (agent.toml 로더/세이버)

우선순위 (높음 → 낮음):
  1. 환경변수
  2. {repo_path}/agent.toml
  3. ~/.config/agent/config.toml
  4. 하드코딩 기본값

기본 저장 포맷:
  [llm.default_role_models.<role>]
  [llm.complexity_role_models.<tier>.<role>]

레거시 호환:
  model_fast / model_capable / provider_fast / provider_capable 도 읽는다.
  다만 저장은 새 포맷으로만 수행한다.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from agents.roles import (
    ROLE_IMPLEMENTER,
    ROLE_INTERVENTION,
    ROLE_MERGE_AGENT,
    ROLE_ORCHESTRATOR,
    ROLE_REVIEWER,
    ROLE_TEST_WRITER,
)
from orchestrator.model_defaults import (
    COMPLEXITY_ROLE_MODEL_MAP,
    DEFAULT_ROLE_MODEL_MAP,
    clone_complexity_role_model_map,
    clone_role_model_map,
)

ROLE_MINI_MEETING = "mini_meeting"

WORKER_ROLE_KEYS: tuple[str, ...] = (
    ROLE_TEST_WRITER,
    ROLE_IMPLEMENTER,
    ROLE_REVIEWER,
    ROLE_MERGE_AGENT,
)
CAPABLE_ROLE_KEYS: tuple[str, ...] = (
    ROLE_ORCHESTRATOR,
    ROLE_INTERVENTION,
    ROLE_MINI_MEETING,
)
CLI_ROLE_KEYS: tuple[str, ...] = (*WORKER_ROLE_KEYS, ROLE_ORCHESTRATOR, ROLE_INTERVENTION, ROLE_MINI_MEETING)
COMPLEXITY_TIERS: tuple[str, ...] = ("simple", "standard", "complex")


def _mini_meeting_default() -> dict[str, str]:
    base = DEFAULT_ROLE_MODEL_MAP[ROLE_ORCHESTRATOR]
    return {"provider": base["provider"], "model": base["model"]}


def default_role_models() -> dict[str, dict[str, str]]:
    models = clone_role_model_map(DEFAULT_ROLE_MODEL_MAP)
    models[ROLE_MINI_MEETING] = _mini_meeting_default()
    return models


def default_complexity_role_models() -> dict[str, dict[str, dict[str, str]]]:
    return clone_complexity_role_model_map(COMPLEXITY_ROLE_MODEL_MAP)


def _clone_role_models(
    models: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, dict[str, str]]:
    cloned: dict[str, dict[str, str]] = {}
    for role, cfg in (models or {}).items():
        cloned[role] = {
            "provider": str(cfg.get("provider") or ""),
            "model": str(cfg.get("model") or ""),
        }
    return cloned


def _clone_complexity_models(
    models: dict[str, dict[str, dict[str, str | None]]] | None = None,
) -> dict[str, dict[str, dict[str, str]]]:
    cloned: dict[str, dict[str, dict[str, str]]] = {}
    for tier, roles in (models or {}).items():
        cloned[tier] = _clone_role_models(roles)
    return cloned


@dataclass
class AgentConfig:
    # legacy/general
    provider: str = "claude"

    # 역할별 모델
    default_role_models: dict[str, dict[str, str]] = field(default_factory=default_role_models)
    complexity_role_models: dict[str, dict[str, dict[str, str]]] = field(
        default_factory=default_complexity_role_models
    )

    # 프로젝트 기본값
    language: str = "python"
    test_framework: str = "pytest"
    base_branch: str = "main"

    # 동작
    default_mode: str = "normal"   # "normal" | "tdd"
    auto_push: bool = False
    auto_select_by_complexity: bool = True


def find_repo_root(start: str | None = None) -> str | None:
    """`.git` 디렉토리 기준 상위 탐색. 찾지 못하면 None."""
    cur = Path(start or os.getcwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return str(p)
    return None


def default_config_path(repo_path: str | None = None) -> Path:
    if repo_path:
        return Path(repo_path) / "agent.toml"
    return Path.home() / ".config" / "agent" / "config.toml"


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        print(
            f"경고: 설정 파일 파싱 실패 ({path}): {exc}. 기본값으로 계속합니다.",
            file=sys.stderr,
        )
        return {}
    except OSError as exc:
        print(
            f"경고: 설정 파일을 읽지 못했습니다 ({path}): {exc}. 기본값으로 계속합니다.",
            file=sys.stderr,
        )
        return {}
    except Exception as exc:
        print(
            f"경고: 설정 파일 로드 실패 ({path}): {exc}. 기본값으로 계속합니다.",
            file=sys.stderr,
        )
        return {}


def _parse_model_ref_env(env_name: str) -> tuple[str, str] | None:
    raw = os.getenv(env_name)
    if not raw or ":" not in raw:
        return None
    provider, model = raw.split(":", 1)
    provider = provider.strip()
    model = model.strip()
    if not provider or not model:
        return None
    return provider, model


def _merge_role_models(
    base: dict[str, dict[str, str]],
    incoming: dict | None,
) -> None:
    for role, cfg in (incoming or {}).items():
        if not isinstance(cfg, dict):
            continue
        provider = cfg.get("provider")
        model = cfg.get("model")
        if provider is None and model is None:
            continue
        current = base.setdefault(role, {"provider": "", "model": ""})
        if provider is not None:
            current["provider"] = str(provider)
        if model is not None:
            current["model"] = str(model)


def _merge_complexity_role_models(
    base: dict[str, dict[str, dict[str, str]]],
    incoming: dict | None,
) -> None:
    for tier, roles in (incoming or {}).items():
        if not isinstance(roles, dict):
            continue
        target_roles = base.setdefault(tier, {})
        _merge_role_models(target_roles, roles)


def _apply_legacy_llm(cfg: AgentConfig, llm_data: dict) -> None:
    capable_model = llm_data.get("model_capable")
    fast_model = llm_data.get("model_fast")
    if capable_model is None and fast_model is None:
        return

    provider = str(llm_data.get("provider") or cfg.provider)
    capable_provider = str(llm_data.get("provider_capable") or provider)
    fast_provider = str(llm_data.get("provider_fast") or provider)
    capable_model = str(capable_model or cfg.default_role_models[ROLE_INTERVENTION]["model"])
    fast_model = str(fast_model or cfg.default_role_models[ROLE_TEST_WRITER]["model"])

    for role in CAPABLE_ROLE_KEYS:
        cfg.default_role_models[role] = {
            "provider": capable_provider,
            "model": capable_model,
        }

    for role in WORKER_ROLE_KEYS:
        cfg.default_role_models[role] = {
            "provider": fast_provider,
            "model": fast_model,
        }
        for tier in COMPLEXITY_TIERS:
            cfg.complexity_role_models.setdefault(tier, {})
            cfg.complexity_role_models[tier][role] = {
                "provider": fast_provider,
                "model": fast_model,
            }


def _apply_toml(cfg: AgentConfig, data: dict) -> None:
    llm_data = data.get("llm", {})
    project_data = data.get("project", {})
    behavior_data = data.get("behavior", {})

    if (provider := llm_data.get("provider")) is not None:
        cfg.provider = str(provider)

    _merge_role_models(cfg.default_role_models, llm_data.get("default_role_models"))
    _merge_complexity_role_models(cfg.complexity_role_models, llm_data.get("complexity_role_models"))
    _apply_legacy_llm(cfg, llm_data)

    for key in ("language", "test_framework", "base_branch"):
        value = project_data.get(key)
        if value is not None:
            setattr(cfg, key, value)

    for key in ("default_mode", "auto_push", "auto_select_by_complexity"):
        value = behavior_data.get(key)
        if value is not None:
            setattr(cfg, key, value)


def _apply_env(cfg: AgentConfig) -> None:
    if v := os.getenv("LLM_PROVIDER"):
        cfg.provider = v

    for role in CLI_ROLE_KEYS:
        override = _parse_model_ref_env(f"LLM_ROLE_{role.upper()}")
        if override:
            provider, model = override
            cfg.default_role_models[role] = {"provider": provider, "model": model}

    for tier in COMPLEXITY_TIERS:
        for role in WORKER_ROLE_KEYS:
            override = _parse_model_ref_env(f"COMPLEXITY_{tier.upper()}_ROLE_{role.upper()}")
            if override:
                provider, model = override
                cfg.complexity_role_models.setdefault(tier, {})
                cfg.complexity_role_models[tier][role] = {
                    "provider": provider,
                    "model": model,
                }

    # legacy env fallback
    legacy_provider = os.getenv("LLM_PROVIDER")
    legacy_fast_model = os.getenv("LLM_MODEL_FAST") or os.getenv("LLM_TITLE_MODEL")
    legacy_capable_model = os.getenv("LLM_MODEL_CAPABLE") or os.getenv("LLM_DEFAULT_MODEL")
    if legacy_fast_model or legacy_capable_model:
        _apply_legacy_llm(
            cfg,
            {
                "provider": legacy_provider or cfg.provider,
                "provider_fast": os.getenv("LLM_PROVIDER_FAST"),
                "provider_capable": os.getenv("LLM_PROVIDER_CAPABLE"),
                "model_fast": legacy_fast_model,
                "model_capable": legacy_capable_model,
            },
        )


def load_config(repo_path: str | None = None) -> AgentConfig:
    """AgentConfig를 환경변수 > repo agent.toml > 글로벌 > 기본값 순으로 구성."""
    cfg = AgentConfig()

    _apply_toml(cfg, _load_toml(default_config_path(None)))
    if repo_path:
        _apply_toml(cfg, _load_toml(default_config_path(repo_path)))
    _apply_env(cfg)

    return cfg


def save_config(config: AgentConfig, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "[llm]",
        f'provider = "{config.provider}"',
        "",
    ]

    for role in CLI_ROLE_KEYS:
        cfg = config.default_role_models.get(role)
        if not cfg:
            continue
        lines.extend([
            f"[llm.default_role_models.{role}]",
            f'provider = "{cfg.get("provider", "")}"',
            f'model = "{cfg.get("model", "")}"',
            "",
        ])

    for tier in COMPLEXITY_TIERS:
        for role in WORKER_ROLE_KEYS:
            cfg = config.complexity_role_models.get(tier, {}).get(role)
            if not cfg:
                continue
            lines.extend([
                f"[llm.complexity_role_models.{tier}.{role}]",
                f'provider = "{cfg.get("provider", "")}"',
                f'model = "{cfg.get("model", "")}"',
                "",
            ])

    lines.extend([
        "[project]",
        f'language = "{config.language}"',
        f'test_framework = "{config.test_framework}"',
        f'base_branch = "{config.base_branch}"',
        "",
        "[behavior]",
        f'default_mode = "{config.default_mode}"',
        f"auto_push = {'true' if config.auto_push else 'false'}",
        f"auto_select_by_complexity = {'true' if config.auto_select_by_complexity else 'false'}",
        "",
    ])

    p.write_text("\n".join(lines), encoding="utf-8")
