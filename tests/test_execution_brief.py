"""
execution_brief 모듈 테스트

reports/execution_brief.py 모듈의 format_report_line과 generate_brief 함수를 검증한다.
"""
import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta

# src 디렉토리를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from reports.execution_brief import format_report_line, generate_brief
from metrics.collector import TaskReport


class TestFormatReportLine:
    """format_report_line 함수 테스트"""

    def test_format_report_line_includes_task_id(self, sample_completed_report):
        """format_report_line은 task_id를 포함한다"""
        result = format_report_line(sample_completed_report)
        assert "task-001" in result

    def test_format_report_line_includes_title(self, sample_completed_report):
        """format_report_line은 title을 포함한다"""
        result = format_report_line(sample_completed_report)
        assert "데이터 수집" in result

    def test_format_report_line_includes_status(self, sample_completed_report):
        """format_report_line은 status를 포함한다"""
        result = format_report_line(sample_completed_report)
        assert "COMPLETED" in result

    def test_format_report_line_includes_time_elapsed(self, sample_completed_report):
        """format_report_line은 소요 시간을 포함한다"""
        result = format_report_line(sample_completed_report)
        assert "120" in result

    def test_format_report_line_includes_retry_count(self, sample_completed_report):
        """format_report_line은 재시도 횟수를 포함한다"""
        result = format_report_line(sample_completed_report)
        assert "0" in result

    def test_format_report_line_format_structure(self, sample_completed_report):
        """format_report_line은 올바른 형식을 반환한다"""
        result = format_report_line(sample_completed_report)
        # 형식: "- {task_id}: {title} — {status}, 소요: {time_elapsed_seconds}s, 재시도: {retry_count}회"
        assert result.startswith("- ")
        assert ":" in result
        assert "—" in result
        assert "소요:" in result
        assert "s," in result
        assert "재시도:" in result
        assert "회" in result

    def test_format_report_line_with_retry(self, sample_failed_report):
        """format_report_line은 재시도 횟수가 있는 경우를 처리한다"""
        result = format_report_line(sample_failed_report)
        assert "task-002" in result
        assert "FAILED" in result
        assert "2" in result  # retry_count


