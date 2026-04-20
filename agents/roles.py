"""
agents/roles.py — 에이전트 역할 정의

각 역할(RoleConfig)은 시스템 프롬프트와 허용 도구 목록을 갖는다.
ScopedReactLoop 생성 시 role 을 전달하면 해당 제약이 자동 적용된다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# 컴파일 언어별 빌드 지시 블록 (인터프리터 언어는 빈 문자열)
_BUILD_INSTRUCTIONS: dict[str, str] = {
    "c": (
        "## 빌드 지시\n\n"
        "테스트 실행 가능한 `Makefile`을 workspace 루트에 반드시 함께 생성하라.\n"
        "`make test` 명령 하나로 컴파일과 테스트가 모두 실행되어야 한다."
    ),
    "cpp": (
        "## 빌드 지시\n\n"
        "테스트 실행 가능한 `CMakeLists.txt`를 workspace 루트에 반드시 함께 생성하라.\n"
        "`cmake --build . && ctest` 명령으로 빌드와 테스트가 실행되어야 한다."
    ),
}


def _get_build_instructions(language: str) -> str:
    """언어에 따른 빌드 지시 블록을 반환한다. 인터프리터 언어는 빈 문자열."""
    return _BUILD_INSTRUCTIONS.get(language, "")

# ── 도구 그룹 정의 ────────────────────────────────────────────────────────────

# 읽기 전용 도구 (모든 역할에서 사용 가능)
READ_TOOLS: list[str] = [
    "read_file",
    "read_file_lines",
    "list_directory",
    "search_files",
    "search_in_file",
    "get_outline",
    "get_function_src",
    "get_imports",
]

# 파일 쓰기 도구 (쓰기 권한이 있는 역할만)
WRITE_TOOLS: list[str] = [
    "write_file",
    "edit_file",
    "append_to_file",
    "delete_file",
    "delete_directory",
]


# ── 역할 키 상수 ─────────────────────────────────────────────────────────────

ROLE_TEST_WRITER = "test_writer"
ROLE_IMPLEMENTER = "implementer"
ROLE_REVIEWER = "reviewer"
ROLE_ORCHESTRATOR = "orchestrator"
ROLE_MERGE_AGENT = "merge_agent"
ROLE_INTERVENTION = "intervention"
ROLE_COMPACTION_BUILTINS = frozenset({
    ROLE_TEST_WRITER,
    ROLE_IMPLEMENTER,
    ROLE_REVIEWER,
})

_ENABLE_ROLE_COMPACTION_TUNING_ENV = "ENABLE_ROLE_COMPACTION_TUNING"
_ROLE_COMPACTION_ENV_BY_ROLE: dict[str, str] = {
    ROLE_TEST_WRITER: "TEST_WRITER_COMPACTION_THRESHOLD",
    ROLE_IMPLEMENTER: "IMPLEMENTER_COMPACTION_THRESHOLD",
    ROLE_REVIEWER: "REVIEWER_COMPACTION_THRESHOLD",
}
ROLE_COMPACTION_SELECTION_DEFAULT = "default"
ROLE_COMPACTION_SELECTION_INHERIT = "inherit"
ROLE_COMPACTION_PRESET_CONSERVATIVE = "conservative"
ROLE_COMPACTION_PRESET_BALANCED = "balanced"
ROLE_COMPACTION_PRESET_AGGRESSIVE = "aggressive"
ROLE_COMPACTION_PRESET_IDS = frozenset({
    ROLE_COMPACTION_PRESET_CONSERVATIVE,
    ROLE_COMPACTION_PRESET_BALANCED,
    ROLE_COMPACTION_PRESET_AGGRESSIVE,
})
ROLE_COMPACTION_SELECTION_IDS = frozenset({
    ROLE_COMPACTION_SELECTION_DEFAULT,
    ROLE_COMPACTION_SELECTION_INHERIT,
    *ROLE_COMPACTION_PRESET_IDS,
})
ROLE_COMPACTION_PRESETS: dict[str, dict[str, int]] = {
    ROLE_COMPACTION_PRESET_CONSERVATIVE: {
        ROLE_TEST_WRITER: 26_000,
        ROLE_IMPLEMENTER: 24_000,
        ROLE_REVIEWER: 28_000,
    },
    ROLE_COMPACTION_PRESET_BALANCED: {
        ROLE_TEST_WRITER: 22_000,
        ROLE_IMPLEMENTER: 20_000,
        ROLE_REVIEWER: 26_000,
    },
    ROLE_COMPACTION_PRESET_AGGRESSIVE: {
        ROLE_TEST_WRITER: 20_000,
        ROLE_IMPLEMENTER: 18_000,
        ROLE_REVIEWER: 25_000,
    },
}


# ── 역할별 모델 오버라이드 ────────────────────────────────────────────────────


@dataclass
class RoleModelConfig:
    """역할별 모델 오버라이드 설정. None인 필드는 기본값(model_fast/model_capable) 사용."""

    provider: str | None = None
    model: str | None = None


def resolve_model_for_role(
    role: str,
    role_models: dict[str, RoleModelConfig] | None,
    provider: str,
    model_fast: str,
    model_capable: str,
    provider_fast: str | None = None,
    provider_capable: str | None = None,
) -> tuple[str, str]:
    """역할에 맞는 (provider, model) 튜플을 반환한다.

    우선순위: role_models[role] → fast/capable 기본값 → 공통 provider

    Args:
        role: 역할 키 (ROLE_TEST_WRITER 등)
        role_models: 역할별 모델 오버라이드 딕셔너리. None이면 기본값만 사용.
        provider: 공통 기본 프로바이더
        model_fast: 코딩 에이전트 기본 모델
        model_capable: 오케스트레이터 기본 모델
        provider_fast: 코딩 에이전트 프로바이더 (None이면 provider 사용)
        provider_capable: 오케스트레이터 프로바이더 (None이면 provider 사용)

    Returns:
        (provider, model) 튜플
    """
    role_cfg = (role_models or {}).get(role)

    if role in (ROLE_ORCHESTRATOR, ROLE_INTERVENTION):
        # 오케스트레이터/개입 분석은 capable 모델이 기본
        resolved_provider = (role_cfg and role_cfg.provider) or provider_capable or provider
        resolved_model = (role_cfg and role_cfg.model) or model_capable
    else:
        # 코딩 에이전트(TestWriter, Implementer, Reviewer, MergeAgent)는 fast 모델이 기본
        resolved_provider = (role_cfg and role_cfg.provider) or provider_fast or provider
        resolved_model = (role_cfg and role_cfg.model) or model_fast

    return resolved_provider, resolved_model


def resolve_complexity_model(
    role: str,
    complexity: str | None,
    complexity_map: dict[str, dict[str, str]],
) -> tuple[str, str]:
    """태스크 복잡도에 기반한 (provider, model) 반환.

    - `complexity`가 "simple"/"standard"/"complex" 중 하나면 해당 tier 사용
    - 그 외 값(None 포함)은 "standard"로 fallback
    - orchestrator/intervention 역할은 capable, 나머지 코딩 역할은 fast 사용
    """
    tier = complexity if complexity in complexity_map else "standard"
    bucket = complexity_map[tier]
    if role in (ROLE_ORCHESTRATOR, ROLE_INTERVENTION):
        return bucket["provider_capable"], bucket["model_capable"]
    return bucket["provider_fast"], bucket["model_fast"]


def compose_role_override(
    role_cfg: "RoleModelConfig | None",
    base_provider: str,
    base_model: str,
) -> tuple[str, str]:
    """부분 오버라이드 합성: role_cfg에 지정된 필드만 base를 덮어쓴다.

    role_cfg가 ``{provider: 'anthropic'}`` 처럼 일부만 지정되어 있으면 provider는
    override 값을, model은 base(복잡도 매핑 또는 기본 fast/capable) 값을 사용한다.
    파이프라인 전반(코딩 역할, intervention, merge_agent)에서 동일한 합성 규칙을
    사용하여 계약 일관성을 보장한다.
    """
    if role_cfg is None:
        return base_provider, base_model
    return (
        role_cfg.provider or base_provider,
        role_cfg.model or base_model,
    )


# ── 역할 데이터 클래스 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoleConfig:
    name: str
    system_prompt: str
    allowed_tools: tuple[str, ...]
    blocked_write_dirs: tuple[str, ...] = ()  # workspace 내에서도 쓰기 금지 디렉토리 (상대 경로)
    # 내장 역할(TestWriter/Implementer/Reviewer)의 경우 이 값은 "튜닝 후보값"이다.
    # 기본 동작에서는 검증된 전역 기본값(30k)을 사용하고, 아래 env flag 를 켠
    # 경우에만 역할별 값이 활성화된다. 커스텀 RoleConfig 는 이 필드가 곧 실제값이다.
    compaction_threshold: int | None = None

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools

    def can_write(self) -> bool:
        return any(t in self.allowed_tools for t in WRITE_TOOLS)

    def render(self, language: str, test_framework: str) -> "RoleConfig":
        """
        시스템 프롬프트 내의 플레이스홀더를 태스크의 실제 값으로 치환한 새 RoleConfig를 반환한다.

        치환 변수:
          {language}            — 태스크 언어 (예: "kotlin", "python")
          {test_framework}      — 테스트 프레임워크 (예: "gradle", "pytest")
          {build_instructions}  — 컴파일 언어(C/C++)용 빌드 지시 블록, 나머지는 빈 문자열

        str.replace 방식을 사용하므로 프롬프트 내 다른 중괄호({올바른 경로} 등)에
        영향을 주지 않는다.
        """
        rendered = (
            self.system_prompt
            .replace("{language}", language)
            .replace("{test_framework}", test_framework)
            .replace("{build_instructions}", _get_build_instructions(language))
        )
        return replace(self, system_prompt=rendered)


def _load_prompt(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"프롬프트 파일 없음: {path}")
    return path.read_text(encoding="utf-8")


def _parse_threshold_env(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got: {value}")
    return value


def _threshold_for_preset(role_name: str, preset: str) -> int:
    thresholds = ROLE_COMPACTION_PRESETS.get(preset)
    if thresholds is None:
        raise ValueError(f"Unknown role compaction preset: {preset!r}")
    return thresholds[role_name]


def _validate_role_compaction_selection(selection: str, *, allow_inherit: bool) -> None:
    allowed = ROLE_COMPACTION_SELECTION_IDS if allow_inherit else frozenset({
        ROLE_COMPACTION_SELECTION_DEFAULT,
        *ROLE_COMPACTION_PRESET_IDS,
    })
    if selection not in allowed:
        raise ValueError(
            f"Unknown role compaction selection: {selection!r}. "
            f"Allowed: {sorted(allowed)!r}"
        )


def resolve_role_compaction_threshold(
    role: RoleConfig,
    *,
    tuning_enabled: bool = False,
    tuning_preset: str = ROLE_COMPACTION_PRESET_BALANCED,
    role_tuning_overrides: dict[str, str] | None = None,
) -> int | None:
    """역할별 compaction threshold 의 실제 적용값을 반환한다.

    우선순위:
      1. 역할별 env override (예: IMPLEMENTER_COMPACTION_THRESHOLD=18000)
      2. per-run 역할 override (default / conservative / balanced / aggressive)
      3. 내장 역할 + per-run tuning enabled → preset threshold
      4. 내장 역할 + ENABLE_ROLE_COMPACTION_TUNING=1 → legacy role.compaction_threshold
      5. 커스텀 역할 → role.compaction_threshold
      6. 내장 역할 기본값 off → None (전역 30k fallback)
    """
    env_name = _ROLE_COMPACTION_ENV_BY_ROLE.get(role.name)
    if env_name is not None:
        override = _parse_threshold_env(env_name)
        if override is not None:
            return override
        selection = (role_tuning_overrides or {}).get(role.name)
        if selection is not None:
            _validate_role_compaction_selection(selection, allow_inherit=True)
            if selection == ROLE_COMPACTION_SELECTION_DEFAULT:
                return None
            if selection != ROLE_COMPACTION_SELECTION_INHERIT:
                return _threshold_for_preset(role.name, selection)
        _validate_role_compaction_selection(tuning_preset, allow_inherit=False)
        if tuning_enabled:
            return _threshold_for_preset(role.name, tuning_preset)
        if os.environ.get(_ENABLE_ROLE_COMPACTION_TUNING_ENV, "").strip() == "1":
            return role.compaction_threshold
        return None
    return role.compaction_threshold


# ── 역할 상수 ─────────────────────────────────────────────────────────────────

TEST_WRITER = RoleConfig(
    name="test_writer",
    system_prompt=_load_prompt("test_writer.md"),
    # 테스트 파일 생성만 허용 (edit_file/append 불필요)
    # ask_user: 금지 패턴(동적 import/try-except 추측 등) 대신 구조적 불확실성을
    # 명시적으로 질의할 통로. 프롬프트의 "ask_user 사용 조건" 에서 남용 방지.
    allowed_tools=tuple(READ_TOOLS + ["write_file", "ask_user"]),
    # 중간 임계치 — 테스트 작성은 탐색과 쓰기가 섞이므로 implementer 와 reviewer 사이.
    compaction_threshold=20000,
)

IMPLEMENTER = RoleConfig(
    name="implementer",
    system_prompt=_load_prompt("implementer.md"),
    # 구현 파일 생성 및 수정 모두 허용
    # ask_user: 비즈니스 로직이 불명확할 때 사용자에게 질문
    allowed_tools=tuple(READ_TOOLS + WRITE_TOOLS + ["ask_user"]),
    # tests/는 TestWriter가 작성한 그대로 보존 — Implementer가 덮어쓰기 금지
    blocked_write_dirs=("tests",),
    # 긴 탐색·수정 반복에서 cached prefix 가치가 크므로 일찍 compact.
    compaction_threshold=18000,
)

REVIEWER = RoleConfig(
    name="reviewer",
    system_prompt=_load_prompt("reviewer.md"),
    # 읽기 전용
    allowed_tools=tuple(READ_TOOLS),
    # 전체 코드를 한 번 훑어야 하므로 약간 넉넉한 임계치.
    compaction_threshold=25000,
)
