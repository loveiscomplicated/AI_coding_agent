"""
backend/routers/dashboard.py — 대시보드 데이터 API

파이프라인 현황, Task Report 집계, 마일스톤 보고서 목록을
프론트엔드 대시보드에 제공한다.

엔드포인트:
  GET /api/dashboard/summary   — 전체 요약 (태스크 현황 + 최근 실행 지표)
  GET /api/dashboard/tasks     — 태스크 목록 + 상태 (tasks.yaml 기반)
  GET /api/dashboard/milestones — 마일스톤 보고서 목록
  GET /api/dashboard/milestones/{filename} — 마일스톤 보고서 본문

모든 엔드포인트는 ?reports_dir=&tasks_path= 쿼리 파라미터로
프로젝트별 경로를 지정할 수 있다.
기본값: reports_dir=agent-data/reports, tasks_path=agent-data/tasks.yaml
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from orchestrator.milestone import load_milestone_reports
from orchestrator.report import _model_rate, load_reports
from orchestrator.task import load_tasks
from project_paths import resolve_reports_dir, resolve_tasks_path
from reports.task_report import is_review_approved

router = APIRouter()


# ── 전체 요약 ─────────────────────────────────────────────────────────────────


@router.get("/dashboard/summary")
def get_dashboard_summary(
    reports_dir: str = "agent-data/reports",
    tasks_path: str = "agent-data/tasks.yaml",
) -> dict[str, Any]:
    reports_path = resolve_reports_dir(reports_dir)
    milestones_dir = reports_path / "milestones"

    # 태스크 현황
    task_stats: dict[str, int] = {}
    tp = resolve_tasks_path(tasks_path)
    if tp.exists():
        tasks = load_tasks(tp)
        for task in tasks:
            status = task.status.value
            task_stats[status] = task_stats.get(status, 0) + 1

    # Task Report 집계
    reports = load_reports(reports_dir=reports_path)
    total = len(reports)
    completed = sum(1 for r in reports if r.status == "COMPLETED")
    failed = total - completed
    # APPROVED 와 APPROVED_WITH_SUGGESTIONS 모두 PR 생성 = "승인" 으로 집계한다.
    approved = sum(1 for r in reports if is_review_approved(r.reviewer_verdict))
    total_tests = sum(r.test_count for r in reports)
    total_retries = sum(r.retry_count for r in reports)
    avg_elapsed = (
        round(sum(r.time_elapsed_seconds for r in reports) / total, 1) if total else 0
    )
    first_try = sum(1 for r in reports if r.test_pass_first_try)
    first_try_rate = round(first_try / completed * 100) if completed else 0
    total_tokens = sum(r.total_tokens for r in reports)
    # total_cost_usd 는 이미 cost_usd is None 인 항목을 제외한 합계다.
    # 아래 플래그는 "이 합계에서 제외된 리포트가 있는가" 만 나타낸다.
    total_cost_usd = round(
        sum(r.cost_usd for r in reports if r.cost_usd is not None), 4
    )
    has_missing_costs = any(r.cost_usd is None for r in reports)

    # 비용 추정 품질 집계 + 단가 미등록 모델 수집
    quality_breakdown: dict[str, int] = {"exact": 0, "fallback": 0, "missing": 0}
    missing_models_set: set[str] = set()
    for r in reports:
        q = r.cost_estimation_quality or "missing"
        quality_breakdown[q] = quality_breakdown.get(q, 0) + 1
        if r.models_used:
            for model in r.models_used.values():
                if model and _model_rate(model) is None:
                    missing_models_set.add(model)
    models_with_missing_pricing = sorted(missing_models_set)

    milestone_count = len(load_milestone_reports(milestones_dir=milestones_dir))

    return {
        "task_status": task_stats,
        "metrics": {
            "total_tasks_run": total,
            "completed": completed,
            "failed": failed,
            "success_rate": round(completed / total * 100) if total else 0,
            "approved": approved,
            "total_tests": total_tests,
            "total_retries": total_retries,
            "avg_elapsed_seconds": avg_elapsed,
            "first_try_rate": first_try_rate,
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost_usd,
            "has_missing_costs": has_missing_costs,
        },
        "cost_estimation_quality_breakdown": quality_breakdown,
        "models_with_missing_pricing": models_with_missing_pricing,
        "milestone_count": milestone_count,
    }


# ── 태스크 목록 ───────────────────────────────────────────────────────────────


@router.get("/dashboard/tasks")
def get_dashboard_tasks(
    reports_dir: str = "agent-data/reports",
    tasks_path: str = "agent-data/tasks.yaml",
) -> dict[str, Any]:
    tp = resolve_tasks_path(tasks_path)
    if not tp.exists():
        return {"tasks": []}

    tasks = load_tasks(tp)
    reports = {r.task_id: r for r in load_reports(reports_dir=resolve_reports_dir(reports_dir))}

    result = []
    for task in tasks:
        report = reports.get(task.id)
        result.append({
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "acceptance_criteria": task.acceptance_criteria,
            "failure_reason": task.failure_reason,
            "status": task.status.value,
            "depends_on": task.depends_on,
            "pr_url": task.pr_url or "",
            "report": {
                "test_count": report.test_count if report else 0,
                "retry_count": report.retry_count if report else 0,
                "reviewer_verdict": report.reviewer_verdict if report else "",
                "time_elapsed_seconds": report.time_elapsed_seconds if report else 0,
                "completed_at": report.completed_at if report else "",
                "total_tokens": report.total_tokens if report else 0,
                "cost_usd": report.cost_usd if report else 0.0,
            } if report else None,
        })

    return {"tasks": result}


# ── 마일스톤 보고서 ───────────────────────────────────────────────────────────


@router.get("/dashboard/milestones")
def list_milestones(reports_dir: str = "agent-data/reports") -> dict[str, Any]:
    milestones_dir = resolve_reports_dir(reports_dir) / "milestones"
    return {"milestones": load_milestone_reports(milestones_dir=milestones_dir)}


@router.get("/dashboard/milestones/{filename}")
def get_milestone(filename: str, reports_dir: str = "agent-data/reports") -> dict[str, Any]:
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="잘못된 파일명입니다.")

    path = resolve_reports_dir(reports_dir) / "milestones" / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")

    return {
        "filename": filename,
        "content": path.read_text(encoding="utf-8"),
    }