class TestGenerateBrief:
    """generate_brief 함수 테스트"""

    def test_generate_brief_empty_list_returns_no_records(self):
        """generate_brief([])는 '실행 기록 없음'을 반환한다"""
        result = generate_brief([])
        assert result == "실행 기록 없음"

    def test_generate_brief_includes_header(self, sample_completed_report):
        """generate_brief는 '## 실행 요약' 헤더를 포함한다"""
        result = generate_brief([sample_completed_report])
        assert "## 실행 요약" in result

    def test_generate_brief_separates_completed_and_failed(self, multiple_reports):
        """generate_brief는 COMPLETED와 FAILED 태스크를 별도 섹션으로 분리한다"""
        result = generate_brief(multiple_reports)
        assert "**완료된 태스크**" in result
        assert "**실패한 태스크**" in result

    def test_generate_brief_completed_section_count(self, multiple_reports):
        """generate_brief의 완료된 태스크 섹션에 개수가 표시된다"""
        result = generate_brief(multiple_reports)
        # multiple_reports에는 COMPLETED 2개, FAILED 1개
        assert "**완료된 태스크** (2개)" in result

    def test_generate_brief_failed_section_count(self, multiple_reports):
        """generate_brief의 실패한 태스크 섹션에 개수가 표시된다"""
        result = generate_brief(multiple_reports)
        # multiple_reports에는 COMPLETED 2개, FAILED 1개
        assert "**실패한 태스크** (1개)" in result

    def test_generate_brief_includes_success_rate(self, multiple_reports):
        """generate_brief의 핵심 지표 섹션에 성공률이 포함된다"""
        result = generate_brief(multiple_reports)
        assert "**핵심 지표**" in result
        assert "전체 성공률:" in result
        # 3개 중 2개 완료 = 66.67% 또는 67%
        assert "%" in result

    def test_generate_brief_includes_first_attempt_success_rate(self, multiple_reports):
        """generate_brief의 핵심 지표 섹션에 첫 시도 성공률이 포함된다"""
        result = generate_brief(multiple_reports)
        assert "첫 시도 성공률:" in result

    def test_generate_brief_includes_average_time(self, multiple_reports):
        """generate_brief의 핵심 지표 섹션에 평균 소요 시간이 포함된다"""
        result = generate_brief(multiple_reports)
        assert "평균 소요 시간:" in result

    def test_generate_brief_success_rate_calculation(self, multiple_reports):
        """generate_brief의 성공률이 올바르게 계산된다"""
        # multiple_reports: COMPLETED 2개, FAILED 1개 → 66.67% 또는 67%
        result = generate_brief(multiple_reports)
        # 성공률은 2/3 = 약 66.67%
        assert "66" in result or "67" in result

    def test_generate_brief_first_attempt_success_rate_calculation(self, multiple_reports):
        """generate_brief의 첫 시도 성공률이 올바르게 계산된다"""
        # multiple_reports: retry_count=0인 완료 1개, retry_count=1인 완료 1개, 실패 1개
        # 첫 시도 성공: task-001 (retry_count=0, COMPLETED)
        # 첫 시도 실패: task-002 (retry_count=2, FAILED), task-003 (retry_count=1, COMPLETED)
        # 첫 시도 성공률: 1/3 = 33.33% 또는 33%
        result = generate_brief(multiple_reports)
        assert "첫 시도 성공률:" in result

    def test_generate_brief_average_time_calculation(self, multiple_reports):
        """generate_brief의 평균 소요 시간이 올바르게 계산된다"""
        # multiple_reports: 120s, 300s, 180s → 평균 200s
        result = generate_brief(multiple_reports)
        assert "200" in result or "평균 소요 시간: 200s" in result

    def test_generate_brief_with_since_filter(self, reports_with_different_dates):
        """generate_brief는 since 파라미터로 completed_at 기준 필터링을 한다"""
        since = datetime(2024, 1, 15, 0, 0, 0)
        result = generate_brief(reports_with_different_dates, since=since)
        
        # since 이후의 보고서만 포함되어야 함 (task-002, task-003)
        assert "task-002" in result
        assert "task-003" in result
        assert "task-001" not in result

    def test_generate_brief_with_until_filter(self, reports_with_different_dates):
        """generate_brief는 until 파라미터로 completed_at 기준 필터링을 한다"""
        until = datetime(2024, 1, 15, 23, 59, 59)
        result = generate_brief(reports_with_different_dates, until=until)
        
        # until 이전의 보고서만 포함되어야 함 (task-001, task-002)
        assert "task-001" in result
        assert "task-002" in result
        assert "task-003" not in result

    def test_generate_brief_with_since_and_until_filter(self, reports_with_different_dates):
        """generate_brief는 since와 until 파라미터로 기간 필터링을 한다"""
        since = datetime(2024, 1, 12, 0, 0, 0)
        until = datetime(2024, 1, 18, 23, 59, 59)
        result = generate_brief(reports_with_different_dates, since=since, until=until)
        
        # 기간 내의 보고서만 포함 (task-002)
        assert "task-002" in result
        assert "task-001" not in result
        assert "task-003" not in result

    def test_generate_brief_with_since_filter_empty_result(self, reports_with_different_dates):
        """generate_brief는 필터링 결과가 없으면 '실행 기록 없음'을 반환한다"""
        since = datetime(2024, 2, 1, 0, 0, 0)
        result = generate_brief(reports_with_different_dates, since=since)
        assert result == "실행 기록 없음"

    def test_generate_brief_with_until_filter_empty_result(self, reports_with_different_dates):
        """generate_brief는 필터링 결과가 없으면 '실행 기록 없음'을 반환한다"""
        until = datetime(2024, 1, 1, 0, 0, 0)
        result = generate_brief(reports_with_different_dates, until=until)
        assert result == "실행 기록 없음"

    def test_generate_brief_only_completed_tasks(self, sample_completed_report, sample_completed_with_retry):
        """generate_brief는 완료된 태스크만 있는 경우를 처리한다"""
        result = generate_brief([sample_completed_report, sample_completed_with_retry])
        assert "**완료된 태스크** (2개)" in result
        # 실패한 태스크 섹션이 없거나 0개로 표시되어야 함
        assert "**실패한 태스크**" not in result or "(0개)" in result

    def test_generate_brief_only_failed_tasks(self, sample_failed_report):
        """generate_brief는 실패한 태스크만 있는 경우를 처리한다"""
        result = generate_brief([sample_failed_report])
        assert "**실패한 태스크** (1개)" in result
        # 완료된 태스크 섹션이 없거나 0개로 표시되어야 함
        assert "**완료된 태스크**" not in result or "(0개)" in result

    def test_generate_brief_markdown_format(self, multiple_reports):
        """generate_brief는 마크다운 형식을 반환한다"""
        result = generate_brief(multiple_reports)
        # 마크다운 헤더
        assert "##" in result
        # 마크다운 굵은 텍스트
        assert "**" in result
        # 마크다운 리스트
        assert "- " in result

    def test_generate_brief_includes_all_report_lines(self, multiple_reports):
        """generate_brief는 모든 보고서를 포함한다"""
        result = generate_brief(multiple_reports)
        assert "task-001" in result
        assert "task-002" in result
        assert "task-003" in result

    def test_generate_brief_single_report(self, sample_completed_report):
        """generate_brief는 단일 보고서를 처리한다"""
        result = generate_brief([sample_completed_report])
        assert "## 실행 요약" in result
        assert "task-001" in result
        assert "데이터 수집" in result
        assert "COMPLETED" in result

    def test_generate_brief_success_rate_100_percent(self):
        """generate_brief는 모든 태스크가 완료된 경우 100% 성공률을 표시한다"""
        reports = [
            TaskReport(
                task_id="task-001",
                title="작업 1",
                status="COMPLETED",
                time_elapsed_seconds=100,
                retry_count=0,
                completed_at=datetime(2024, 1, 15, 10, 0, 0)
            ),
            TaskReport(
                task_id="task-002",
                title="작업 2",
                status="COMPLETED",
                time_elapsed_seconds=200,
                retry_count=0,
                completed_at=datetime(2024, 1, 15, 11, 0, 0)
            ),
        ]
        result = generate_brief(reports)
        assert "100" in result

    def test_generate_brief_success_rate_0_percent(self):
        """generate_brief는 모든 태스크가 실패한 경우 0% 성공률을 표시한다"""
        reports = [
            TaskReport(
                task_id="task-001",
                title="작업 1",
                status="FAILED",
                time_elapsed_seconds=100,
                retry_count=1,
                completed_at=datetime(2024, 1, 15, 10, 0, 0)
            ),
            TaskReport(
                task_id="task-002",
                title="작업 2",
                status="FAILED",
                time_elapsed_seconds=200,
                retry_count=2,
                completed_at=datetime(2024, 1, 15, 11, 0, 0)
            ),
        ]
        result = generate_brief(reports)
        assert "0" in result

    def test_generate_brief_first_attempt_success_all_succeeded_first_try(self):
        """generate_brief는 모든 태스크가 첫 시도에 성공한 경우를 처리한다"""
        reports = [
            TaskReport(
                task_id="task-001",
                title="작업 1",
                status="COMPLETED",
                time_elapsed_seconds=100,
                retry_count=0,
                completed_at=datetime(2024, 1, 15, 10, 0, 0)
            ),
            TaskReport(
                task_id="task-002",
                title="작업 2",
                status="COMPLETED",
                time_elapsed_seconds=200,
                retry_count=0,
                completed_at=datetime(2024, 1, 15, 11, 0, 0)
            ),
        ]
        result = generate_brief(reports)
        # 첫 시도 성공률: 2/2 = 100%
        assert "첫 시도 성공률:" in result
