"""
TaskReport 스키마 단일 소스 모듈.

orchestrator/metrics/report 계층에서 공통으로 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CostEstimationQuality = Literal["exact", "fallback", "missing"]

ReviewerVerdict = Literal[
    "APPROVED",
    "APPROVED_WITH_SUGGESTIONS",
    "CHANGES_REQUESTED",
    "ERROR",
    "",  # reviewer 단계 이전에 실패한 경우
]

QualityGateVerdict = Literal["PASS", "WARNING", "BLOCKED"]

# PR 생성 + 태스크 COMPLETED 로 간주되는 verdict 집합. 집계(dashboard, weekly,
# milestone, collector)와 UI 는 모두 이 집합을 참조해야 한다. APPROVED 만 세고
# APPROVED_WITH_SUGGESTIONS 를 빼면 정상 머지된 PR이 "미승인" 으로 보이는 회귀가
# 생긴다.
APPROVED_VERDICTS: frozenset[str] = frozenset({"APPROVED", "APPROVED_WITH_SUGGESTIONS"})


def is_review_approved(verdict: str | None) -> bool:
    """reviewer_verdict 가 PR 생성으로 이어진 '승인' 상태인지 반환한다."""
    return verdict in APPROVED_VERDICTS


@dataclass
class TaskReport:
    task_id: str
    title: str
    status: str                        # "COMPLETED" | "FAILED"
    completed_at: str | None           # ISO 8601 UTC
    retry_count: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    cost_estimation_quality: CostEstimationQuality = "missing"
    test_count: int = 0
    test_pass_first_try: bool = False
    reviewer_verdict: ReviewerVerdict = ""
    time_elapsed_seconds: float = 0.0
    failure_reasons: list[str] = field(default_factory=list)
    test_output_summary: str = ""
    reviewer_feedback: str = ""
    pr_number: int | None = None
    branch: str = ""
    orchestrator_attempts: int = 0
    orchestrator_model: str = ""
    coding_agent_model: str = ""
    orchestrator_summary: str = ""
    quality_gate_rejections: int = 0
    quality_gate_reasons: list[str] = field(default_factory=list)
    quality_gate_verdict: QualityGateVerdict | None = None
    quality_gate_rule_results: list[dict[str, Any]] = field(default_factory=list)
    test_red_to_green_first_try: bool = False
    impl_retries: int = 0
    review_retries: int = 0
    dep_files_injected: int = 0
    failed_stage: str = ""
    models_used: dict[str, str] | None = None
    total_cached_read_tokens: int = 0
    total_cached_write_tokens: int = 0
    cache_hit_rate: float = 0.0
    token_usage: dict[str, dict[str, int]] | None = None
    token_usage_detail: dict[str, dict[str, int]] | None = None
    # T2: iteration 시계열 요약 — raw call_log 는 JSONL 로 별도 저장되며,
    # 여기에는 outlier 탐지에 필요한 파생 지표만 기록한다 (파일 폭증 방지).
    max_single_iteration_tokens: int = 0
    iteration_count_by_role: dict[str, int] = field(default_factory=dict)
    # T11: escalation 메트릭
    complexity_classified: str | None = None      # "simple" | "non-simple"
    models_escalated: bool = False                # 더 높은 tier로 escalation 발생 여부
    escalation_trigger: str | None = None         # escalation을 유발한 failure_reason 요약
    successful_tier: str | None = None            # 성공한 tier ("simple"/"standard"/"complex")
    tier_attempts: list[dict] = field(default_factory=list)
    # tier_attempts 예시: [{"tier": "standard", "success": False, "failure_type": "LOGIC_ERROR"},
    #                      {"tier": "complex",  "success": True,  "failure_type": None}]

    def __post_init__(self) -> None:
        if self.token_usage is None and self.token_usage_detail is not None:
            self.token_usage = self.token_usage_detail
        elif self.token_usage_detail is None and self.token_usage is not None:
            self.token_usage_detail = self.token_usage

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "completed_at": self.completed_at,
            "metrics": {
                "retry_count": self.retry_count,
                "total_tokens": self.total_tokens,
                "cost_usd": self.cost_usd,
                "cost_estimation_quality": self.cost_estimation_quality,
                "test_count": self.test_count,
                "test_pass_first_try": self.test_pass_first_try,
                "reviewer_verdict": self.reviewer_verdict,
                "time_elapsed_seconds": self.time_elapsed_seconds,
                "failure_reasons": self.failure_reasons,
                "quality_gate_rejections": self.quality_gate_rejections,
                "quality_gate_reasons": self.quality_gate_reasons,
                "quality_gate_verdict": self.quality_gate_verdict,
                "quality_gate_rule_results": self.quality_gate_rule_results,
                "test_red_to_green_first_try": self.test_red_to_green_first_try,
                "impl_retries": self.impl_retries,
                "review_retries": self.review_retries,
                "dep_files_injected": self.dep_files_injected,
                "failed_stage": self.failed_stage,
                "total_cached_read_tokens": self.total_cached_read_tokens,
                "total_cached_write_tokens": self.total_cached_write_tokens,
                "cache_hit_rate": self.cache_hit_rate,
                "max_single_iteration_tokens": self.max_single_iteration_tokens,
                "iteration_count_by_role": dict(self.iteration_count_by_role),
                # escalation fields are omitted when at defaults to preserve legacy YAML shape
                **({
                    "complexity_classified": self.complexity_classified,
                    "models_escalated": self.models_escalated,
                    "escalation_trigger": self.escalation_trigger,
                    "successful_tier": self.successful_tier,
                    "tier_attempts": list(self.tier_attempts),
                } if (self.complexity_classified is not None or self.models_escalated
                     or self.tier_attempts) else {}),
            },
            "pipeline_result": {
                "test_output_summary": self.test_output_summary,
                "reviewer_feedback": self.reviewer_feedback,
                "pr_number": self.pr_number,
                "branch": self.branch,
            },
        }
        if self.orchestrator_attempts:
            d["orchestrator"] = {
                "attempts": self.orchestrator_attempts,
                "orchestrator_model": self.orchestrator_model,
                "coding_agent_model": self.coding_agent_model,
                "summary": self.orchestrator_summary,
            }
        if self.models_used is not None:
            d["models_used"] = self.models_used
        if self.token_usage is not None:
            d["token_usage"] = self.token_usage
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskReport":
        m = data.get("metrics", {})
        p = data.get("pipeline_result", {})
        o = data.get("orchestrator", {})
        return cls(
            task_id=data["task_id"],
            title=data.get("title", ""),
            status=data.get("status", ""),
            completed_at=data.get("completed_at", ""),
            retry_count=m.get("retry_count", 0),
            total_tokens=m.get("total_tokens", 0),
            cost_usd=m.get("cost_usd"),
            cost_estimation_quality=_coerce_quality(m.get("cost_estimation_quality")),
            test_count=m.get("test_count", 0),
            test_pass_first_try=m.get("test_pass_first_try", False),
            reviewer_verdict=m.get("reviewer_verdict", ""),
            time_elapsed_seconds=m.get("time_elapsed_seconds", 0.0),
            failure_reasons=m.get("failure_reasons", []),
            quality_gate_rejections=m.get("quality_gate_rejections", 0),
            quality_gate_reasons=m.get("quality_gate_reasons", []),
            quality_gate_verdict=_coerce_verdict(m.get("quality_gate_verdict")),
            quality_gate_rule_results=list(m.get("quality_gate_rule_results", [])),
            test_red_to_green_first_try=m.get("test_red_to_green_first_try", False),
            impl_retries=m.get("impl_retries", 0),
            review_retries=m.get("review_retries", 0),
            dep_files_injected=m.get("dep_files_injected", 0),
            failed_stage=m.get("failed_stage", ""),
            total_cached_read_tokens=m.get("total_cached_read_tokens", 0),
            total_cached_write_tokens=m.get("total_cached_write_tokens", 0),
            cache_hit_rate=m.get("cache_hit_rate", 0.0),
            max_single_iteration_tokens=m.get("max_single_iteration_tokens", 0),
            iteration_count_by_role=dict(m.get("iteration_count_by_role", {})),
            complexity_classified=m.get("complexity_classified"),
            models_escalated=m.get("models_escalated", False),
            escalation_trigger=m.get("escalation_trigger"),
            successful_tier=m.get("successful_tier"),
            tier_attempts=list(m.get("tier_attempts", [])),
            token_usage=data.get("token_usage", data.get("token_usage_detail")),
            test_output_summary=p.get("test_output_summary", ""),
            reviewer_feedback=p.get("reviewer_feedback", ""),
            pr_number=p.get("pr_number"),
            branch=p.get("branch", ""),
            orchestrator_attempts=o.get("attempts", 0),
            orchestrator_model=o.get("orchestrator_model", ""),
            coding_agent_model=o.get("coding_agent_model", ""),
            orchestrator_summary=o.get("summary", ""),
            models_used=data.get("models_used"),
        )


def _coerce_quality(value: Any) -> CostEstimationQuality:
    """YAML 로드 시 cost_estimation_quality 필드의 값을 Literal 범위로 제한한다.

    구 포맷 (필드 없음) 또는 unknown 문자열은 "missing" 으로 폴백한다.
    """
    if value in ("exact", "fallback", "missing"):
        return value  # type: ignore[return-value]
    return "missing"


def _coerce_verdict(value: Any) -> QualityGateVerdict | None:
    """YAML 로드 시 quality_gate_verdict 를 Literal 범위로 제한한다.

    구 포맷(필드 없음) 또는 unknown 값은 None 으로 폴백.
    """
    if value in ("PASS", "WARNING", "BLOCKED"):
        return value  # type: ignore[return-value]
    return None
