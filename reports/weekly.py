"""Weekly Report 생성기 모듈

TaskReport 리스트를 받아 주차 기준 주간 마크다운 보고서를 생성하는
순수 함수 모듈이다.
"""
from datetime import datetime, timedelta, timezone

from reports.task_report import TaskReport


def _normalize_completed_at(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _status_of(report: TaskReport) -> str:
    value = report.status
    return value.value if hasattr(value, "value") else str(value)


def get_week_range(year: int, week: int) -> tuple[datetime, datetime]:
    """주차의 월요일 00:00 UTC ~ 일요일 23:59:59 UTC를 반환한다.

    해당 연도의 첫 번째 월요일을 1주차 시작으로 삼아
    week번째 주의 범위를 계산한다.

    Args:
        year: 연도
        week: 주차 번호 (1부터 시작)

    Returns:
        (월요일 00:00:00 UTC, 일요일 23:59:59 UTC) 튜플
    """
    # 해당 연도 1월 1일
    jan1 = datetime(year, 1, 1, tzinfo=timezone.utc)
    # 1월 1일의 요일 (0=월요일, 6=일요일)
    jan1_weekday = jan1.weekday()

    # 연도의 첫 번째 월요일 계산
    # 1월 1일이 월요일(0)이면 그대로, 아니면 다음 월요일로 이동
    if jan1_weekday == 0:
        first_monday = jan1
    else:
        first_monday = jan1 + timedelta(days=(7 - jan1_weekday))

    # week번째 주의 월요일 (1주차 = first_monday)
    monday = first_monday + timedelta(weeks=week - 1)
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)

    # 일요일 = 월요일 + 6일, 23:59:59
    sunday = monday + timedelta(days=6)
    sunday = sunday.replace(hour=23, minute=59, second=59, microsecond=0)

    return monday, sunday


def filter_by_week(
    reports: list[TaskReport], year: int, week: int
) -> list[TaskReport]:
    """completed_at 기준으로 해당 주의 Report만 필터링한다.

    Args:
        reports: TaskReport 리스트
        year: 연도
        week: 주차 번호

    Returns:
        해당 주에 완료된 TaskReport 리스트
    """
    start, end = get_week_range(year, week)
    result = []
    for report in reports:
        completed_at = _normalize_completed_at(report.completed_at)
        if completed_at is None:
            continue
        if start <= completed_at <= end:
            result.append(report)
    return result


def collect_stats(reports: list[TaskReport]) -> dict:
    """TaskReport 리스트에서 집계 통계를 계산한다.

    Args:
        reports: TaskReport 리스트

    Returns:
        집계 딕셔너리:
            - total: 전체 항목 수
            - completed: COMPLETED 상태 항목 수
            - failed: FAILED 상태 항목 수
            - success_rate: 완료율 (completed / total, total=0이면 0)
            - first_try_rate: 첫 시도 성공률 (first_try=True 비율, total=0이면 0)
            - avg_elapsed_seconds: 평균 소요 시간 (total=0이면 0)
            - total_retries: 전체 재시도 횟수 합계
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
        }

    completed = sum(1 for r in reports if _status_of(r) == "COMPLETED")
    failed = sum(1 for r in reports if _status_of(r) == "FAILED")
    success_rate = completed / total
    first_try_count = sum(
        1 for r in reports if getattr(r, "test_pass_first_try", getattr(r, "first_try", False))
    )
    first_try_rate = first_try_count / total
    total_retries = sum(getattr(r, "retry_count", getattr(r, "retries", 0)) for r in reports)
    elapsed_sum = sum(
        getattr(r, "time_elapsed_seconds", getattr(r, "elapsed_seconds", 0.0))
        for r in reports
    )
    avg_elapsed_seconds = elapsed_sum / total

    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "success_rate": success_rate,
        "first_try_rate": first_try_rate,
        "avg_elapsed_seconds": avg_elapsed_seconds,
        "total_retries": total_retries,
    }


def generate_report(reports: list[TaskReport], year: int, week: int) -> str:
    """마크다운 형식의 주간 보고서를 생성한다.

    Args:
        reports: 전체 TaskReport 리스트 (내부에서 해당 주차로 필터링)
        year: 연도
        week: 주차 번호

    Returns:
        마크다운 문자열
    """
    # 해당 주차 보고서 필터링
    week_reports = filter_by_week(reports, year, week)
    stats = collect_stats(week_reports)

    start, end = get_week_range(year, week)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    lines = [
        f"# 주간 보고서: {year}년 {week}주차",
        f"> 기간: {start_str} ~ {end_str}",
        "",
        "## 집계 지표",
        "",
        "| 항목 | 값 |",
        "|------|-----|",
        f"| 전체 태스크 수 | {stats['total']} |",
        f"| 완료 (COMPLETED) | {stats['completed']} |",
        f"| 실패 (FAILED) | {stats['failed']} |",
        f"| 성공률 | {stats['success_rate']:.1%} |",
        f"| 첫 시도 성공률 | {stats['first_try_rate']:.1%} |",
        f"| 평균 소요 시간 (초) | {stats['avg_elapsed_seconds']:.1f} |",
        f"| 총 재시도 횟수 | {stats['total_retries']} |",
        "",
        "## 태스크 목록",
        "",
    ]

    if not week_reports:
        lines.append("완료된 태스크 없음")
    else:
        lines.append("| task_id | 상태 | 소요 시간(초) | 재시도 | 첫 시도 |")
        lines.append("|---------|------|--------------|--------|---------|")
        for report in week_reports:
            status_str = _status_of(report)
            elapsed_seconds = getattr(
                report, "time_elapsed_seconds", getattr(report, "elapsed_seconds", 0.0)
            )
            retries = getattr(report, "retry_count", getattr(report, "retries", 0))
            first_try = getattr(report, "test_pass_first_try", getattr(report, "first_try", False))
            lines.append(
                f"| {report.task_id} | {status_str} | {elapsed_seconds:.1f} "
                f"| {retries} | {'✓' if first_try else '✗'} |"
            )

    return "\n".join(lines)
