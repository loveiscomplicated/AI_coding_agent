"""
메트릭 수집기(metrics/collector.py) 테스트

수락 기준:
1. TaskReport 인스턴스를 save_report()로 저장하면 지정 디렉토리에 YAML 파일이 생성된다
2. load_reports()가 저장된 YAML 파일들을 TaskReport 리스트로 정확히 복원한다
3. aggregate([])는 total=0, success_rate=0, first_try_rate=0을 반환한다
4. aggregate()는 status=="COMPLETED"인 항목만 completed로 집계한다
5. aggregate()의 success_rate는 completed/total*100 (정수 반올림)이다
6. since 파라미터로 completed_at 기준 필터링이 동작한다
7. reviewer_verdict=="APPROVED"인 항목 수를 reviewer_approved로 반환한다
"""
import pytest
from datetime import datetime
from pathlib import Path
import yaml

from metrics.collector import TaskReport, save_report, load_reports, aggregate


class TestTaskReportDataclass:
    """TaskReport dataclass 테스트"""

    def test_task_report_creation(self, sample_task_report_data):
        """TaskReport 인스턴스 생성 가능"""
        report = TaskReport(**sample_task_report_data)
        
        assert report.task_id == "task-001"
        assert report.title == "메트릭 수집기 구현"
        assert report.status == "COMPLETED"
        assert report.completed_at == "2024-01-15T10:30:00"
        assert report.retry_count == 2
        assert report.test_count == 15
        assert report.test_pass_first_try is True
        assert report.reviewer_verdict == "APPROVED"
        assert report.time_elapsed_seconds == 3600.5
        assert report.failure_reasons == []
        assert report.reviewer_feedback == "좋은 구현입니다"

    def test_task_report_with_failure_reasons(self, sample_task_report_data_failed):
        """TaskReport에 failure_reasons 리스트 저장"""
        report = TaskReport(**sample_task_report_data_failed)
        
        assert report.failure_reasons == ["테스트 실패", "성능 이슈"]
        assert len(report.failure_reasons) == 2

    def test_task_report_with_none_completed_at(self, sample_task_report_data_pending):
        """TaskReport의 completed_at이 None일 수 있음"""
        report = TaskReport(**sample_task_report_data_pending)
        
        assert report.completed_at is None
        assert report.status == "IN_PROGRESS"


class TestSaveReport:
    """save_report() 함수 테스트"""

    def test_save_report_creates_yaml_file(self, sample_task_report_data, temp_reports_dir):
        """TaskReport를 YAML 파일로 저장"""
        report = TaskReport(**sample_task_report_data)
        
        result_path = save_report(report, reports_dir=temp_reports_dir)
        
        # 파일이 생성되었는지 확인
        assert result_path.exists()
        assert result_path.is_file()

    def test_save_report_filename_format(self, sample_task_report_data, temp_reports_dir):
        """저장된 파일명이 task-{task_id}.yaml 형식"""
        report = TaskReport(**sample_task_report_data)
        
        result_path = save_report(report, reports_dir=temp_reports_dir)
        
        assert result_path.name == "task-001.yaml"

    def test_save_report_returns_path(self, sample_task_report_data, temp_reports_dir):
        """save_report()가 Path 객체를 반환"""
        report = TaskReport(**sample_task_report_data)
        
        result_path = save_report(report, reports_dir=temp_reports_dir)
        
        assert isinstance(result_path, Path)
        assert str(result_path).endswith(".yaml")

    def test_save_report_creates_directory_if_not_exists(self, sample_task_report_data, temp_reports_dir):
        """reports_dir이 없으면 자동 생성"""
        nested_dir = Path(temp_reports_dir) / "nested" / "reports"
        report = TaskReport(**sample_task_report_data)
        
        result_path = save_report(report, reports_dir=str(nested_dir))
        
        assert nested_dir.exists()
        assert result_path.exists()

    def test_save_report_yaml_content_valid(self, sample_task_report_data, temp_reports_dir):
        """저장된 YAML 파일이 유효한 형식"""
        report = TaskReport(**sample_task_report_data)
        
        result_path = save_report(report, reports_dir=temp_reports_dir)
        
        # YAML 파일 읽기 가능 확인
        with open(result_path, 'r', encoding='utf-8') as f:
            loaded_data = yaml.safe_load(f)
        
        assert loaded_data is not None
        assert loaded_data['task_id'] == "task-001"

    def test_save_report_multiple_reports(self, sample_task_report_data, sample_task_report_data_failed, temp_reports_dir):
        """여러 TaskReport를 저장"""
        report1 = TaskReport(**sample_task_report_data)
        report2 = TaskReport(**sample_task_report_data_failed)
        
        path1 = save_report(report1, reports_dir=temp_reports_dir)
        path2 = save_report(report2, reports_dir=temp_reports_dir)
        
        assert path1.exists()
        assert path2.exists()
        assert path1.name == "task-001.yaml"
        assert path2.name == "task-002.yaml"


