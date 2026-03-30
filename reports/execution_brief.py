"""
execution_brief 모듈

TaskReport 리스트를 받아 회의 시작 시 Opus 시스템 프롬프트에 주입할
실행 요약 마크다운을 생성하는 순수 함수 모듈이다. LLM을 호출하지 않는다.
"""
from datetime import datetime
from typing import Optional

from metrics.collector import TaskReport


def format_report_line(report: TaskReport) -> str:
    """
    TaskReport를 포맷팅된 문자열로 변환한다.

    형식: "- {task_id}: {title} — {status}, 소요: {time_elapsed_seconds}s, 재시도: {retry_count}회"

    Args:
        report: TaskReport 객체

    Returns:
        포맷팅된 문자열
    """
    return (
        f"- {report.task_id}: {report.title} — {report.status}, "
        f"소요: {report.time_elapsed_seconds}s, "
        f"재시도: {report.retry_count}회"
    )


def generate_brief(
    reports: list,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None
) -> str:
    """
    TaskReport 리스트를 받아 마크다운 실행 요약을 생성한다.

    since/until로 기간 필터링을 지원한다.

    반환 형식:
        ## 실행 요약
        **완료된 태스크** (N개)
        - task-xxx: 제목 — COMPLETED, 소요: Xs, 재시도: N회
        **실패한 태스크** (N개)
        - ...
        **핵심 지표**
        - 전체 성공률: N%
        - 첫 시도 성공률: N%
        - 평균 소요 시간: Xs

    보고서가 없으면 "실행 기록 없음"을 반환한다.

    Args:
        reports: TaskReport 객체의 리스트
        since: 필터링 시작 시간 (completed_at 기준, 이 시간 이후)
        until: 필터링 종료 시간 (completed_at 기준, 이 시간 이전)

    Returns:
        마크다운 형식의 실행 요약 문자열
    """
    # since/until 기간 필터링
    filtered = list(reports)
    if since is not None:
        filtered = [r for r in filtered if r.completed_at >= since]
    if until is not None:
        filtered = [r for r in filtered if r.completed_at <= until]

    # 보고서가 없으면 "실행 기록 없음" 반환
    if not filtered:
        return "실행 기록 없음"

    # COMPLETED / FAILED 분리
    completed = [r for r in filtered if r.status == "COMPLETED"]
    failed = [r for r in filtered if r.status != "COMPLETED"]

    total = len(filtered)

    # 전체 성공률
    success_rate = round(len(completed) / total * 100) if total > 0 else 0

    # 첫 시도 성공률: retry_count == 0 이고 COMPLETED인 태스크 / 전체
    first_attempt_success = [r for r in completed if r.retry_count == 0]
    first_attempt_rate = round(len(first_attempt_success) / total * 100) if total > 0 else 0

    # 평균 소요 시간
    avg_time = round(sum(r.time_elapsed_seconds for r in filtered) / total) if total > 0 else 0

    # 마크다운 조립
    lines = ["## 실행 요약"]

    # 완료된 태스크 섹션
    lines.append(f"**완료된 태스크** ({len(completed)}개)")
    if completed:
        for r in completed:
            lines.append(format_report_line(r))
    else:
        lines.append("- 없음")

    # 실패한 태스크 섹션
    lines.append(f"**실패한 태스크** ({len(failed)}개)")
    if failed:
        for r in failed:
            lines.append(format_report_line(r))
    else:
        lines.append("- 없음")

    # 핵심 지표 섹션
    lines.append("**핵심 지표**")
    lines.append(f"- 전체 성공률: {success_rate}%")
    lines.append(f"- 첫 시도 성공률: {first_attempt_rate}%")
    lines.append(f"- 평균 소요 시간: {avg_time}s")

    return "\n".join(lines)
