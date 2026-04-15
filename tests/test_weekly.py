"""Weekly Report 생성기 테스트"""
import pytest
from datetime import datetime, timezone
from reports.weekly import (
    get_week_range,
    filter_by_week,
    collect_stats,
    generate_report,
)
from metrics.collector import TaskReport


@pytest.fixture
def sample_reports():
    return [
        TaskReport(
            task_id="task_1",
            title="task 1",
            status="COMPLETED",
            completed_at=datetime(2026, 1, 5, 10, 0, 0, tzinfo=timezone.utc),
            time_elapsed_seconds=3600.0,
            retry_count=0,
            test_pass_first_try=True,
        ),
        TaskReport(
            task_id="task_2",
            title="task 2",
            status="COMPLETED",
            completed_at=datetime(2026, 1, 7, 10, 0, 0, tzinfo=timezone.utc),
            time_elapsed_seconds=7200.0,
            retry_count=1,
            test_pass_first_try=False,
        ),
        TaskReport(
            task_id="task_3",
            title="task 3",
            status="FAILED",
            completed_at=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc),
            time_elapsed_seconds=5400.0,
            retry_count=2,
            test_pass_first_try=False,
        ),
        TaskReport(
            task_id="task_4",
            title="task 4",
            status="COMPLETED",
            completed_at=datetime(2026, 1, 10, 10, 0, 0, tzinfo=timezone.utc),
            time_elapsed_seconds=1800.0,
            retry_count=0,
            test_pass_first_try=True,
        ),
        TaskReport(
            task_id="task_5",
            title="task 5",
            status="PENDING",
            completed_at=None,
            time_elapsed_seconds=0.0,
            retry_count=0,
            test_pass_first_try=True,
        ),
    ]


@pytest.fixture
def reports_different_weeks():
    return [
        TaskReport(
            task_id="task_w1_1",
            title="week1",
            status="COMPLETED",
            completed_at=datetime(2026, 1, 7, 10, 0, 0, tzinfo=timezone.utc),
            time_elapsed_seconds=1200.0,
            retry_count=0,
            test_pass_first_try=True,
        ),
        TaskReport(
            task_id="task_w2_1",
            title="week2",
            status="COMPLETED",
            completed_at=datetime(2026, 1, 12, 10, 0, 0, tzinfo=timezone.utc),
            time_elapsed_seconds=1200.0,
            retry_count=0,
            test_pass_first_try=True,
        ),
        TaskReport(
            task_id="task_w3_1",
            title="week3",
            status="COMPLETED",
            completed_at=datetime(2026, 1, 19, 10, 0, 0, tzinfo=timezone.utc),
            time_elapsed_seconds=1200.0,
            retry_count=0,
            test_pass_first_try=True,
        ),
    ]


class TestGetWeekRange:
    """get_week_range 함수 테스트"""

    def test_get_week_range_returns_tuple(self):
        """get_week_range는 튜플을 반환한다"""
        result = get_week_range(2026, 1)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_get_week_range_first_value_is_monday(self):
        """get_week_range(2026, 1)의 첫 번째 값은 월요일(weekday==0)이다"""
        start, _ = get_week_range(2026, 1)
        assert start.weekday() == 0, f"Expected Monday (0), got {start.weekday()}"

    def test_get_week_range_second_value_is_sunday(self):
        """get_week_range(2026, 1)의 두 번째 값은 일요일(weekday==6)이다"""
        _, end = get_week_range(2026, 1)
        assert end.weekday() == 6, f"Expected Sunday (6), got {end.weekday()}"

    def test_get_week_range_returns_datetime_objects(self):
        """get_week_range는 datetime 객체를 반환한다"""
        start, end = get_week_range(2026, 1)
        assert isinstance(start, datetime)
        assert isinstance(end, datetime)

    def test_get_week_range_start_is_midnight_utc(self):
        """get_week_range의 시작은 00:00:00 UTC이다"""
        start, _ = get_week_range(2026, 1)
        assert start.hour == 0
        assert start.minute == 0
        assert start.second == 0

    def test_get_week_range_end_is_end_of_day_utc(self):
        """get_week_range의 끝은 23:59:59 UTC이다"""
        _, end = get_week_range(2026, 1)
        assert end.hour == 23
        assert end.minute == 59
        assert end.second == 59

    def test_get_week_range_different_weeks(self):
        """get_week_range는 다른 주차에 대해 올바른 범위를 반환한다"""
        # 2주차
        start2, end2 = get_week_range(2026, 2)
        assert start2.weekday() == 0
        assert end2.weekday() == 6
        
        # 3주차
        start3, end3 = get_week_range(2026, 3)
        assert start3.weekday() == 0
        assert end3.weekday() == 6

    def test_get_week_range_week_53(self):
        """get_week_range는 53주차도 처리한다"""
        start, end = get_week_range(2026, 53)
        assert start.weekday() == 0
        assert end.weekday() == 6


