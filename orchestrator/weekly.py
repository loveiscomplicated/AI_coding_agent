"""
orchestrator/weekly.py — Weekly Report 생성기

ISO 주차 기준으로 해당 주의 Task Report들을 집계하여
Sonnet이 마크다운 주간 보고서를 생성한다.

저장 위치: data/reports/weekly/YYYY-WNN.md

사용 예시:
    from orchestrator.weekly import generate_weekly_report
    content, path = generate_weekly_report(llm_fn=my_llm, year=2026, week=15)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from orchestrator.report import TaskReport, load_reports

logger = logging.getLogger(__name__)

_WEEKLY_DIR = Path("agent-data/reports/weekly")


# ── 날짜 계산 ──────────────────────────────────────────────────────────────────

def get_week_range(year: int, week: int) -> tuple[datetime, datetime]:
    """
    ISO 주차(year, week)에 해당하는 월요일 00:00 UTC ~ 일요일 23:59:59 UTC를 반환한다.
    """
    # ISO 8601: 주차의 첫 날은 월요일
    monday = datetime.fromisocalendar(year, week, 1).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )
    sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return monday, sunday


def current_iso_week() -> tuple[int, int]:
    """현재 UTC 기준 ISO (year, week) 반환."""
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    return iso.year, iso.week


# ── 데이터 집계 ───────────────────────────────────────────────────────────────

def collect_week_stats(reports: list[TaskReport]) -> dict:
    """Task Report 목록에서 집계 지표를 계산한다."""
    total = len(reports)
    completed = sum(1 for r in reports if r.status == "COMPLETED")
    failed = total - completed
    success_rate = round(completed / total * 100) if total else 0
    first_try = sum(1 for r in reports if r.test_pass_first_try)
    first_try_rate = round(first_try / total * 100) if total else 0
    total_elapsed = sum(r.time_elapsed_seconds for r in reports)
    avg_elapsed = round(total_elapsed / total, 1) if total else 0
    total_retries = sum(r.retry_count for r in reports)

    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "success_rate": success_rate,
        "first_try_rate": first_try_rate,
        "total_elapsed_seconds": round(total_elapsed, 1),
        "avg_elapsed_seconds": avg_elapsed,
        "total_retries": total_retries,
        "reviewer_approved": sum(
            1 for r in reports if r.reviewer_verdict == "APPROVED"
        ),
    }


def _serialize_reports(reports: list[TaskReport]) -> str:
    """Sonnet 프롬프트용 텍스트로 직렬화한다."""
    lines = []
    for r in reports:
        lines.append(
            f"task_id: {r.task_id}\n"
            f"title: {r.title}\n"
            f"status: {r.status}\n"
            f"completed_at: {r.completed_at}\n"
            f"retry_count: {r.retry_count}\n"
            f"test_count: {r.test_count}\n"
            f"test_pass_first_try: {r.test_pass_first_try}\n"
            f"reviewer_verdict: {r.reviewer_verdict}\n"
            f"time_elapsed_seconds: {r.time_elapsed_seconds}\n"
            f"failure_reasons: {r.failure_reasons}\n"
            f"reviewer_feedback: {r.reviewer_feedback[:200] if r.reviewer_feedback else ''}\n"
        )
    return "\n---\n".join(lines)


def build_weekly_prompt(
    year: int,
    week: int,
    reports: list[TaskReport],
    prev_content: str = "",
) -> str:
    """Sonnet에 전달할 주간 보고서 생성 프롬프트를 구성한다."""
    monday, sunday = get_week_range(year, week)
    date_range = f"{monday.strftime('%Y-%m-%d')} ~ {sunday.strftime('%Y-%m-%d')}"
    stats = collect_week_stats(reports)
    report_text = _serialize_reports(reports) if reports else "(이번 주 완료된 태스크 없음)"

    prompt_parts = [
        f"# 주간 보고서 생성 요청\n",
        f"기간: {year}년 {week}주차 ({date_range})\n",
        f"\n## 집계 지표\n",
        f"- 총 태스크: {stats['total']}개 (완료: {stats['completed']}, 실패: {stats['failed']})\n",
        f"- 성공률: {stats['success_rate']}%\n",
        f"- 첫 시도 성공률: {stats['first_try_rate']}%\n",
        f"- 평균 소요 시간: {stats['avg_elapsed_seconds']}s\n",
        f"- 총 재시도 횟수: {stats['total_retries']}\n",
        f"\n## Task Report 상세\n",
        report_text,
    ]

    if prev_content:
        prompt_parts.append(f"\n\n## 전주 보고서 (추이 비교용)\n{prev_content[:2000]}")

    return "".join(prompt_parts)


_WEEKLY_SYSTEM = """\
당신은 소프트웨어 개발 에이전트 시스템의 주간 보고서 작성자입니다.

