"""
orchestrator/report.py — Task Report 저장/로드

파이프라인 완료 후 실행 결과를 구조화된 YAML로 저장한다.
이 데이터가 Weekly Report, execution_brief, 시스템 자기 개선 루프의 기반이 된다.

저장 위치: agent-data/reports/task-{id}.yaml
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from orchestrator.pipeline import PipelineMetrics, PipelineResult
from orchestrator.task import Task, TaskStatus
from reports.task_report import TaskReport

logger = logging.getLogger(__name__)

_REPORTS_DIR = Path("agent-data/reports")

# ── LLM 모델별 단가 ($/1M tokens): {model_key: (input_rate, output_rate)} ──────
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic Claude
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    # OpenAI
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    # Zhipu GLM
    "glm-4-flash": (0.10, 0.10),
    "glm-4-plus": (0.70, 0.70),
}


def _model_rate(model_id: str) -> tuple[float, float]:
    """model_id (예: 'anthropic/claude-haiku-4-5-20251001')에서 단가를 반환한다."""
    lm = model_id.lower()
    for key, rates in _MODEL_PRICING.items():
        if key in lm:
            return rates
    return (0.0, 0.0)


def _calculate_cost(
    token_usage: dict,
    models_used: dict[str, str] | None,
) -> float:
    """역할별 토큰 사용량과 모델 정보로 총 USD 비용을 계산한다."""
    if not models_used:
        return 0.0
    total = 0.0
    for role, usage in token_usage.items():
        inp, out = usage if isinstance(usage, (tuple, list)) and len(usage) == 2 else (0, 0)
        model = models_used.get(role, "")
        rate_in, rate_out = _model_rate(model)
        total += (inp * rate_in + out * rate_out) / 1_000_000
    return round(total, 6)


def build_report(
    task: Task,
    result: PipelineResult,
    elapsed_seconds: float = 0.0,
    pr_url: str = "",
    orchestrator_attempts: int = 0,
    orchestrator_model: str = "",
    coding_agent_model: str = "",
    orchestrator_summary: str = "",
    models_used: dict[str, str] | None = None,
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

    m = result.metrics
    _mu = models_used or result.models_used or {}
    _total_tokens = sum(inp + out for inp, out in m.token_usage.values())
    _cost_usd = _calculate_cost(m.token_usage, _mu)
    return TaskReport(
        task_id=task.id,
        title=task.title,
        status="COMPLETED" if result.succeeded else "FAILED",
        completed_at=datetime.now(timezone.utc).isoformat(),
        retry_count=task.retry_count,
        total_tokens=_total_tokens,
        cost_usd=_cost_usd,
        test_count=test_count,
        test_pass_first_try=(task.retry_count == 0 and result.succeeded),
        reviewer_verdict=reviewer_verdict,
        time_elapsed_seconds=round(elapsed_seconds, 1),
        failure_reasons=failure_reasons,
        test_output_summary=test_summary,
        reviewer_feedback=reviewer_feedback,
        pr_number=pr_number,
        branch=task.branch_name,
        orchestrator_attempts=orchestrator_attempts,
        orchestrator_model=orchestrator_model,
        coding_agent_model=coding_agent_model,
        orchestrator_summary=orchestrator_summary,
        quality_gate_rejections=m.quality_gate_rejections,
        quality_gate_reasons=m.quality_gate_reasons,
        test_red_to_green_first_try=m.test_red_to_green_first_try,
        impl_retries=m.impl_retries,
        review_retries=m.review_retries,
        dep_files_injected=m.dep_files_injected,
        failed_stage=m.failed_stage,
        models_used=_mu or None,
    )


def save_report(report: TaskReport, reports_dir: Path = _REPORTS_DIR) -> Path:
    """Task Report를 YAML로 저장하고 경로를 반환한다."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{report.task_id}.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(report.to_dict(), f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    logger.info("Task Report 저장: %s", path)
    return path


def load_report(task_id: str, reports_dir: Path = _REPORTS_DIR) -> TaskReport:
    """저장된 Task Report를 로드한다."""
    path = reports_dir / f"{task_id}.yaml"
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
