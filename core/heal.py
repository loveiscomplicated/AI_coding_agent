"""
core/heal.py — 자가 수정(Self-Healing) 로직

에러를 세 등급으로 분류하고, FIXABLE 에러에 대해 LLM이 재계획할 수 있도록
구조화된 힐 프롬프트를 생성한다.

  ErrorClass.TRANSIENT — 동일 입력 재시도 (네트워크·레이트리밋 등)
  ErrorClass.FIXABLE   — LLM이 근본 원인을 분석하고 수정해야 함
  ErrorClass.FATAL     — 즉시 중단 (디스크 풀, 권한 거부 등)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ErrorClass(str, Enum):
    TRANSIENT = "transient"  # 동일 입력 재시도 (LLM 관여 없음)
    FIXABLE = "fixable"      # LLM이 진단·재계획해야 함
    FATAL = "fatal"          # 즉시 중단


@dataclass
class HealContext:
    """FIXABLE 에러에서 힐 프롬프트를 생성하기 위한 컨텍스트."""

    tool_name: str
    tool_input: dict[str, Any]
    error_content: str
    attempt: int          # 1-indexed; 이번이 몇 번째 힐 시도인지
    max_attempts: int     # ReactLoop.max_heal_attempts
    error_class: ErrorClass


# ── 에러 분류 키워드 ────────────────────────────────────────────────────────────

_FATAL_KEYWORDS: tuple[str, ...] = (
    "permission denied",
    "disk full",
    "no space left",
    "out of memory",
    "killed",
    "segmentation fault",
    "core dumped",
)

_TRANSIENT_KEYWORDS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "connection refused",
    "connection reset",
    "temporarily unavailable",
    "service unavailable",
    "rate limit",
    "too many requests",
    "retry",
    "503",
    "429",
)


def classify_error(tool_name: str, error_content: str) -> ErrorClass:
    """
    에러 메시지를 분석해 ErrorClass 를 반환한다.

    우선순위: FATAL > TRANSIENT > FIXABLE
    (기존 _is_fatal_error() 로직을 이 함수로 통합)

    Args:
        tool_name:     실패한 도구 이름 (향후 도구별 오버라이드에 활용 가능)
        error_content: ToolResult.content (에러 메시지 전문)

    Returns:
        ErrorClass 열거값
    """
    lower = error_content.lower()

    if any(kw in lower for kw in _FATAL_KEYWORDS):
        return ErrorClass.FATAL

    if any(kw in lower for kw in _TRANSIENT_KEYWORDS):
        return ErrorClass.TRANSIENT

    return ErrorClass.FIXABLE


def build_heal_prompt(ctx: HealContext) -> str:
    """
    FIXABLE 에러 발생 시 LLM에게 주입할 힐 프롬프트를 생성한다.

    형식:
        [HEAL REQUEST] ...
        Tool: ...
        Attempt: N/M
        Error: ...
        Instructions: ...

    Args:
        ctx: HealContext (도구명, 입력값, 에러 내용, 시도 횟수)

    Returns:
        LLM user 메시지로 추가될 문자열
    """
    # 입력값은 너무 길 수 있으므로 300자로 제한
    input_preview = repr(ctx.tool_input)
    if len(input_preview) > 300:
        input_preview = input_preview[:297] + "..."

    # 에러 내용도 600자로 제한 (컨텍스트 절약)
    error_preview = ctx.error_content
    if len(error_preview) > 600:
        error_preview = error_preview[:597] + "...[생략]"

    lines = [
        f"[HEAL REQUEST] '{ctx.tool_name}' 도구가 실패했습니다 "
        f"(시도 {ctx.attempt}/{ctx.max_attempts}).",
        "",
        f"Tool   : {ctx.tool_name}",
        f"Input  : {input_preview}",
        f"Error  : {error_preview}",
        "",
        "Instructions:",
        "  1. 위 에러의 근본 원인을 분석하세요.",
        "  2. 입력값 또는 접근 방식을 수정하세요.",
        "  3. 동일한 호출을 그대로 반복하지 마세요.",
        f"  4. 남은 힐 시도: {ctx.max_attempts - ctx.attempt}회.",
    ]

    if ctx.attempt >= ctx.max_attempts:
        lines.append(
            "  ⚠️  이번이 마지막 힐 시도입니다. "
            "반드시 올바른 수정을 적용해야 합니다."
        )

    return "\n".join(lines)
