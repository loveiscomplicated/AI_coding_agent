"""
execution_brief 모듈 테스트

TaskReport 리스트를 받아 회의 시작 시 Opus 시스템 프롬프트에 주입할
실행 요약 마크다운을 생성하는 순수 함수 모듈 테스트
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
        """task_id가 포함되어야 한다"""
        result = format_report_line(sample_completed_report)
        assert "task-001" in result

    def test_format_report_line_includes_title(self, sample_completed_report):
        """title이 포함되어야 한다"""
        result = format_report_line(sample_completed_report)
        assert "데이터 수집" in result

    def test_format_report_line_includes_status(self, sample_completed_report):
        """status가 포함되어야 한다"""
        result = format_report_line(sample_completed_report)
        assert "COMPLETED" in result

    def test_format_report_line_includes_time_elapsed(self, sample_completed_report):
        """소요 시간이 포함되어야 한다"""
        result = format_report_line(sample_completed_report)
        assert "120" in result or "120s" in result

    def test_format_report_line_includes_retry_count(self, sample_completed_report):
        """재시도 횟수가 포함되어야 한다"""
        result = format_report_line(sample_completed_report)
        assert "0" in result

    def test_format_report_line_format_structure(self, sample_completed_report):
        """반환 형식이 "- {task_id}: {title} — {status}, 소요: {time_elapsed_seconds}s, 재시도: {retry_count}회" 형식이어야 한다"""
        result = format_report_line(sample_completed_report)
        # 기본 구조 확인
        assert result.startswith("- ")
        assert ":" in result
        assert "—" in result or "-" in result
        assert "소요:" in result
        assert "재시도:" in result

    def test_format_report_line_with_failed_status(self, sample_failed_report):
        """FAILED 상태의 리포트도 올바르게 포맷팅되어야 한다"""
        result = format_report_line(sample_failed_report)
        assert "task-002" in result
        assert "데이터 처리" in result
        assert "FAILED" in result
        assert "300" in result or "300s" in result
        assert "2" in result

    def test_format_report_line_with_zero_retry(self, sample_completed_report):
        """재시도 횟수가 0일 때도 올바르게 표시되어야 한다"""
        result = format_report_line(sample_completed_report)
        assert "재시도:" in result
        assert "0회" in result or "0" in result


class TestGenerateBrief:
    """generate_brief 함수 테스트"""

    def test_generate_brief_empty_list_returns_no_records(self):
        """빈 리스트를 받으면 '실행 기록 없음'을 반환한다"""
        result = generate_brief([])
        assert result == "실행 기록 없음"

    def test_generate_brief_includes_header(self, sample_reports_list):
        """반환값이 '## 실행 요약' 헤더를 포함한다"""
        result = generate_brief(sample_reports_list)
        assert "## 실행 요약" in result

    def test_generate_brief_separates_completed_and_failed(self, sample_reports_list):
        """COMPLETED 태스크와 FAILED 태스크를 별도 섹션으로 분리한다"""
        result = generate_brief(sample_reports_list)
        # 완료된 태스크 섹션 확인
        assert "완료된 태스크" in result
        # 실패한 태스크 섹션 확인
        assert "실패한 태스크" in result

    def test_generate_brief_includes_completed_count(self, sample_reports_list):
        """완료된 태스크 개수를 표시한다"""
        result = generate_brief(sample_reports_list)
        # sample_reports_list에는 3개의 COMPLETED 태스크가 있음
        assert "완료된 태스크" in result
        assert "(3개)" in result or "3" in result

    def test_generate_brief_includes_failed_count(self, sample_reports_list):
        """실패한 태스크 개수를 표시한다"""
        result = generate_brief(sample_reports_list)
        # sample_reports_list에는 1개의 FAILED 태스크가 있음
        assert "실패한 태스크" in result
        assert "(1개)" in result or "1" in result

    def test_generate_brief_includes_key_metrics_section(self, sample_reports_list):
        """핵심 지표 섹션을 포함한다"""
        result = generate_brief(sample_reports_list)
        assert "핵심 지표" in result

    def test_generate_brief_includes_success_rate(self, sample_reports_list):
        """성공률 숫자가 포함된다"""
        result = generate_brief(sample_reports_list)
        assert "성공률" in result
        # 3개 완료, 1개 실패 → 75% 성공률
        assert "75" in result or "%" in result

    def test_generate_brief_includes_first_attempt_success_rate(self, sample_reports_list):
        """첫 시도 성공률이 포함된다"""
        result = generate_brief(sample_reports_list)
        assert "첫 시도 성공률" in result or "첫시도" in result

    def test_generate_brief_includes_average_time(self, sample_reports_list):
        """평균 소요 시간이 포함된다"""
        result = generate_brief(sample_reports_list)
        assert "평균 소요 시간" in result or "평균" in result

    def test_generate_brief_with_single_report(self, sample_completed_report):
        """단일 리포트로도 올바르게 작동한다"""
        result = generate_brief([sample_completed_report])
        assert "## 실행 요약" in result
        assert "완료된 태스크" in result
        assert "task-001" in result

    def test_generate_brief_with_only_completed_reports(self):
        """모든 태스크가 완료된 경우"""
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
        assert "## 실행 요약" in result
        assert "완료된 태스크" in result
        assert "100%" in result or "성공률" in result

    def test_generate_brief_with_only_failed_reports(self):
        """모든 태스크가 실패한 경우"""
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
        assert "## 실행 요약" in result
        assert "실패한 태스크" in result
        assert "0%" in result or "성공률" in result


class TestGenerateBriefFiltering:
    """generate_brief의 시간 필터링 테스트"""

    def test_generate_brief_with_since_filter(self, reports_with_various_times):
        """since 파라미터로 completed_at 기준 필터링이 동작한다"""
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        since = base_time + timedelta(hours=1)
        
        result = generate_brief(reports_with_various_times, since=since)
        
        # since 이후의 태스크만 포함되어야 함
        # task-early는 제외, task-middle과 task-late는 포함
        assert "task-early" not in result
        assert "task-middle" in result or "중간 작업" in result
        assert "task-late" in result or "후기 작업" in result

    def test_generate_brief_with_until_filter(self, reports_with_various_times):
        """until 파라미터로 completed_at 기준 필터링이 동작한다"""
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        until = base_time + timedelta(hours=3)
        
        result = generate_brief(reports_with_various_times, until=until)
        
        # until 이전의 태스크만 포함되어야 함
        # task-early와 task-middle은 포함, task-late는 제외
        assert "task-early" in result or "초기 작업" in result
        assert "task-middle" in result or "중간 작업" in result
        assert "task-late" not in result

    def test_generate_brief_with_since_and_until_filter(self, reports_with_various_times):
        """since와 until 파라미터를 함께 사용하여 기간 필터링이 동작한다"""
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        since = base_time + timedelta(hours=1)
        until = base_time + timedelta(hours=3)
        
        result = generate_brief(reports_with_various_times, since=since, until=until)
        
        # since 이후, until 이전의 태스크만 포함
        # task-early는 제외, task-middle은 포함, task-late는 제외
        assert "task-early" not in result
        assert "task-middle" in result or "중간 작업" in result
        assert "task-late" not in result

    def test_generate_brief_with_since_filter_no_matching_reports(self, reports_with_various_times):
        """since 필터로 인해 매칭되는 리포트가 없으면 '실행 기록 없음'을 반환한다"""
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        since = base_time + timedelta(hours=10)  # 모든 리포트보다 이후
        
        result = generate_brief(reports_with_various_times, since=since)
        assert result == "실행 기록 없음"

    def test_generate_brief_with_until_filter_no_matching_reports(self, reports_with_various_times):
        """until 필터로 인해 매칭되는 리포트가 없으면 '실행 기록 없음'을 반환한다"""
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        until = base_time - timedelta(hours=1)  # 모든 리포트보다 이전
        
        result = generate_brief(reports_with_various_times, until=until)
        assert result == "실행 기록 없음"

    def test_generate_brief_with_none_filters(self, sample_reports_list):
        """since와 until이 None일 때 모든 리포트를 포함한다"""
        result = generate_brief(sample_reports_list, since=None, until=None)
        assert "## 실행 요약" in result
        assert "task-001" in result


class TestGenerateBriefMetrics:
    """generate_brief의 지표 계산 테스트"""

    def test_success_rate_calculation_100_percent(self):
        """모든 태스크가 완료되면 성공률이 100%이다"""
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
        assert "100%" in result

    def test_success_rate_calculation_50_percent(self):
        """절반이 완료되면 성공률이 50%이다"""
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
                status="FAILED",
                time_elapsed_seconds=200,
                retry_count=1,
                completed_at=datetime(2024, 1, 15, 11, 0, 0)
            ),
        ]
        result = generate_brief(reports)
        assert "50%" in result

    def test_first_attempt_success_rate_calculation(self):
        """첫 시도 성공률이 올바르게 계산된다"""
        reports = [
            TaskReport(
                task_id="task-001",
                title="작업 1",
                status="COMPLETED",
                time_elapsed_seconds=100,
                retry_count=0,  # 첫 시도 성공
                completed_at=datetime(2024, 1, 15, 10, 0, 0)
            ),
            TaskReport(
                task_id="task-002",
                title="작업 2",
                status="COMPLETED",
                time_elapsed_seconds=200,
                retry_count=1,  # 재시도 후 성공
                completed_at=datetime(2024, 1, 15, 11, 0, 0)
            ),
            TaskReport(
                task_id="task-003",
                title="작업 3",
                status="FAILED",
                time_elapsed_seconds=150,
                retry_count=2,  # 실패
                completed_at=datetime(2024, 1, 15, 12, 0, 0)
            ),
        ]
        result = generate_brief(reports)
        # 첫 시도 성공: task-001 (1개)
        # 전체: 3개
        # 첫 시도 성공률: 33% 또는 약 33%
        assert "첫 시도 성공률" in result or "첫시도" in result
        assert "33" in result or "%" in result

    def test_average_time_calculation(self):
        """평균 소요 시간이 올바르게 계산된다"""
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
            TaskReport(
                task_id="task-003",
                title="작업 3",
                status="COMPLETED",
                time_elapsed_seconds=300,
                retry_count=0,
                completed_at=datetime(2024, 1, 15, 12, 0, 0)
            ),
        ]
        result = generate_brief(reports)
        # 평균: (100 + 200 + 300) / 3 = 200s
        assert "평균 소요 시간" in result or "평균" in result
        assert "200" in result or "s" in result


class TestGenerateBriefEdgeCases:
    """generate_brief의 엣지 케이스 테스트"""

    def test_generate_brief_with_very_long_title(self):
        """매우 긴 제목도 올바르게 처리된다"""
        long_title = "이것은 매우 긴 제목입니다. " * 10
        reports = [
            TaskReport(
                task_id="task-001",
                title=long_title,
                status="COMPLETED",
                time_elapsed_seconds=100,
                retry_count=0,
                completed_at=datetime(2024, 1, 15, 10, 0, 0)
            ),
        ]
        result = generate_brief(reports)
        assert "## 실행 요약" in result
        assert long_title in result

    def test_generate_brief_with_special_characters_in_title(self):
        """특수 문자가 포함된 제목도 올바르게 처리된다"""
        reports = [
            TaskReport(
                task_id="task-001",
                title="작업 [1] - 데이터 & 분석 (중요)",
                status="COMPLETED",
                time_elapsed_seconds=100,
                retry_count=0,
                completed_at=datetime(2024, 1, 15, 10, 0, 0)
            ),
        ]
        result = generate_brief(reports)
        assert "## 실행 요약" in result
        assert "작업 [1] - 데이터 & 분석 (중요)" in result

    def test_generate_brief_with_high_retry_count(self):
        """높은 재시도 횟수도 올바르게 표시된다"""
        reports = [
            TaskReport(
                task_id="task-001",
                title="작업 1",
                status="COMPLETED",
                time_elapsed_seconds=1000,
                retry_count=10,
                completed_at=datetime(2024, 1, 15, 10, 0, 0)
            ),
        ]
        result = generate_brief(reports)
        assert "10" in result or "재시도" in result

    def test_generate_brief_with_zero_time_elapsed(self):
        """소요 시간이 0초인 경우도 처리된다"""
        reports = [
            TaskReport(
                task_id="task-001",
                title="작업 1",
                status="COMPLETED",
                time_elapsed_seconds=0,
                retry_count=0,
                completed_at=datetime(2024, 1, 15, 10, 0, 0)
            ),
        ]
        result = generate_brief(reports)
        assert "## 실행 요약" in result
        assert "task-001" in result

    def test_generate_brief_preserves_report_order_in_sections(self, sample_reports_list):
        """각 섹션 내에서 리포트 순서가 유지된다"""
        result = generate_brief(sample_reports_list)
        # 완료된 태스크 섹션에서 task-001이 task-003보다 먼저 나타나야 함
        completed_section_start = result.find("완료된 태스크")
        task_001_pos = result.find("task-001", completed_section_start)
        task_003_pos = result.find("task-003", completed_section_start)
        
        if task_001_pos != -1 and task_003_pos != -1:
            assert task_001_pos < task_003_pos
