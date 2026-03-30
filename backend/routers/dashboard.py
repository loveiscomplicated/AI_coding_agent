"""
backend/routers/dashboard.py — 대시보드 데이터 API

파이프라인 현황, Task Report 집계, 마일스톤 보고서 목록을
프론트엔드 대시보드에 제공한다.

엔드포인트:
  GET /api/dashboard/summary   — 전체 요약 (태스크 현황 + 최근 실행 지표)
  GET /api/dashboard/tasks     — 태스크 목록 + 상태 (tasks.yaml 기반)
  GET /api/dashboard/milestones — 마일스톤 보고서 목록
  GET /api/dashboard/milestones/{filename} — 마일스톤 보고서 본문
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from orchestrator.milestone import load_milestone_reports, _MILESTONES_DIR
from orchestrator.report import load_reports
from orchestrator.task import load_tasks

router = APIRouter()

_TASKS_PATH = Path("data/tasks.yaml")


# ── 전체 요약 ─────────────────────────────────────────────────────────────────


@router.get("/dashboard/summary")
def get_dashboard_summary() -> dict[str, Any]:
    """
    대시보드 메인에 표시할 전체 요약을 반환한다.

    - 태스크 상태별 카운트
    - 최근 실행 집계 지표 (Task Report 전체 기준)
    - 마일스톤 보고서 수
    """
    # 태스크 현황
    task_stats: dict[str, int] = {}
    if _TASKS_PATH.exists():
        tasks = load_tasks(_TASKS_PATH)
        for task in tasks:
            status = task.status.value
            task_stats[status] = task_stats.get(status, 0) + 1

    # Task Report 집계
    reports = load_reports()
    total = len(reports)
    completed = sum(1 for r in reports if r.status == "COMPLETED")
    failed = total - completed
    approved = sum(1 for r in reports if r.reviewer_verdict == "APPROVED")
    total_tests = sum(r.test_count for r in reports)
    total_retries = sum(r.retry_count for r in reports)
    avg_elapsed = (
        round(sum(r.time_elapsed_seconds for r in reports) / total, 1) if total else 0
    )
    first_try = sum(1 for r in reports if r.test_pass_first_try)
    first_try_rate = round(first_try / completed * 100) if completed else 0

    milestone_count = len(load_milestone_reports())

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
        },
        "milestone_count": milestone_count,
    }


# ── 태스크 목록 ───────────────────────────────────────────────────────────────


@router.get("/dashboard/tasks")
def get_dashboard_tasks() -> dict[str, Any]:
    """
    tasks.yaml 의 태스크 목록과 각 태스크의 최신 Report 지표를 반환한다.
    """
    if not _TASKS_PATH.exists():
        return {"tasks": []}

    tasks = load_tasks(_TASKS_PATH)
    reports = {r.task_id: r for r in load_reports()}

    result = []
    for task in tasks:
        report = reports.get(task.id)
        result.append({
            "id": task.id,
            "title": task.title,
            "status": task.status.value,
            "depends_on": task.depends_on,
            "pr_url": task.pr_url or "",
            "report": {
                "test_count": report.test_count if report else 0,
                "retry_count": report.retry_count if report else 0,
                "reviewer_verdict": report.reviewer_verdict if report else "",
                "time_elapsed_seconds": report.time_elapsed_seconds if report else 0,
                "completed_at": report.completed_at if report else "",
            } if report else None,
        })

    return {"tasks": result}


# ── 마일스톤 보고서 ───────────────────────────────────────────────────────────


@router.get("/dashboard/milestones")
def list_milestones() -> dict[str, Any]:
    """마일스톤 보고서 목록을 최신순으로 반환한다."""
    return {"milestones": load_milestone_reports()}


@router.get("/dashboard/milestones/{filename}")
def get_milestone(filename: str) -> dict[str, Any]:
    """마일스톤 보고서 본문을 반환한다."""
    # path traversal 방지
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="잘못된 파일명입니다.")

    path = _MILESTONES_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")

    return {
        "filename": filename,
        "content": path.read_text(encoding="utf-8"),
    }
