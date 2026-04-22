"""
cli/settings_wizard.py — `agent set` 인터랙티브 설정 UI
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cli import interface as ui
from cli.config import (
    AgentConfig,
    CAPABLE_ROLE_KEYS,
    COMPLEXITY_TIERS,
    ROLE_MINI_MEETING,
    WORKER_ROLE_KEYS,
    default_config_path,
    load_config,
    save_config,
)
from cli.selector import SelectOption, inline_select
from llm import LLMConfig, create_client

_PROVIDERS: tuple[str, ...] = ("ollama", "openai", "claude", "gemini", "glm")
_DEFAULT_MODELS: dict[str, str] = {
    "ollama": "devstral:24b",
    "openai": "gpt-4o",
    "claude": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-pro-preview-06-05",
    "glm": "glm-5.1",
}

_ROLE_LABELS: dict[str, str] = {
    "mini_meeting": "Mini Meeting",
    "orchestrator": "Orchestrator",
    "intervention": "Intervention",
    "test_writer": "Test Writer",
    "implementer": "Implementer",
    "reviewer": "Reviewer",
    "merge_agent": "Merge Agent",
}
_TIER_LABELS: dict[str, str] = {
    "simple": "Simple",
    "standard": "Standard",
    "complex": "Complex",
}


@dataclass(frozen=True)
class SettingTarget:
    kind: str
    role: str
    tier: str | None = None

    @property
    def key(self) -> str:
        if self.tier:
            return f"{self.tier}:{self.role}"
        return self.role

    @property
    def title(self) -> str:
        role_label = _ROLE_LABELS.get(self.role, self.role)
        if self.tier:
            return f"{_TIER_LABELS.get(self.tier, self.tier)} / {role_label}"
        return role_label


def _all_targets() -> list[SettingTarget]:
    targets = [SettingTarget(kind="capable", role=role) for role in CAPABLE_ROLE_KEYS]
    for tier in COMPLEXITY_TIERS:
        for role in WORKER_ROLE_KEYS:
            targets.append(SettingTarget(kind="worker", role=role, tier=tier))
    return targets


def _get_target_value(config: AgentConfig, target: SettingTarget) -> tuple[str, str]:
    if target.kind == "capable":
        ref = config.default_role_models[target.role]
    else:
        ref = config.complexity_role_models[target.tier or "standard"][target.role]
    return ref["provider"], ref["model"]


def _set_target_value(config: AgentConfig, target: SettingTarget, provider: str, model: str) -> None:
    if target.kind == "capable":
        config.default_role_models[target.role] = {"provider": provider, "model": model}
        return
    config.complexity_role_models.setdefault(target.tier or "standard", {})
    config.complexity_role_models[target.tier or "standard"][target.role] = {
        "provider": provider,
        "model": model,
    }


def _reset_target_value(config: AgentConfig, target: SettingTarget, base: AgentConfig) -> None:
    provider, model = _get_target_value(base, target)
    _set_target_value(config, target, provider, model)


def _target_options(config: AgentConfig) -> list[SelectOption]:
    options: list[SelectOption] = []
    for target in _all_targets():
        provider, model = _get_target_value(config, target)
        description = (
            "capable 고정 역할"
            if target.kind == "capable"
            else f"복잡도 {_TIER_LABELS.get(target.tier or '', target.tier or '')} 작업 역할"
        )
        options.append(
            SelectOption(
                label=f"{target.title:<28} {provider}/{model}",
                value=target.key,
                description=description,
            )
        )
    options.append(
        SelectOption(
            label="저장하고 종료",
            value="__save__",
            description="현재 선택값을 agent.toml에 저장",
        )
    )
    return options


def _resolve_target(raw: str) -> SettingTarget:
    if ":" not in raw:
        return SettingTarget(kind="capable", role=raw)
    tier, role = raw.split(":", 1)
    return SettingTarget(kind="worker", role=role, tier=tier)


def _provider_options() -> list[SelectOption]:
    options = [
        SelectOption(
            label="기본값으로 복원",
            value="__reset__",
            description="이 항목만 기본 조합으로 되돌림",
        )
    ]
    for provider in _PROVIDERS:
        options.append(
            SelectOption(
                label=provider,
                value=provider,
                description=f"기본 모델: {_DEFAULT_MODELS[provider]}",
            )
        )
    return options


def _load_models_for_provider(
    provider: str,
    cache: dict[str, list[str]],
) -> list[str]:
    if provider in cache:
        return cache[provider]

    fallback = [_DEFAULT_MODELS[provider]]
    try:
        client = create_client(provider=provider, config=LLMConfig(model=_DEFAULT_MODELS[provider]))
        models = sorted(client.list_models())
        cache[provider] = models or fallback
    except Exception as exc:
        ui.print_info(f"{provider} 모델 목록 조회 실패: {exc} — 기본 모델 목록으로 진행")
        cache[provider] = fallback
    return cache[provider]


def run_settings_wizard(repo_path: str | None = None) -> Path | None:
    target_path = default_config_path(repo_path)
    config = load_config(repo_path)
    base = AgentConfig()
    model_cache: dict[str, list[str]] = {}
    target_index = 0
    provider_index = 0

    ui.console.print()
    ui.console.print("[bold cyan]agent set[/bold cyan]")
    ui.console.print(f"[dim]설정 파일: {target_path}[/dim]")

    while True:
        target_options = _target_options(config)
        choice = inline_select(
            target_options,
            message="[bold]설정할 역할을 선택하세요[/bold]",
            detail="[dim]↑↓ 이동 · Enter/→ 선택 · Esc 취소[/dim]",
            default_index=target_index,
        )
        if choice is None:
            return None
        if choice == "__save__":
            save_config(config, target_path)
            ui.print_info(f"설정 저장 완료: {target_path}")
            return target_path

        target = _resolve_target(choice)
        target_index = next(
            (idx for idx, option in enumerate(target_options) if option.value == choice),
            target_index,
        )
        current_provider, current_model = _get_target_value(config, target)

        provider_options = _provider_options()
        provider_index = next(
            (
                idx for idx, option in enumerate(provider_options)
                if option.value == current_provider
            ),
            1,
        )
        provider_choice = inline_select(
            provider_options,
            message=f"[bold]{target.title}[/bold] provider 선택",
            detail=(
                f"[dim]현재 값: {current_provider}/{current_model}\n"
                "↑↓ 이동 · Enter/→ 선택 · ← 이전 · Esc 취소[/dim]"
            ),
            default_index=provider_index,
            allow_back=True,
        )
        if provider_choice is None:
            return None
        if provider_choice == "__back__":
            continue
        if provider_choice == "__reset__":
            _reset_target_value(config, target, base)
            continue

        provider_index = next(
            (
                idx for idx, option in enumerate(provider_options)
                if option.value == provider_choice
            ),
            provider_index,
        )
        models = _load_models_for_provider(provider_choice, model_cache)
        model_options = [
            SelectOption(label=model, value=model) for model in models
        ]
        model_index = next(
            (
                idx for idx, option in enumerate(model_options)
                if option.value == current_model
            ),
            0,
        )
        model_choice = inline_select(
            model_options,
            message=f"[bold]{target.title}[/bold] model 선택",
            detail=(
                f"[dim]provider: {provider_choice}\n"
                "↑↓ 이동 · Enter/→ 선택 · ← 이전 · Esc 취소[/dim]"
            ),
            default_index=model_index,
            allow_back=True,
        )
        if model_choice is None:
            return None
        if model_choice == "__back__":
            continue

        _set_target_value(config, target, provider_choice, model_choice)