class TestLoadReports:
    """load_reports() 함수 테스트"""

    def test_load_reports_empty_directory(self, temp_reports_dir):
        """빈 디렉토리에서 빈 리스트 반환"""
        reports = load_reports(reports_dir=temp_reports_dir)
        
        assert reports == []
        assert isinstance(reports, list)

    def test_load_reports_single_file(self, sample_task_report_data, temp_reports_dir):
        """저장된 YAML 파일을 TaskReport로 복원"""
        report = TaskReport(**sample_task_report_data)
        save_report(report, reports_dir=temp_reports_dir)
        
        loaded_reports = load_reports(reports_dir=temp_reports_dir)
        
        assert len(loaded_reports) == 1
        assert loaded_reports[0].task_id == "task-001"
        assert loaded_reports[0].title == "메트릭 수집기 구현"
        assert loaded_reports[0].status == "COMPLETED"

    def test_load_reports_multiple_files(self, sample_task_report_data, sample_task_report_data_failed, temp_reports_dir):
        """여러 YAML 파일을 모두 로드"""
        report1 = TaskReport(**sample_task_report_data)
        report2 = TaskReport(**sample_task_report_data_failed)
        
        save_report(report1, reports_dir=temp_reports_dir)
        save_report(report2, reports_dir=temp_reports_dir)
        
        loaded_reports = load_reports(reports_dir=temp_reports_dir)
        
        assert len(loaded_reports) == 2
        task_ids = {r.task_id for r in loaded_reports}
        assert task_ids == {"task-001", "task-002"}

    def test_load_reports_preserves_all_fields(self, sample_task_report_data, temp_reports_dir):
        """로드된 TaskReport가 모든 필드를 정확히 복원"""
        original = TaskReport(**sample_task_report_data)
        save_report(original, reports_dir=temp_reports_dir)
        
        loaded_reports = load_reports(reports_dir=temp_reports_dir)
        loaded = loaded_reports[0]
        
        assert loaded.task_id == original.task_id
        assert loaded.title == original.title
        assert loaded.status == original.status
        assert loaded.completed_at == original.completed_at
        assert loaded.retry_count == original.retry_count
        assert loaded.test_count == original.test_count
        assert loaded.test_pass_first_try == original.test_pass_first_try
        assert loaded.reviewer_verdict == original.reviewer_verdict
        assert loaded.time_elapsed_seconds == original.time_elapsed_seconds
        assert loaded.failure_reasons == original.failure_reasons
        assert loaded.reviewer_feedback == original.reviewer_feedback

    def test_load_reports_with_failure_reasons(self, sample_task_report_data_failed, temp_reports_dir):
        """failure_reasons 리스트가 정확히 복원"""
        original = TaskReport(**sample_task_report_data_failed)
        save_report(original, reports_dir=temp_reports_dir)
        
        loaded_reports = load_reports(reports_dir=temp_reports_dir)
        loaded = loaded_reports[0]
        
        assert loaded.failure_reasons == ["테스트 실패", "성능 이슈"]

    def test_load_reports_with_none_completed_at(self, sample_task_report_data_pending, temp_reports_dir):
        """completed_at이 None인 경우 정확히 복원"""
        original = TaskReport(**sample_task_report_data_pending)
        save_report(original, reports_dir=temp_reports_dir)
        
        loaded_reports = load_reports(reports_dir=temp_reports_dir)
        loaded = loaded_reports[0]
        
        assert loaded.completed_at is None

    def test_load_reports_accepts_orchestrator_nested_schema(self, temp_reports_dir):
        """중첩 스키마(metrics/pipeline_result) YAML도 TaskReport로 어댑팅한다"""
        nested = {
            "task_id": "task-nested",
            "title": "nested schema",
            "status": "COMPLETED",
            "completed_at": "2024-01-20T09:00:00",
            "metrics": {
                "retry_count": 3,
                "test_count": 12,
                "test_pass_first_try": False,
                "reviewer_verdict": "APPROVED",
                "time_elapsed_seconds": 123.4,
                "failure_reasons": [],
            },
            "pipeline_result": {
                "reviewer_feedback": "ok",
            },
        }
        path = Path(temp_reports_dir) / "task-nested.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(nested, f, allow_unicode=True, sort_keys=False)

        loaded_reports = load_reports(reports_dir=temp_reports_dir)
        assert len(loaded_reports) == 1
        loaded = loaded_reports[0]
        assert loaded.task_id == "task-nested"
        assert loaded.retry_count == 3
        assert loaded.time_elapsed_seconds == 123.4
        assert loaded.reviewer_feedback == "ok"