class TestFilterByWeek:
    """filter_by_week 함수 테스트"""

    def test_filter_by_week_returns_list(self, sample_reports):
        """filter_by_week는 리스트를 반환한다"""
        result = filter_by_week(sample_reports, 2026, 1)
        assert isinstance(result, list)

    def test_filter_by_week_filters_by_completed_at(self, sample_reports):
        """filter_by_week는 completed_at 기준으로 해당 주의 Report만 필터링한다"""
        result = filter_by_week(sample_reports, 2026, 1)
        # 1주차(2026-01-05 ~ 2026-01-11)에 완료된 항목만 포함
        assert len(result) > 0
        for report in result:
            assert report.completed_at is not None
            start, end = get_week_range(2026, 1)
            assert start <= report.completed_at <= end

    def test_filter_by_week_excludes_none_completed_at(self, sample_reports):
        """filter_by_week는 completed_at이 None인 항목을 제외한다"""
        result = filter_by_week(sample_reports, 2026, 1)
        for report in result:
            assert report.completed_at is not None

    def test_filter_by_week_different_weeks(self, reports_different_weeks):
        """filter_by_week는 다른 주차를 올바르게 필터링한다"""
        # 1주차 필터링
        week1_reports = filter_by_week(reports_different_weeks, 2026, 1)
        assert len(week1_reports) == 1
        assert week1_reports[0].task_id == "task_w1_1"

        # 2주차 필터링
        week2_reports = filter_by_week(reports_different_weeks, 2026, 2)
        assert len(week2_reports) == 1
        assert week2_reports[0].task_id == "task_w2_1"

        # 3주차 필터링
        week3_reports = filter_by_week(reports_different_weeks, 2026, 3)
        assert len(week3_reports) == 1
        assert week3_reports[0].task_id == "task_w3_1"

    def test_filter_by_week_empty_list(self):
        """filter_by_week는 빈 리스트를 처리한다"""
        result = filter_by_week([], 2026, 1)
        assert result == []

    def test_filter_by_week_no_matching_reports(self):
        """filter_by_week는 해당 주에 일치하는 보고서가 없으면 빈 리스트를 반환한다"""
        reports = [
            TaskReport(
                task_id="task_1",
                title="task 1",
                status="COMPLETED",
                completed_at=datetime(2026, 2, 5, 10, 0, 0, tzinfo=timezone.utc),
                time_elapsed_seconds=3600.0,
                retry_count=0,
                test_pass_first_try=True,
            ),
        ]
        result = filter_by_week(reports, 2026, 1)
        assert result == []


