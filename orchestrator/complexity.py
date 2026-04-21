"""
orchestrator/complexity.py — 태스크 복잡도 자동 계산

binary 판정: "simple" 또는 "non-simple".
  - simple:     단일 파일, 의존성 없음, 기준 3개 이하, description 800자 이하
  - non-simple: 위 조건 중 하나라도 어긋남

non-simple 태스크는 standard tier로 실행되고, 실패 시 complex tier로 escalation된다.
simple 태스크는 simple tier로 실행되고, 실패 시 standard tier로 escalation된다.

참고: Task.complexity 값("simple"/"non-simple")과 COMPLEXITY_ROLE_MODEL_MAP의
tier 키("simple"/"standard"/"complex")는 다른 개념이다 — orchestrator/escalation.py 참조.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.task import Task

SIMPLE_THRESHOLDS: dict[str, int] = {
    "target_files_max": 1,
    "depends_on_max": 0,
    "criteria_max": 3,
    "description_max_chars": 800,
}


def compute_complexity(task: "dict | Task") -> str:
    """
    태스크의 정량 신호로 simple 여부를 판정한다.

    Returns:
        "simple" — 모든 임계값 충족 시
        "non-simple" — 하나라도 초과 시 (빈 target_files도 non-simple)
    """
    target_files = _get_field(task, "target_files", default=[])
    depends_on = _get_field(task, "depends_on", default=[])
    criteria = _get_field(task, "acceptance_criteria", default=[])
    description = _get_field(task, "description", default="")

    if (
        len(target_files) == 1                                        # 정확히 1개
        and len(depends_on) <= SIMPLE_THRESHOLDS["depends_on_max"]    # 의존성 없음
        and len(criteria) <= SIMPLE_THRESHOLDS["criteria_max"]        # 기준 3개 이하
        and len(description) <= SIMPLE_THRESHOLDS["description_max_chars"]  # 800자 이하
    ):
        return "simple"
    return "non-simple"


def normalize_complexity(value: str | None) -> str | None:
    """
    legacy 3단계 값("standard", "complex")을 "non-simple"로 정규화한다.

    Returns:
        "simple"      — 입력이 "simple"
        "non-simple"  — 입력이 "standard" or "complex"
        None          — 입력이 None 또는 알 수 없는 값
    """
    if value == "simple":
        return "simple"
    if value in ("non-simple", "standard", "complex"):
        return "non-simple"
    return None


def _get_field(task: "dict | object", name: str, default):
    """dict와 dataclass 모두 지원하는 필드 접근 헬퍼."""
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)