class TestLoadReportsSinceFilter:
    """load_reports()의 since 파라미터 필터링 테스트"""

    def test_load_reports_since_filters_by_completed_at(self, sample_task_report_data, sample_task_report_data_failed, temp_reports_dir):
        """since 파라미터로 completed_at 기준 필터링"""
        report1 = TaskReport(**sample_task_report_data)  # 2024-01-15
        report2 = TaskReport(**sample_task_report_data_failed)  # 2024-01-16
        
        save_report(report1, reports_dir=temp_reports_dir)
        save_report(report2, reports_dir=temp_reports_dir)
        
        # 2024-01-16 이후만 로드
        since = datetime(2024, 1, 16, 0, 0, 0)
        loaded_reports = load_reports(reports_dir=temp_reports_dir, since=since)
        
        assert len(loaded_reports) == 1
        assert loaded_reports[0].task_id == "task-002"

    def test_load_reports_since_includes_exact_datetime(self, sample_task_report_data, temp_reports_dir):
        """since와 정확히 같은 시간의 report도 포함"""
        report = TaskReport(**sample_task_report_data)  # 2024-01-15T10:30:00
        save_report(report, reports_dir=temp_reports_dir)
        
        since = datetime(2024, 1, 15, 10, 30, 0)
        loaded_reports = load_reports(reports_dir=temp_reports_dir, since=since)
        
        assert len(loaded_reports) == 1

    def test_load_reports_since_excludes_before_datetime(self, sample_task_report_data, temp_reports_dir):
        """since보다 이전의 report는 제외"""
        report = TaskReport(**sample_task_report_data)  # 2024-01-15T10:30:00
        save_report(report, reports_dir=temp_reports_dir)
        
        since = datetime(2024, 1, 15, 10, 30, 1)  # 1초 뒤
        loaded_reports = load_reports(reports_dir=temp_reports_dir, since=since)
        
        assert len(loaded_reports) == 0

    def test_load_reports_since_none_returns_all(self, sample_task_report_data, sample_task_report_data_failed, temp_reports_dir):
        """since=None이면 모든 report 반환"""
        report1 = TaskReport(**sample_task_report_data)
        report2 = TaskReport(**sample_task_report_data_failed)
        
        save_report(report1, reports_dir=temp_reports_dir)
        save_report(report2, reports_dir=temp_reports_dir)
        
        loaded_reports = load_reports(reports_dir=temp_reports_dir, since=None)
        
        assert len(loaded_reports) == 2