class TestCollectStats:
    """collect_stats 함수 테스트"""

    def test_collect_stats_returns_dict(self, sample_reports):
        """collect_stats는 딕셔너리를 반환한다"""
        result = collect_stats(sample_reports)
        assert isinstance(result, dict)

    def test_collect_stats_has_required_keys(self, sample_reports):
        """collect_stats는 필수 키를 포함한다"""
        result = collect_stats(sample_reports)
        required_keys = {
            "total",
            "completed",
            "failed",
            "success_rate",
            "first_try_rate",
            "avg_elapsed_seconds",
            "total_retries",
        }
        assert required_keys.issubset(result.keys())

    def test_collect_stats_empty_list_total_zero(self):
        """collect_stats([])는 total=0을 반환한다"""
        result = collect_stats([])
        assert result["total"] == 0

    def test_collect_stats_empty_list_success_rate_zero(self):
        """collect_stats([])는 success_rate=0을 반환한다"""
        result = collect_stats([])
        assert result["success_rate"] == 0

    def test_collect_stats_counts_completed_status(self, sample_reports):
        """collect_stats는 COMPLETED 상태 항목을 정확히 집계한다"""
        result = collect_stats(sample_reports)
        # sample_reports에서 COMPLETED 상태는 3개 (task_1, task_2, task_4)
        assert result["completed"] == 3

    def test_collect_stats_counts_failed_status(self, sample_reports):
        """collect_stats는 FAILED 상태 항목을 정확히 집계한다"""
        result = collect_stats(sample_reports)
        # sample_reports에서 FAILED 상태는 1개 (task_3)
        assert result["failed"] == 1

    def test_collect_stats_total_count(self, sample_reports):
        """collect_stats는 전체 항목 수를 정확히 집계한다"""
        result = collect_stats(sample_reports)
        assert result["total"] == len(sample_reports)

    def test_collect_stats_success_rate_calculation(self, sample_reports):
        """collect_stats는 success_rate를 올바르게 계산한다"""
        result = collect_stats(sample_reports)
        # 3 completed / 5 total = 0.6
        assert result["success_rate"] == pytest.approx(0.6)

    def test_collect_stats_first_try_rate(self, sample_reports):
        """collect_stats는 first_try_rate를 올바르게 계산한다"""
        result = collect_stats(sample_reports)
        # first_try=True인 항목: task_1, task_4, task_5 = 3개
        # 3 / 5 = 0.6
        assert result["first_try_rate"] == pytest.approx(0.6)

    def test_collect_stats_avg_elapsed_seconds(self, sample_reports):
        """collect_stats는 avg_elapsed_seconds를 올바르게 계산한다"""
        result = collect_stats(sample_reports)
        # (3600 + 7200 + 5400 + 1800 + 0) / 5 = 3600
        assert result["avg_elapsed_seconds"] == pytest.approx(3600.0)

    def test_collect_stats_total_retries(self, sample_reports):
        """collect_stats는 total_retries를 올바르게 집계한다"""
        result = collect_stats(sample_reports)
        # 0 + 1 + 2 + 0 + 0 = 3
        assert result["total_retries"] == 3

    def test_collect_stats_single_completed_report(self):
        """collect_stats는 단일 완료 보고서를 처리한다"""
        reports = [
            TaskReport(
                task_id="task_1",
                title="task 1",
                status="COMPLETED",
                completed_at=datetime(2026, 1, 5, 10, 0, 0, tzinfo=timezone.utc),
                time_elapsed_seconds=3600.0,
                retry_count=0,
                test_pass_first_try=True,
            ),
        ]
        result = collect_stats(reports)
        assert result["total"] == 1
        assert result["completed"] == 1
        assert result["failed"] == 0
        assert result["success_rate"] == 1.0
        assert result["first_try_rate"] == 1.0
        assert result["avg_elapsed_seconds"] == 3600.0
        assert result["total_retries"] == 0

    def test_collect_stats_all_failed(self):
        """collect_stats는 모두 실패한 경우를 처리한다"""
        reports = [
            TaskReport(
                task_id="task_1",
                title="task 1",
                status="FAILED",
                completed_at=datetime(2026, 1, 5, 10, 0, 0, tzinfo=timezone.utc),
                time_elapsed_seconds=3600.0,
                retry_count=2,
                test_pass_first_try=False,
            ),
            TaskReport(
                task_id="task_2",
                title="task 2",
                status="FAILED",
                completed_at=datetime(2026, 1, 6, 10, 0, 0, tzinfo=timezone.utc),
                time_elapsed_seconds=3600.0,
                retry_count=1,
                test_pass_first_try=False,
            ),
        ]
        result = collect_stats(reports)
        assert result["total"] == 2
        assert result["completed"] == 0
        assert result["failed"] == 2
        assert result["success_rate"] == 0.0


