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

import math
from typing import Any

from fastapi import APIRouter, HTTPException

from orchestrator.milestone import load_milestone_reports
from orchestrator.report import _model_rate, load_reports
from orchestrator.task import load_tasks
from project_paths import resolve_reports_dir, resolve_tasks_path
from reports.task_report import TaskReport, is_review_approved

router = APIRouter()

# T2: 이상치 태스크 탐지 임계값.
# - iteration count: 표본 N>=10 에서는 역할별 mean+2σ, 미만이면 절대 fallback
# - single iteration tokens: 절대 임계 50_000 초과 (어떤 역할이든)
_MAX_ITER_TOKENS_ABSOLUTE = 50_000
# 역할별 iteration 절대 상한. 샘플이 적을 때 σ 가 극단값 자신에 지배되어
# outlier 를 숨기는 masking 을 방지하는 하한 가드 역할. ReactLoop 기본 max
# (ScopedReactLoop 25) 보다 넉넉해야 "정상적으로 좀 긴 세션" 과 구분된다.
_MAX_ITER_COUNT_ABSOLUTE = 30
# 이 미만이면 μ+2σ 대신 절대 fallback 을 사용한다.
# 예: [5, 100] → μ=52.5, σ=47.5, μ+2σ=147.5 → 100 이 outlier 로 안 잡힘.
# N<10 은 통계적으로 신뢰할 수 없는 구간이므로 초기 프로젝트에서 runaway 를
# 놓치지 않도록 절대 임계를 쓴다.
_MIN_SAMPLES_FOR_SIGMA = 10
_ITER_COUNT_SIGMA_MULTIPLIER = 2.0


def _detect_outlier_tasks(reports: list[TaskReport]) -> list[dict[str, Any]]:
    """리포트 목록에서 이상치 태스크를 탐지한다.

    기준:
      1. iteration_count > 역할별 임계치:
         - 표본 N >= _MIN_SAMPLES_FOR_SIGMA(10) → mean + 2σ
         - 그 외 (N < 10)                       → _MAX_ITER_COUNT_ABSOLUTE(30)
      2. max_single_iteration_tokens > 50_000 (절대 임계)

    두 조건 중 하나라도 해당하면 outlier 로 분류된다.
    같은 태스크가 양쪽에 걸리면 1건만 — high_iteration_count 가 우선.
    역할별 통계는 iteration 이 기록된 태스크만으로 계산한다 (0 건 제외).
    """
    # 역할별 iteration 분포 수집 (평균/표준편차 계산용)
    per_role_counts: dict[str, list[int]] = {}
    for r in reports:
        for role, count in (r.iteration_count_by_role or {}).items():
            if count > 0:
                per_role_counts.setdefault(role, []).append(count)

    # 역할별 임계치 결정: 표본 충분하면 mean+2σ, 부족하면 절대 fallback.
    role_thresholds: dict[str, float] = {}
    for role, counts in per_role_counts.items():
        if len(counts) >= _MIN_SAMPLES_FOR_SIGMA:
            mean = sum(counts) / len(counts)
            variance = sum((c - mean) ** 2 for c in counts) / len(counts)
            sigma = math.sqrt(variance)
            role_thresholds[role] = mean + _ITER_COUNT_SIGMA_MULTIPLIER * sigma
        else:
            role_thresholds[role] = float(_MAX_ITER_COUNT_ABSOLUTE)

    outliers: list[dict[str, Any]] = []
    for r in reports:
        # 1) iteration 카운트 기준
        # 여러 역할이 동시에 임계를 초과하면 "가장 비정상적인" 역할
        # (exceedance = count - threshold 최대) 을 대표로 선택한다.
        # raw count 로 비교하면 임계가 더 높은 역할의 정상 상한에 가까운 값이
        # 다른 역할의 심각한 초과보다 크게 잡혀 잘못 대표되는 경우가 생긴다.
        flagged: tuple[str, int, float] | None = None  # (role, count, exceedance)
        for role, count in (r.iteration_count_by_role or {}).items():
            threshold = role_thresholds.get(role)
            if threshold is None or count <= threshold:
                continue
            exceedance = count - threshold
            if flagged is None or exceedance > flagged[2]:
                flagged = (role, count, exceedance)

        if flagged is not None:
            outliers.append({
                "task_id": r.task_id,
                "reason": "high_iteration_count",
                "value": flagged[1],  # UI 표시는 raw count 로 — 익숙한 단위.
                "role": flagged[0],
            })
            continue  # high_iteration_count 가 우선 — 같은 태스크 중복 방지

        # 2) 단일 iteration 토큰 기준 (역할 정보 없음 — call_log 에서 추적되지 않음)
        if r.max_single_iteration_tokens > _MAX_ITER_TOKENS_ABSOLUTE:
            outliers.append({
                "task_id": r.task_id,
                "reason": "high_single_iteration_tokens",
                "value": r.max_single_iteration_tokens,
                "role": "",
            })

    # 큰 이상치부터 정렬 (UI 에서 우선순위 표시)
    outliers.sort(key=lambda d: d["value"], reverse=True)
    return outliers


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

    outlier_tasks = _detect_outlier_tasks(reports)

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
        "outlier_tasks": outlier_tasks,
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
            "complexity": task.complexity,
            "report": {
                "test_count": report.test_count if report else 0,
                "retry_count": report.retry_count if report else 0,
                "reviewer_verdict": report.reviewer_verdict if report else "",
                "time_elapsed_seconds": report.time_elapsed_seconds if report else 0,
                "completed_at": report.completed_at if report else "",
                "total_tokens": report.total_tokens if report else 0,
                "cost_usd": report.cost_usd if report else 0.0,
                "models_escalated": report.models_escalated if report else False,
                "successful_tier": report.successful_tier if report else None,
                "escalation_trigger": report.escalation_trigger if report else None,
                "tier_attempts": report.tier_attempts if report else [],
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
