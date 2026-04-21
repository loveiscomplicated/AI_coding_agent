"""
orchestrator/escalation.py — Tier escalation 로직

개념 분리:
  complexity (Task 필드): "simple" | "non-simple"
  tier       (모델 선택): "simple" | "standard" | "complex"

매핑:
  complexity=simple      → tier chain [simple, standard]
  complexity=non-simple  → tier chain [standard, complex]

실패 유형에 따라 escalation 가능 여부를 판정한다.
  - LOGIC_ERROR, MAX_ITER_EXCEEDED → escalation 가능 (모델 능력 부족 의심)
  - ENV_ERROR, COLLECTION_ERROR 등 인프라 실패 → escalation 불가 (모델 교체 무의미)
"""
from __future__ import annotations

# escalation 시 각 tier 내부의 최대 intervention retry 횟수
TIER_INTERNAL_MAX_RETRIES: int = 1

# complexity 값 → tier 순서 매핑
ESCALATION_CHAIN: dict[str, list[str]] = {
    "simple":     ["simple", "standard"],
    "non-simple": ["standard", "complex"],
}


def resolve_tier_chain(complexity: str | None) -> list[str]:
    """
    complexity 값에서 tier escalation 순서를 반환한다.

    complexity가 None이거나 알 수 없는 값이면 보수적으로 non-simple 체인을 사용한다.
    """
    return ESCALATION_CHAIN.get(complexity or "", ESCALATION_CHAIN["non-simple"])


def should_escalate_tier(failure_reason: str | None) -> bool:
    """
    실패 유형이 모델 능력 부족인지 판정한다.

    True면 다음 tier로 escalation, False면 같은 tier에서 종료.
    orchestrator.intervention.classify_failure()를 재사용하여
    기존 failure_reason 문자열(예: "[MAX_ITER] ...", "Reviewer CHANGES_REQUESTED ...")을
    처리한다.
    """
    if not failure_reason:
        return False

    from orchestrator.intervention import FailureType, classify_failure

    # escalation 가능한 유형 — 모델이 더 강력하면 해결 가능한 실패
    _ESCALATABLE: frozenset[FailureType] = frozenset({
        FailureType.LOGIC_ERROR,
        FailureType.MAX_ITER_EXCEEDED,
    })

    ft = classify_failure(failure_reason)
    return ft in _ESCALATABLE