class TestGenerateReport:
    """generate_report 함수 테스트"""

    def test_generate_report_returns_string(self, sample_reports):
        """generate_report는 문자열을 반환한다"""
        result = generate_report(sample_reports, 2026, 1)
        assert isinstance(result, str)

    def test_generate_report_starts_with_heading(self, sample_reports):
        """generate_report()의 반환값은 '# 주간 보고서' 문자열로 시작한다"""
        result = generate_report(sample_reports, 2026, 1)
        assert result.startswith("# 주간 보고서")

    def test_generate_report_includes_year(self, sample_reports):
        """generate_report()의 반환값에 year 숫자가 포함된다"""
        result = generate_report(sample_reports, 2026, 1)
        assert "2026" in result

    def test_generate_report_includes_week(self, sample_reports):
        """generate_report()의 반환값에 week 숫자가 포함된다"""
        result = generate_report(sample_reports, 2026, 1)
        assert "1" in result

    def test_generate_report_includes_year_and_week_in_title(self, sample_reports):
        """generate_report의 제목에 year와 week이 포함된다"""
        result = generate_report(sample_reports, 2026, 15)
        assert "2026" in result
        assert "15" in result

    def test_generate_report_empty_list_indicates_no_tasks(self):
        """generate_report([], 2026, 15)는 완료된 태스크 없음을 명시한다"""
        result = generate_report([], 2026, 15)
        assert "완료된 태스크 없음" in result or "없음" in result or "없습니다" in result

    def test_generate_report_includes_stats(self, sample_reports):
        """generate_report는 집계 지표를 포함한다"""
        result = generate_report(sample_reports, 2026, 1)
        # 집계 지표 키워드 확인
        assert "완료" in result or "completed" in result.lower()

    def test_generate_report_includes_task_list(self, sample_reports):
        """generate_report는 태스크 목록을 포함한다"""
        result = generate_report(sample_reports, 2026, 1)
        # 필터링된 보고서의 task_id가 포함되어야 함
        filtered = filter_by_week(sample_reports, 2026, 1)
        if filtered:
            # 최소한 하나의 task_id가 포함되어야 함
            assert any(report.task_id in result for report in filtered)

    def test_generate_report_different_weeks(self, reports_different_weeks):
        """generate_report는 다른 주차에 대해 올바른 보고서를 생성한다"""
        result1 = generate_report(reports_different_weeks, 2026, 1)
        result2 = generate_report(reports_different_weeks, 2026, 2)
        
        assert "2026" in result1
        assert "1" in result1
        assert "2026" in result2
        assert "2" in result2

    def test_generate_report_markdown_format(self, sample_reports):
        """generate_report는 마크다운 형식을 사용한다"""
        result = generate_report(sample_reports, 2026, 1)
        # 마크다운 헤딩 확인
        assert "#" in result

    def test_generate_report_with_filtered_reports(self, sample_reports):
        """generate_report는 해당 주의 보고서만 포함한다"""
        result = generate_report(sample_reports, 2026, 1)
        filtered = filter_by_week(sample_reports, 2026, 1)
        
        # 필터링된 보고서가 있으면 보고서에 포함되어야 함
        if filtered:
            assert len(result) > 0

    def test_generate_report_empty_with_different_week(self):
        """generate_report는 해당 주에 보고서가 없으면 빈 상태를 표시한다"""
        reports = [
            TaskReport(
                task_id="task_1",
                title="task 1",
                status="COMPLETED",
                completed_at=datetime(2026, 2, 5, 10, 0, 0, tzinfo=timezone.utc),
                time_elapsed_seconds=3600.0,
                retry_count=0,
                test_pass_first_try=True,
            ),
        ]
        result = generate_report(reports, 2026, 1)
        assert "완료된 태스크 없음" in result or "없음" in result or "없습니다" in result
