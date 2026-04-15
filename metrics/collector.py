"""
메트릭 수집기 모듈

TaskReport를 YAML 파일로 저장/로드하고, 여러 Report를 집계한다.
TaskReport 타입의 단일 소스는 orchestrator.report.TaskReport를 사용한다.
"""
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from reports.task_report import TaskReport


def _to_flat_dict(report: TaskReport) -> dict[str, Any]:
    """collector 레거시 평면 포맷으로 변환한다."""
    return {
        "task_id": report.task_id,
        "title": report.title,
        "status": report.status,
        "completed_at": report.completed_at,
        "retry_count": report.retry_count,
        "time_elapsed_seconds": report.time_elapsed_seconds,
        "test_count": report.test_count,
        "test_pass_first_try": report.test_pass_first_try,
        "reviewer_verdict": report.reviewer_verdict,
        "failure_reasons": report.failure_reasons,
        "reviewer_feedback": report.reviewer_feedback,
        "models_used": report.models_used,
    }


def _from_flat_dict(data: dict[str, Any]) -> TaskReport:
    """collector 레거시 평면 포맷 dict를 TaskReport로 변환한다."""
    return TaskReport(
        task_id=data["task_id"],
        title=data.get("title", ""),
        status=data.get("status", ""),
        completed_at=data.get("completed_at"),
        retry_count=data.get("retry_count", 0),
        total_tokens=data.get("total_tokens", 0),
        cost_usd=data.get("cost_usd", 0.0),
        test_count=data.get("test_count", 0),
        test_pass_first_try=data.get("test_pass_first_try", False),
        reviewer_verdict=data.get("reviewer_verdict", ""),
        time_elapsed_seconds=data.get("time_elapsed_seconds", 0.0),
        failure_reasons=data.get("failure_reasons") or [],
        reviewer_feedback=data.get("reviewer_feedback", ""),
        models_used=data.get("models_used"),
    )


def save_report(report: TaskReport, reports_dir: str = "agent-data/reports") -> Path:
    """
    TaskReport를 YAML 파일로 저장한다.

    파일명: task-{task_id}.yaml
    reports_dir이 존재하지 않으면 자동으로 생성한다.

    Args:
        report: 저장할 TaskReport 인스턴스
        reports_dir: 저장할 디렉토리 경로 (기본값: "agent-data/reports")

    Returns:
        저장된 파일의 Path 객체
    """
    dir_path = Path(reports_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    file_path = dir_path / f"{report.task_id}.yaml"

    data = _to_flat_dict(report)
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    return file_path


def load_reports(
    reports_dir: str = "agent-data/reports",
    since: Optional[datetime] = None,
) -> list[TaskReport]:
    """
    디렉토리 내 모든 YAML 파일을 TaskReport 리스트로 로드한다.

    since가 주어지면 completed_at이 그 이후(포함)인 것만 반환한다.
    completed_at이 None인 항목은 since 필터링 시 제외된다.

    Args:
        reports_dir: YAML 파일이 있는 디렉토리 경로 (기본값: "agent-data/reports")
        since: 이 시각 이후의 report만 반환 (None이면 전체 반환)

    Returns:
        TaskReport 인스턴스 리스트
    """
    dir_path = Path(reports_dir)

    # 디렉토리가 없으면 빈 리스트 반환
    if not dir_path.exists():
        return []

    reports: list[TaskReport] = []
    for yaml_file in sorted(dir_path.glob("*.yaml")):
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if data is None:
            continue

        # orchestrator.report의 중첩 포맷도 그대로 읽을 수 있게 지원한다.
        report = (
            TaskReport.from_dict(data)
            if isinstance(data, dict) and "metrics" in data
            else _from_flat_dict(data)
        )

        # since 필터링
        if since is not None:
            if report.completed_at is None:
                # completed_at이 None이면 제외
                continue
            if isinstance(report.completed_at, datetime):
                completed_dt = report.completed_at
            else:
                completed_dt = datetime.fromisoformat(report.completed_at)
            if completed_dt < since:
                continue

        reports.append(report)

    return reports


def aggregate(reports: list[TaskReport]) -> dict[str, Any]:
    """
    여러 TaskReport를 집계하여 통계 딕셔너리를 반환한다.

    Args:
        reports: TaskReport 인스턴스 리스트

    Returns:
        집계 결과 딕셔너리:
            - total: 전체 report 수
            - completed: status=="COMPLETED"인 수
            - failed: status=="FAILED"인 수
            - success_rate: completed/total*100 (정수 반올림, total=0이면 0)
            - first_try_rate: test_pass_first_try==True인 수/total*100 (정수 반올림)
            - avg_elapsed_seconds: time_elapsed_seconds 평균 (total=0이면 0)
            - total_retries: retry_count 합계
            - reviewer_approved: reviewer_verdict=="APPROVED"인 수
    """
    total = len(reports)

    if total == 0:
        return {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "success_rate": 0,
            "first_try_rate": 0,
            "avg_elapsed_seconds": 0,
            "total_retries": 0,
            "reviewer_approved": 0,
        }

    completed = sum(1 for r in reports if r.status == "COMPLETED")
    failed = sum(1 for r in reports if r.status == "FAILED")
    first_try_count = sum(1 for r in reports if r.test_pass_first_try)
    total_elapsed = sum(r.time_elapsed_seconds for r in reports)
    total_retries = sum(r.retry_count for r in reports)
    reviewer_approved = sum(1 for r in reports if r.reviewer_verdict == "APPROVED")

    success_rate = round(completed / total * 100)
    first_try_rate = round(first_try_count / total * 100)
    avg_elapsed_seconds = total_elapsed / total

    # 모델별 통계 (models_used가 있는 태스크 기준, 역할 구분 없이 모델 단위 집계)
    model_stats: dict[str, dict] = {}
    for r in reports:
        if not r.models_used:
            continue
        seen_models: set[str] = set()
        for model_key in r.models_used.values():
            if model_key in seen_models:
                continue
            seen_models.add(model_key)
            if model_key not in model_stats:
                model_stats[model_key] = {"total": 0, "completed": 0}
            model_stats[model_key]["total"] += 1
            if r.status == "COMPLETED":
                model_stats[model_key]["completed"] += 1
    for stats in model_stats.values():
        stats["success_rate"] = round(stats["completed"] / stats["total"], 4) if stats["total"] else 0

    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "success_rate": success_rate,
        "first_try_rate": first_try_rate,
        "avg_elapsed_seconds": avg_elapsed_seconds,
        "total_retries": total_retries,
        "reviewer_approved": reviewer_approved,
        "model_stats": model_stats,
    }
