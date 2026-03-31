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
기본값: reports_dir=data/reports, tasks_path=data/tasks.yaml
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from orchestrator.milestone import load_milestone_reports
from orchestrator.report import load_reports
from orchestrator.task import load_tasks

router = APIRouter()


# ── 전체 요약 ─────────────────────────────────────────────────────────────────


@router.get("/dashboard/summary")
def get_dashboard_summary(
    reports_dir: str = "data/reports",
    tasks_path: str = "data/tasks.yaml",
) -> dict[str, Any]:
    reports_path = Path(reports_dir)
    milestones_dir = reports_path / "milestones"

    # 태스크 현황
    task_stats: dict[str, int] = {}
    tp = Path(tasks_path)
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
    approved = sum(1 for r in reports if r.reviewer_verdict == "APPROVED")
    total_tests = sum(r.test_count for r in reports)
    total_retries = sum(r.retry_count for r in reports)
    avg_elapsed = (
        round(sum(r.time_elapsed_seconds for r in reports) / total, 1) if total else 0
    )
    first_try = sum(1 for r in reports if r.test_pass_first_try)
    first_try_rate = round(first_try / completed * 100) if completed else 0

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
        },
        "milestone_count": milestone_count,
    }


# ── 태스크 목록 ───────────────────────────────────────────────────────────────


@router.get("/dashboard/tasks")
def get_dashboard_tasks(
    reports_dir: str = "data/reports",
    tasks_path: str = "data/tasks.yaml",
) -> dict[str, Any]:
    tp = Path(tasks_path)
    if not tp.exists():
        return {"tasks": []}

    tasks = load_tasks(tp)
    reports = {r.task_id: r for r in load_reports(reports_dir=Path(reports_dir))}

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
def list_milestones(reports_dir: str = "data/reports") -> dict[str, Any]:
    milestones_dir = Path(reports_dir) / "milestones"
    return {"milestones": load_milestone_reports(milestones_dir=milestones_dir)}


@router.get("/dashboard/milestones/{filename}")
def get_milestone(filename: str, reports_dir: str = "data/reports") -> dict[str, Any]:
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="잘못된 파일명입니다.")

    path = Path(reports_dir) / "milestones" / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")

    return {
        "filename": filename,
        "content": path.read_text(encoding="utf-8"),
    }