class TestAggregate:
    """aggregate() 함수 테스트"""

    def test_aggregate_empty_list(self):
        """aggregate([])는 total=0, success_rate=0, first_try_rate=0"""
        result = aggregate([])
        
        assert result['total'] == 0
        assert result['success_rate'] == 0
        assert result['first_try_rate'] == 0

    def test_aggregate_empty_list_has_required_keys(self):
        """aggregate([])가 모든 필수 키를 반환"""
        result = aggregate([])
        
        required_keys = {
            'total', 'completed', 'failed', 'success_rate',
            'first_try_rate', 'avg_elapsed_seconds', 'total_retries',
            'reviewer_approved'
        }
        assert set(result.keys()) == required_keys

    def test_aggregate_single_completed_report(self, sample_task_report_data):
        """완료된 report 1개 집계"""
        report = TaskReport(**sample_task_report_data)
        
        result = aggregate([report])
        
        assert result['total'] == 1
        assert result['completed'] == 1
        assert result['failed'] == 0

    def test_aggregate_counts_only_completed_status(self, sample_task_report_data, sample_task_report_data_failed, sample_task_report_data_pending):
        """status=="COMPLETED"인 항목만 completed로 집계"""
        report1 = TaskReport(**sample_task_report_data)  # COMPLETED
        report2 = TaskReport(**sample_task_report_data_failed)  # FAILED
        report3 = TaskReport(**sample_task_report_data_pending)  # IN_PROGRESS
        
        result = aggregate([report1, report2, report3])
        
        assert result['total'] == 3
        assert result['completed'] == 1  # COMPLETED만 카운트
        assert result['failed'] == 1  # FAILED 카운트

    def test_aggregate_success_rate_calculation(self, sample_task_report_data, sample_task_report_data_failed):
        """success_rate = completed/total*100 (정수 반올림)"""
        report1 = TaskReport(**sample_task_report_data)  # COMPLETED
        report2 = TaskReport(**sample_task_report_data_failed)  # FAILED
        
        result = aggregate([report1, report2])
        
        # completed=1, total=2 → 1/2*100 = 50
        assert result['success_rate'] == 50

    def test_aggregate_success_rate_rounding(self):
        """success_rate가 정수로 반올림"""
        data1 = {
            "task_id": "task-1",
            "title": "Task 1",
            "status": "COMPLETED",
            "completed_at": "2024-01-15T10:00:00",
            "retry_count": 0,
            "test_count": 10,
            "test_pass_first_try": True,
            "reviewer_verdict": "APPROVED",
            "time_elapsed_seconds": 1000.0,
            "failure_reasons": [],
            "reviewer_feedback": ""
        }
        data2 = {
            "task_id": "task-2",
            "title": "Task 2",
            "status": "FAILED",
            "completed_at": "2024-01-15T11:00:00",
            "retry_count": 1,
            "test_count": 5,
            "test_pass_first_try": False,
            "reviewer_verdict": "REJECTED",
            "time_elapsed_seconds": 500.0,
            "failure_reasons": ["error"],
            "reviewer_feedback": ""
        }
        data3 = {
            "task_id": "task-3",
            "title": "Task 3",
            "status": "FAILED",
            "completed_at": "2024-01-15T12:00:00",
            "retry_count": 2,
            "test_count": 8,
            "test_pass_first_try": False,
            "reviewer_verdict": "REJECTED",
            "time_elapsed_seconds": 800.0,
            "failure_reasons": ["error"],
            "reviewer_feedback": ""
        }
        
        reports = [TaskReport(**data1), TaskReport(**data2), TaskReport(**data3)]
        result = aggregate(reports)
        
        # completed=1, total=3 → 1/3*100 = 33.333... → 33 (반올림)
        assert result['success_rate'] == 33

    def test_aggregate_first_try_rate(self, sample_task_report_data, sample_task_report_data_failed):
        """first_try_rate = test_pass_first_try인 항목 수 / total * 100"""
        report1 = TaskReport(**sample_task_report_data)  # test_pass_first_try=True
        report2 = TaskReport(**sample_task_report_data_failed)  # test_pass_first_try=False
        
        result = aggregate([report1, report2])
        
        # first_try=1, total=2 → 1/2*100 = 50
        assert result['first_try_rate'] == 50

    def test_aggregate_avg_elapsed_seconds(self, sample_task_report_data, sample_task_report_data_failed):
        """avg_elapsed_seconds 계산"""
        report1 = TaskReport(**sample_task_report_data)  # 3600.5
        report2 = TaskReport(**sample_task_report_data_failed)  # 7200.0
        
        result = aggregate([report1, report2])
        
        # (3600.5 + 7200.0) / 2 = 5400.25
        assert result['avg_elapsed_seconds'] == 5400.25

    def test_aggregate_total_retries(self, sample_task_report_data, sample_task_report_data_failed):
        """total_retries = 모든 retry_count의 합"""
        report1 = TaskReport(**sample_task_report_data)  # retry_count=2
        report2 = TaskReport(**sample_task_report_data_failed)  # retry_count=5
        
        result = aggregate([report1, report2])
        
        assert result['total_retries'] == 7

    def test_aggregate_reviewer_approved_count(self, sample_task_report_data, sample_task_report_data_failed):
        """reviewer_approved = reviewer_verdict=="APPROVED"인 항목 수"""
        report1 = TaskReport(**sample_task_report_data)  # APPROVED
        report2 = TaskReport(**sample_task_report_data_failed)  # REJECTED
        
        result = aggregate([report1, report2])
        
        assert result['reviewer_approved'] == 1

    def test_aggregate_reviewer_approved_multiple(self):
        """여러 APPROVED 항목 집계"""
        data1 = {
            "task_id": "task-1",
            "title": "Task 1",
            "status": "COMPLETED",
            "completed_at": "2024-01-15T10:00:00",
            "retry_count": 0,
            "test_count": 10,
            "test_pass_first_try": True,
            "reviewer_verdict": "APPROVED",
            "time_elapsed_seconds": 1000.0,
            "failure_reasons": [],
            "reviewer_feedback": ""
        }
        data2 = {
            "task_id": "task-2",
            "title": "Task 2",
            "status": "COMPLETED",
            "completed_at": "2024-01-15T11:00:00",
            "retry_count": 1,
            "test_count": 5,
            "test_pass_first_try": True,
            "reviewer_verdict": "APPROVED",
            "time_elapsed_seconds": 500.0,
            "failure_reasons": [],
            "reviewer_feedback": ""
        }
        data3 = {
            "task_id": "task-3",
            "title": "Task 3",
            "status": "COMPLETED",
            "completed_at": "2024-01-15T12:00:00",
            "retry_count": 2,
            "test_count": 8,
            "test_pass_first_try": False,
            "reviewer_verdict": "PENDING",
            "time_elapsed_seconds": 800.0,
            "failure_reasons": [],
            "reviewer_feedback": ""
        }
        
        reports = [TaskReport(**data1), TaskReport(**data2), TaskReport(**data3)]
        result = aggregate(reports)
        
        assert result['reviewer_approved'] == 2

    def test_aggregate_with_pending_verdict(self, sample_task_report_data_pending):
        """PENDING verdict는 APPROVED로 카운트되지 않음"""
        report = TaskReport(**sample_task_report_data_pending)
        
        result = aggregate([report])
        
        assert result['reviewer_approved'] == 0

    def test_aggregate_complex_scenario(self, sample_task_report_data, sample_task_report_data_failed, sample_task_report_data_pending):
        """복합 시나리오: 여러 상태의 report 집계"""
        report1 = TaskReport(**sample_task_report_data)  # COMPLETED, APPROVED
        report2 = TaskReport(**sample_task_report_data_failed)  # FAILED, REJECTED
        report3 = TaskReport(**sample_task_report_data_pending)  # IN_PROGRESS, PENDING
        
        result = aggregate([report1, report2, report3])
        
        assert result['total'] == 3
        assert result['completed'] == 1
        assert result['failed'] == 1
        assert result['success_rate'] == 33  # 1/3*100 = 33.333... → 33
        assert result['first_try_rate'] == 67  # 2/3*100 = 66.666... → 67
        assert result['avg_elapsed_seconds'] == (3600.5 + 7200.0 + 1800.0) / 3
        assert result['total_retries'] == 8  # 2 + 5 + 1
        assert result['reviewer_approved'] == 1

    def test_aggregate_all_completed(self):
        """모든 report가 COMPLETED인 경우"""
        data1 = {
            "task_id": "task-1",
            "title": "Task 1",
            "status": "COMPLETED",
            "completed_at": "2024-01-15T10:00:00",
            "retry_count": 0,
            "test_count": 10,
            "test_pass_first_try": True,
            "reviewer_verdict": "APPROVED",
            "time_elapsed_seconds": 1000.0,
            "failure_reasons": [],
            "reviewer_feedback": ""
        }
        data2 = {
            "task_id": "task-2",
            "title": "Task 2",
            "status": "COMPLETED",
            "completed_at": "2024-01-15T11:00:00",
            "retry_count": 1,
            "test_count": 5,
            "test_pass_first_try": True,
            "reviewer_verdict": "APPROVED",
            "time_elapsed_seconds": 500.0,
            "failure_reasons": [],
            "reviewer_feedback": ""
        }
        
        reports = [TaskReport(**data1), TaskReport(**data2)]
        result = aggregate(reports)
        
        assert result['total'] == 2
        assert result['completed'] == 2
        assert result['failed'] == 0
        assert result['success_rate'] == 100
        assert result['first_try_rate'] == 100
        assert result['reviewer_approved'] == 2

    def test_aggregate_all_failed(self):
        """모든 report가 FAILED인 경우"""
        data1 = {
            "task_id": "task-1",
            "title": "Task 1",
            "status": "FAILED",
            "completed_at": "2024-01-15T10:00:00",
            "retry_count": 3,
            "test_count": 10,
            "test_pass_first_try": False,
            "reviewer_verdict": "REJECTED",
            "time_elapsed_seconds": 1000.0,
            "failure_reasons": ["error"],
            "reviewer_feedback": ""
        }
        data2 = {
            "task_id": "task-2",
            "title": "Task 2",
            "status": "FAILED",
            "completed_at": "2024-01-15T11:00:00",
            "retry_count": 2,
            "test_count": 5,
            "test_pass_first_try": False,
            "reviewer_verdict": "REJECTED",
            "time_elapsed_seconds": 500.0,
            "failure_reasons": ["error"],
            "reviewer_feedback": ""
        }
        
        reports = [TaskReport(**data1), TaskReport(**data2)]
        result = aggregate(reports)
        
        assert result['total'] == 2
        assert result['completed'] == 0
        assert result['failed'] == 2
        assert result['success_rate'] == 0
        assert result['first_try_rate'] == 0
        assert result['reviewer_approved'] == 0
