"""
TaskReport 스키마 단일 소스 모듈.

orchestrator/metrics/report 계층에서 공통으로 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskReport:
    task_id: str
    title: str
    status: str                        # "COMPLETED" | "FAILED"
    completed_at: str | None           # ISO 8601 UTC
    retry_count: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    test_count: int = 0
    test_pass_first_try: bool = False
    reviewer_verdict: str = ""
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
    test_red_to_green_first_try: bool = False
    impl_retries: int = 0
    review_retries: int = 0
    dep_files_injected: int = 0
    failed_stage: str = ""
    models_used: dict[str, str] | None = None

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
                "test_count": self.test_count,
                "test_pass_first_try": self.test_pass_first_try,
                "reviewer_verdict": self.reviewer_verdict,
                "time_elapsed_seconds": self.time_elapsed_seconds,
                "failure_reasons": self.failure_reasons,
                "quality_gate_rejections": self.quality_gate_rejections,
                "quality_gate_reasons": self.quality_gate_reasons,
                "test_red_to_green_first_try": self.test_red_to_green_first_try,
                "impl_retries": self.impl_retries,
                "review_retries": self.review_retries,
                "dep_files_injected": self.dep_files_injected,
                "failed_stage": self.failed_stage,
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
            cost_usd=m.get("cost_usd", 0.0),
            test_count=m.get("test_count", 0),
            test_pass_first_try=m.get("test_pass_first_try", False),
            reviewer_verdict=m.get("reviewer_verdict", ""),
            time_elapsed_seconds=m.get("time_elapsed_seconds", 0.0),
            failure_reasons=m.get("failure_reasons", []),
            quality_gate_rejections=m.get("quality_gate_rejections", 0),
            quality_gate_reasons=m.get("quality_gate_reasons", []),
            test_red_to_green_first_try=m.get("test_red_to_green_first_try", False),
            impl_retries=m.get("impl_retries", 0),
            review_retries=m.get("review_retries", 0),
            dep_files_injected=m.get("dep_files_injected", 0),
            failed_stage=m.get("failed_stage", ""),
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
