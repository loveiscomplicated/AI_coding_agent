"""
orchestrator/report.py — Task Report 저장/로드

파이프라인 완료 후 실행 결과를 구조화된 YAML로 저장한다.
이 데이터가 Weekly Report, execution_brief, 시스템 자기 개선 루프의 기반이 된다.

저장 위치: data/reports/task-{id}.yaml
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from orchestrator.pipeline import PipelineResult
from orchestrator.task import Task, TaskStatus

logger = logging.getLogger(__name__)

_REPORTS_DIR = Path("data/reports")


@dataclass
class TaskReport:
    task_id: str
    title: str
    status: str                        # "COMPLETED" | "FAILED"
    completed_at: str                  # ISO 8601 UTC
    retry_count: int = 0
    total_tokens: int = 0              # 향후 LLM 토큰 집계 시 채움
    cost_usd: float = 0.0             # 향후 비용 추적 시 채움
    test_count: int = 0
    test_pass_first_try: bool = False
    reviewer_verdict: str = ""         # "APPROVED" | "CHANGES_REQUESTED" | ""
    time_elapsed_seconds: float = 0.0
    failure_reasons: list[str] = field(default_factory=list)
    test_output_summary: str = ""
    reviewer_feedback: str = ""
    pr_number: int | None = None
    branch: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
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
            },
            "pipeline_result": {
                "test_output_summary": self.test_output_summary,
                "reviewer_feedback": self.reviewer_feedback,
                "pr_number": self.pr_number,
                "branch": self.branch,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskReport":
        m = data.get("metrics", {})
        p = data.get("pipeline_result", {})
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
            test_output_summary=p.get("test_output_summary", ""),
            reviewer_feedback=p.get("reviewer_feedback", ""),
            pr_number=p.get("pr_number"),
            branch=p.get("branch", ""),
        )


def build_report(
    task: Task,
    result: PipelineResult,
    elapsed_seconds: float = 0.0,
    pr_url: str = "",
) -> TaskReport:
    """PipelineResult → TaskReport 변환."""
    test_count = 0
    test_summary = ""
    if result.test_result:
        test_summary = result.test_result.summary
        # "7 passed in 1.2s" 형식에서 숫자 추출
        parts = test_summary.split()
        if parts and parts[0].isdigit():
            test_count = int(parts[0])

    reviewer_verdict = ""
    reviewer_feedback = ""
    if result.review:
        reviewer_verdict = result.review.verdict
        reviewer_feedback = result.review.details

    failure_reasons = []
    if result.failure_reason:
        failure_reasons = [result.failure_reason]
    elif result.test_result and result.test_result.failed_tests:
        failure_reasons = result.test_result.failed_tests

    pr_number: int | None = None
    if pr_url:
        try:
            pr_number = int(pr_url.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            pass

    return TaskReport(
        task_id=task.id,
        title=task.title,
        status="COMPLETED" if result.succeeded else "FAILED",
        completed_at=datetime.now(timezone.utc).isoformat(),
        retry_count=task.retry_count,
        test_count=test_count,
        test_pass_first_try=(task.retry_count == 0 and result.succeeded),
        reviewer_verdict=reviewer_verdict,
        time_elapsed_seconds=round(elapsed_seconds, 1),
        failure_reasons=failure_reasons,
        test_output_summary=test_summary,
        reviewer_feedback=reviewer_feedback,
        pr_number=pr_number,
        branch=task.branch_name,
    )


def save_report(report: TaskReport, reports_dir: Path = _REPORTS_DIR) -> Path:
    """Task Report를 YAML로 저장하고 경로를 반환한다."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"task-{report.task_id}.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(report.to_dict(), f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    logger.info("Task Report 저장: %s", path)
    return path


def load_report(task_id: str, reports_dir: Path = _REPORTS_DIR) -> TaskReport:
    """저장된 Task Report를 로드한다."""
    path = reports_dir / f"task-{task_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Task Report 없음: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return TaskReport.from_dict(data)


def load_reports(
    since: datetime | None = None,
    reports_dir: Path = _REPORTS_DIR,
) -> list[TaskReport]:
    """reports_dir 내 모든 Task Report를 로드한다. since 지정 시 이후 항목만."""
    if not reports_dir.exists():
        return []

    reports = []
    for path in sorted(reports_dir.glob("task-*.yaml")):
        try:
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            report = TaskReport.from_dict(data)
            if since is not None:
                completed = datetime.fromisoformat(report.completed_at)
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=timezone.utc)
                if completed < since:
                    continue
            reports.append(report)
        except Exception as e:
            logger.warning("Task Report 로드 실패 (%s): %s", path, e)

    return reports