주어진 데이터를 분석하여 다음 마크다운 형식의 주간 보고서를 작성하세요.

형식:
# 주간 보고서: YYYY년 WN주차 (M/D ~ M/D)

## 진행 요약
- 완료/실패/총 태스크 수, 성공률

## 주요 성과
- 완료된 주요 태스크와 그 의미

## 이슈 & 패턴
- 단순 나열이 아닌 원인 분석과 개선 제안
- (예: "재시도율 높음 → acceptance_criteria 구체화 필요")

## 지표
- 첫 시도 성공률, 평균 소요 시간, 재시도 횟수

## 전주 대비 추이
- 전주 보고서가 없으면 이 섹션 생략

## 다음 주 제안
- 시스템 개선을 위한 실행 가능한 제안

---
데이터가 충분하지 않으면 해당 섹션을 간결하게 유지하거나 생략하세요."""


# ── 보고서 생성 & 저장 ────────────────────────────────────────────────────────

def generate_weekly_report(
    llm_fn: Callable[[str, str], str],
    year: int | None = None,
    week: int | None = None,
    reports_dir: Path = Path("agent-data/reports"),
    weekly_dir: Path = _WEEKLY_DIR,
) -> tuple[str, Path]:
    """
    주간 보고서를 생성하고 파일로 저장한다.

    Args:
        llm_fn: (system_prompt, user_prompt) → 생성된 텍스트. 동기 함수.
        year, week: ISO 주차. None이면 현재 주.
        reports_dir: Task Report YAML 디렉토리.
        weekly_dir: 주간 보고서 저장 디렉토리.

    Returns:
        (markdown_content, saved_path)
    """
    if year is None or week is None:
        year, week = current_iso_week()

    monday, sunday = get_week_range(year, week)
    reports = load_reports(since=monday, reports_dir=reports_dir)
    # since가 monday 이전 것도 포함될 수 있으므로 until 필터 추가
    reports = [r for r in reports if _completed_before(r, sunday)]

    # 전주 보고서 로드 (추이 비교용)
    prev_year, prev_week = _prev_week(year, week)
    prev_path = weekly_dir / f"{prev_year}-W{prev_week:02d}.md"
    prev_content = prev_path.read_text(encoding="utf-8") if prev_path.exists() else ""

    user_prompt = build_weekly_prompt(year, week, reports, prev_content)
    content = llm_fn(_WEEKLY_SYSTEM, user_prompt)

    # 저장
    weekly_dir.mkdir(parents=True, exist_ok=True)
    save_path = weekly_dir / f"{year}-W{week:02d}.md"
    save_path.write_text(content, encoding="utf-8")
    logger.info("주간 보고서 저장: %s", save_path)

    return content, save_path


def load_weekly_report(year: int, week: int, weekly_dir: Path = _WEEKLY_DIR) -> str | None:
    """저장된 주간 보고서를 로드한다. 없으면 None."""
    path = weekly_dir / f"{year}-W{week:02d}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def list_weekly_reports(weekly_dir: Path = _WEEKLY_DIR) -> list[dict]:
    """저장된 주간 보고서 목록을 반환한다."""
    if not weekly_dir.exists():
        return []
    result = []
    for p in sorted(weekly_dir.glob("????-W??.md"), reverse=True):
        stem = p.stem  # e.g. "2026-W15"
        try:
            year_str, week_str = stem.split("-W")
            result.append({"year": int(year_str), "week": int(week_str), "path": str(p)})
        except ValueError:
            pass
    return result


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _completed_before(report: TaskReport, until: datetime) -> bool:
    try:
        completed = datetime.fromisoformat(report.completed_at)
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
        return completed <= until
    except (ValueError, AttributeError):
        return True


def _prev_week(year: int, week: int) -> tuple[int, int]:
    """ISO 주차에서 한 주 이전 (year, week)을 반환한다."""
    monday, _ = get_week_range(year, week)
    prev_monday = monday - timedelta(weeks=1)
    iso = prev_monday.isocalendar()
    return iso.year, iso.week
