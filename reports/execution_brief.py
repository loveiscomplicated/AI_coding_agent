"""
execution_brief 모듈

이 모듈은 TaskReport 리스트를 받아 회의 시작 시 Opus 시스템 프롬프트에 주입할
실행 요약 마크다운을 생성하는 순수 함수를 제공한다.
"""

from datetime import datetime
from typing import Optional
from metrics.collector import TaskReport


def format_report_line(report: TaskReport) -> str:
    """
    TaskReport를 포맷된 문자열로 변환한다.
    
    형식: "- {task_id}: {title} — {status}, 소요: {time_elapsed_seconds}s, 재시도: {retry_count}회"
    
    Args:
        report: TaskReport 객체
        
    Returns:
        포맷된 문자열
    """
    return (
        f"- {report.task_id}: {report.title} — {report.status}, "
        f"소요: {report.time_elapsed_seconds}s, 재시도: {report.retry_count}회"
    )


def generate_brief(
    reports: list[TaskReport],
    since: Optional[datetime] = None,
    until: Optional[datetime] = None
) -> str:
    """
    TaskReport 리스트를 받아 마크다운 실행 요약을 생성한다.
    
    since/until 파라미터로 completed_at 기준 필터링을 지원한다.
    
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
        reports: TaskReport 리스트
        since: 필터링 시작 시간 (inclusive)
        until: 필터링 종료 시간 (inclusive)
        
    Returns:
        마크다운 형식의 실행 요약 문자열
    """
    # 필터링 적용
    filtered_reports = reports
    
    if since is not None:
        filtered_reports = [r for r in filtered_reports if r.completed_at >= since]
    
    if until is not None:
        filtered_reports = [r for r in filtered_reports if r.completed_at <= until]
    
    # 보고서가 없으면 "실행 기록 없음" 반환
    if not filtered_reports:
        return "실행 기록 없음"
    
    # 완료된 태스크와 실패한 태스크 분리
    completed_reports = [r for r in filtered_reports if r.status == "COMPLETED"]
    failed_reports = [r for r in filtered_reports if r.status == "FAILED"]
    
    # 마크다운 생성
    lines = ["## 실행 요약"]
    
    # 완료된 태스크 섹션
    lines.append(f"**완료된 태스크** ({len(completed_reports)}개)")
    for report in completed_reports:
        lines.append(format_report_line(report))
    
    # 실패한 태스크 섹션
    lines.append(f"**실패한 태스크** ({len(failed_reports)}개)")
    for report in failed_reports:
        lines.append(format_report_line(report))
    
    # 핵심 지표 계산
    total_count = len(filtered_reports)
    completed_count = len(completed_reports)
    
    # 전체 성공률
    success_rate = (completed_count / total_count * 100) if total_count > 0 else 0
    
    # 첫 시도 성공 (retry_count == 0이고 status == "COMPLETED")
    first_attempt_success = sum(1 for r in filtered_reports if r.retry_count == 0 and r.status == "COMPLETED")
    first_attempt_success_rate = (first_attempt_success / total_count * 100) if total_count > 0 else 0
    
    # 평균 소요 시간
    avg_time = sum(r.time_elapsed_seconds for r in filtered_reports) / total_count if total_count > 0 else 0
    
    # 핵심 지표 섹션
    lines.append("**핵심 지표**")
    
    # 성공률 포맷팅 (소수점 제거)
    def format_percentage(value: float) -> str:
        if value == int(value):
            return f"{int(value)}%"
        else:
            return f"{value:.2f}%"
    
    lines.append(f"- 전체 성공률: {format_percentage(success_rate)}")
    lines.append(f"- 첫 시도 성공률: {format_percentage(first_attempt_success_rate)}")
    lines.append(f"- 평균 소요 시간: {int(avg_time)}s")
    
    return "\n".join(lines)
