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
from pathlib import Path
from datetime import datetime
import yaml

from metrics.collector import TaskReport, save_report, load_reports, aggregate


class TestTaskReportDataclass:
    """TaskReport dataclass 테스트"""

    def test_task_report_creation(self, sample_report_data):
        """TaskReport 인스턴스 생성 가능"""
        report = TaskReport(**sample_report_data)
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
        assert report.reviewer_feedback == "좋은 구현입니다."

    def test_task_report_with_none_values(self, sample_report_data_in_progress):
        """TaskReport는 None 값을 허용"""
        report = TaskReport(**sample_report_data_in_progress)
        assert report.completed_at is None
        assert report.reviewer_feedback is None

    def test_task_report_with_failure_reasons(self, sample_report_data_failed):
        """TaskReport는 failure_reasons 리스트를 저장"""
        report = TaskReport(**sample_report_data_failed)
        assert report.failure_reasons == ["테스트 실패", "성능 미달"]
        assert len(report.failure_reasons) == 2


class TestSaveReport:
    """save_report() 함수 테스트"""

    def test_save_report_creates_yaml_file(self, sample_report_data, temp_reports_dir):
        """수락 기준 1: TaskReport를 save_report()로 저장하면 YAML 파일이 생성된다"""
        report = TaskReport(**sample_report_data)
        result_path = save_report(report, temp_reports_dir)

        # 파일이 생성되었는지 확인
        assert result_path.exists()
        assert result_path.is_file()
        assert result_path.suffix == ".yaml"

    def test_save_report_filename_format(self, sample_report_data, temp_reports_dir):
        """save_report()는 task-{task_id}.yaml 형식으로 저장"""
        report = TaskReport(**sample_report_data)
        result_path = save_report(report, temp_reports_dir)

        expected_filename = f"task-{sample_report_data['task_id']}.yaml"
        assert result_path.name == expected_filename

    def test_save_report_returns_path(self, sample_report_data, temp_reports_dir):
        """save_report()는 저장된 파일의 Path를 반환"""
        report = TaskReport(**sample_report_data)
        result_path = save_report(report, temp_reports_dir)

        assert isinstance(result_path, Path)
        assert str(temp_reports_dir) in str(result_path)

    def test_save_report_creates_directory(self, sample_report_data, temp_reports_dir):
        """save_report()는 디렉토리가 없으면 생성"""
        nested_dir = Path(temp_reports_dir) / "nested" / "reports"
        report = TaskReport(**sample_report_data)
        result_path = save_report(report, str(nested_dir))

        assert nested_dir.exists()
        assert result_path.exists()

    def test_save_report_yaml_content(self, sample_report_data, temp_reports_dir):
        """저장된 YAML 파일의 내용이 올바름"""
        report = TaskReport(**sample_report_data)
        result_path = save_report(report, temp_reports_dir)

        with open(result_path, "r", encoding="utf-8") as f:
            loaded_data = yaml.safe_load(f)

        assert loaded_data["task_id"] == sample_report_data["task_id"]
        assert loaded_data["title"] == sample_report_data["title"]
        assert loaded_data["status"] == sample_report_data["status"]
        assert loaded_data["retry_count"] == sample_report_data["retry_count"]

    def test_save_report_overwrites_existing(self, sample_report_data, temp_reports_dir):
        """같은 task_id로 저장하면 기존 파일을 덮어씀"""
        report = TaskReport(**sample_report_data)
        path1 = save_report(report, temp_reports_dir)

        # 다른 데이터로 같은 task_id 저장
        modified_data = sample_report_data.copy()
        modified_data["title"] = "수정된 제목"
        report2 = TaskReport(**modified_data)
        path2 = save_report(report2, temp_reports_dir)

        assert path1 == path2
        with open(path2, "r", encoding="utf-8") as f:
            loaded_data = yaml.safe_load(f)
        assert loaded_data["title"] == "수정된 제목"


class TestLoadReports:
    """load_reports() 함수 테스트"""

    def test_load_reports_empty_directory(self, temp_reports_dir):
        """빈 디렉토리에서 load_reports()는 빈 리스트 반환"""
        reports = load_reports(temp_reports_dir)
        assert reports == []

    def test_load_reports_single_file(self, sample_report_data, temp_reports_dir):
        """수락 기준 2: 저장된 YAML을 TaskReport로 정확히 복원"""
        report = TaskReport(**sample_report_data)
        save_report(report, temp_reports_dir)

        loaded_reports = load_reports(temp_reports_dir)
        assert len(loaded_reports) == 1
        assert loaded_reports[0].task_id == sample_report_data["task_id"]
        assert loaded_reports[0].title == sample_report_data["title"]
        assert loaded_reports[0].status == sample_report_data["status"]
        assert loaded_reports[0].retry_count == sample_report_data["retry_count"]

    def test_load_reports_multiple_files(
        self, sample_report_data, sample_report_data_failed, temp_reports_dir
    ):
        """여러 YAML 파일을 모두 로드"""
        report1 = TaskReport(**sample_report_data)
        report2 = TaskReport(**sample_report_data_failed)
        save_report(report1, temp_reports_dir)
        save_report(report2, temp_reports_dir)

        loaded_reports = load_reports(temp_reports_dir)
        assert len(loaded_reports) == 2

        task_ids = {r.task_id for r in loaded_reports}
        assert "task-001" in task_ids
        assert "task-002" in task_ids

    def test_load_reports_preserves_all_fields(self, sample_report_data, temp_reports_dir):
        """로드된 TaskReport가 모든 필드를 정확히 복원"""
        report = TaskReport(**sample_report_data)
        save_report(report, temp_reports_dir)

        loaded_reports = load_reports(temp_reports_dir)
        loaded = loaded_reports[0]

        assert loaded.task_id == sample_report_data["task_id"]
        assert loaded.title == sample_report_data["title"]
        assert loaded.status == sample_report_data["status"]
        assert loaded.completed_at == sample_report_data["completed_at"]
        assert loaded.retry_count == sample_report_data["retry_count"]
        assert loaded.test_count == sample_report_data["test_count"]
        assert loaded.test_pass_first_try == sample_report_data["test_pass_first_try"]
        assert loaded.reviewer_verdict == sample_report_data["reviewer_verdict"]
        assert loaded.time_elapsed_seconds == sample_report_data["time_elapsed_seconds"]
        assert loaded.failure_reasons == sample_report_data["failure_reasons"]
        assert loaded.reviewer_feedback == sample_report_data["reviewer_feedback"]

    def test_load_reports_with_none_values(
        self, sample_report_data_in_progress, temp_reports_dir
    ):
        """None 값을 포함한 TaskReport 로드"""
        report = TaskReport(**sample_report_data_in_progress)
        save_report(report, temp_reports_dir)

        loaded_reports = load_reports(temp_reports_dir)
        loaded = loaded_reports[0]

        assert loaded.completed_at is None
        assert loaded.reviewer_feedback is None

    def test_load_reports_since_filter(self, sample_report_data, temp_reports_dir):
        """수락 기준 6: since 파라미터로 completed_at 기준 필터링"""
        # 2024-01-15 저장
        report1 = TaskReport(**sample_report_data)
        save_report(report1, temp_reports_dir)

        # 2024-01-16 저장
        data2 = sample_report_data.copy()
        data2["task_id"] = "task-004"
        data2["completed_at"] = "2024-01-16T10:30:00"
        report2 = TaskReport(**data2)
        save_report(report2, temp_reports_dir)

        # since 없이 로드
        all_reports = load_reports(temp_reports_dir)
        assert len(all_reports) == 2

        # 2024-01-16 이후만 로드
        since = datetime.fromisoformat("2024-01-16T00:00:00")
        filtered_reports = load_reports(temp_reports_dir, since=since)
        assert len(filtered_reports) == 1
        assert filtered_reports[0].task_id == "task-004"

    def test_load_reports_since_exact_match(self, sample_report_data, temp_reports_dir):
        """since와 completed_at이 정확히 일치하는 경우 포함"""
        report = TaskReport(**sample_report_data)
        save_report(report, temp_reports_dir)

        since = datetime.fromisoformat("2024-01-15T10:30:00")
        filtered_reports = load_reports(temp_reports_dir, since=since)
        assert len(filtered_reports) == 1

    def test_load_reports_since_excludes_earlier(self, sample_report_data, temp_reports_dir):
        """since보다 이전 completed_at은 제외"""
        report = TaskReport(**sample_report_data)
        save_report(report, temp_reports_dir)

        since = datetime.fromisoformat("2024-01-16T00:00:00")
        filtered_reports = load_reports(temp_reports_dir, since=since)
        assert len(filtered_reports) == 0

    def test_load_reports_since_with_none_completed_at(
        self, sample_report_data_in_progress, temp_reports_dir
    ):
        """completed_at이 None인 경우 since 필터링에서 제외"""
        report = TaskReport(**sample_report_data_in_progress)
        save_report(report, temp_reports_dir)

        since = datetime.fromisoformat("2024-01-01T00:00:00")
        filtered_reports = load_reports(temp_reports_dir, since=since)
        assert len(filtered_reports) == 0


class TestAggregate:
    """aggregate() 함수 테스트"""

    def test_aggregate_empty_list(self):
        """수락 기준 3: aggregate([])는 total=0, success_rate=0, first_try_rate=0"""
        result = aggregate([])

        assert result["total"] == 0
        assert result["success_rate"] == 0
        assert result["first_try_rate"] == 0

    def test_aggregate_empty_list_all_keys(self):
        """aggregate([])는 모든 필수 키를 반환"""
        result = aggregate([])

        required_keys = [
            "total",
            "completed",
            "failed",
            "success_rate",
            "first_try_rate",
            "avg_elapsed_seconds",
            "total_retries",
            "reviewer_approved",
        ]
        for key in required_keys:
            assert key in result

    def test_aggregate_single_completed_report(self, sample_report_data):
        """단일 COMPLETED 리포트 집계"""
        report = TaskReport(**sample_report_data)
        result = aggregate([report])

        assert result["total"] == 1
        assert result["completed"] == 1
        assert result["failed"] == 0

    def test_aggregate_completed_status_only(
        self, sample_report_data, sample_report_data_failed, sample_report_data_in_progress
    ):
        """수락 기준 4: status=='COMPLETED'인 항목만 completed로 집계"""
        report1 = TaskReport(**sample_report_data)  # COMPLETED
        report2 = TaskReport(**sample_report_data_failed)  # FAILED
        report3 = TaskReport(**sample_report_data_in_progress)  # IN_PROGRESS

        result = aggregate([report1, report2, report3])

        assert result["total"] == 3
        assert result["completed"] == 1  # COMPLETED만 카운트
        assert result["failed"] == 1  # FAILED 카운트

    def test_aggregate_success_rate_calculation(self, sample_report_data):
        """수락 기준 5: success_rate = completed/total*100 (정수 반올림)"""
        # 1개 COMPLETED, 1개 FAILED
        report1 = TaskReport(**sample_report_data)
        data2 = sample_report_data.copy()
        data2["task_id"] = "task-005"
        data2["status"] = "FAILED"
        report2 = TaskReport(**data2)

        result = aggregate([report1, report2])

        # completed=1, total=2 → 1/2*100 = 50
        assert result["success_rate"] == 50

    def test_aggregate_success_rate_rounding(self, sample_report_data):
        """success_rate는 정수로 반올림"""
        # 1개 COMPLETED, 2개 FAILED → 1/3*100 = 33.333... → 33
        report1 = TaskReport(**sample_report_data)
        data2 = sample_report_data.copy()
        data2["task_id"] = "task-006"
        data2["status"] = "FAILED"
        report2 = TaskReport(**data2)
        data3 = sample_report_data.copy()
        data3["task_id"] = "task-007"
        data3["status"] = "FAILED"
        report3 = TaskReport(**data3)

        result = aggregate([report1, report2, report3])

        # 1/3*100 = 33.333... → 33 (반올림)
        assert isinstance(result["success_rate"], int)
        assert result["success_rate"] == 33

    def test_aggregate_first_try_rate(self, sample_report_data):
        """first_try_rate 계산"""
        # test_pass_first_try=True인 항목 비율
        report1 = TaskReport(**sample_report_data)  # True
        data2 = sample_report_data.copy()
        data2["task_id"] = "task-008"
        data2["test_pass_first_try"] = False
        report2 = TaskReport(**data2)

        result = aggregate([report1, report2])

        # 1/2*100 = 50
        assert result["first_try_rate"] == 50

    def test_aggregate_avg_elapsed_seconds(self, sample_report_data):
        """평균 경과 시간 계산"""
        report1 = TaskReport(**sample_report_data)  # 3600.5
        data2 = sample_report_data.copy()
        data2["task_id"] = "task-009"
        data2["time_elapsed_seconds"] = 7200.0
        report2 = TaskReport(**data2)

        result = aggregate([report1, report2])

        # (3600.5 + 7200.0) / 2 = 5400.25
        assert result["avg_elapsed_seconds"] == 5400.25

    def test_aggregate_total_retries(self, sample_report_data):
        """총 재시도 횟수 합계"""
        report1 = TaskReport(**sample_report_data)  # retry_count=2
        data2 = sample_report_data.copy()
        data2["task_id"] = "task-010"
        data2["retry_count"] = 3
        report2 = TaskReport(**data2)

        result = aggregate([report1, report2])

        assert result["total_retries"] == 5

    def test_aggregate_reviewer_approved(self, sample_report_data):
        """수락 기준 7: reviewer_verdict=='APPROVED'인 항목 수"""
        report1 = TaskReport(**sample_report_data)  # APPROVED
        data2 = sample_report_data.copy()
        data2["task_id"] = "task-011"
        data2["reviewer_verdict"] = "REJECTED"
        report2 = TaskReport(**data2)
        data3 = sample_report_data.copy()
        data3["task_id"] = "task-012"
        data3["reviewer_verdict"] = "PENDING"
        report3 = TaskReport(**data3)

        result = aggregate([report1, report2, report3])

        assert result["reviewer_approved"] == 1

    def test_aggregate_all_approved(self, sample_report_data):
        """모든 항목이 APPROVED인 경우"""
        report1 = TaskReport(**sample_report_data)
        data2 = sample_report_data.copy()
        data2["task_id"] = "task-013"
        report2 = TaskReport(**data2)

        result = aggregate([report1, report2])

        assert result["reviewer_approved"] == 2

    def test_aggregate_none_approved(self, sample_report_data):
        """APPROVED가 없는 경우"""
        data1 = sample_report_data.copy()
        data1["reviewer_verdict"] = "REJECTED"
        report1 = TaskReport(**data1)
        data2 = sample_report_data.copy()
        data2["task_id"] = "task-014"
        data2["reviewer_verdict"] = "PENDING"
        report2 = TaskReport(**data2)

        result = aggregate([report1, report2])

        assert result["reviewer_approved"] == 0

    def test_aggregate_complex_scenario(
        self, sample_report_data, sample_report_data_failed, sample_report_data_in_progress
    ):
        """복합 시나리오: 여러 상태의 리포트 집계"""
        report1 = TaskReport(**sample_report_data)  # COMPLETED, APPROVED
        report2 = TaskReport(**sample_report_data_failed)  # FAILED, REJECTED
        report3 = TaskReport(**sample_report_data_in_progress)  # IN_PROGRESS, PENDING

        result = aggregate([report1, report2, report3])

        assert result["total"] == 3
        assert result["completed"] == 1
        assert result["failed"] == 1
        assert result["success_rate"] == 33  # 1/3*100
        assert result["reviewer_approved"] == 1
        assert result["total_retries"] == 8  # 2+5+1

    def test_aggregate_zero_elapsed_seconds(self, sample_report_data):
        """time_elapsed_seconds가 0인 경우"""
        data = sample_report_data.copy()
        data["time_elapsed_seconds"] = 0
        report = TaskReport(**data)

        result = aggregate([report])

        assert result["avg_elapsed_seconds"] == 0


class TestIntegration:
    """통합 테스트: save, load, aggregate 함께 동작"""

    def test_save_load_aggregate_workflow(
        self, sample_report_data, sample_report_data_failed, temp_reports_dir
    ):
        """전체 워크플로우: 저장 → 로드 → 집계"""
        # 저장
        report1 = TaskReport(**sample_report_data)
        report2 = TaskReport(**sample_report_data_failed)
        save_report(report1, temp_reports_dir)
        save_report(report2, temp_reports_dir)

        # 로드
        loaded_reports = load_reports(temp_reports_dir)
        assert len(loaded_reports) == 2

        # 집계
        result = aggregate(loaded_reports)
        assert result["total"] == 2
        assert result["completed"] == 1
        assert result["failed"] == 1
        assert result["reviewer_approved"] == 1

    def test_save_load_aggregate_with_since_filter(
        self, sample_report_data, temp_reports_dir
    ):
        """since 필터링을 포함한 전체 워크플로우"""
        # 2024-01-15 저장
        report1 = TaskReport(**sample_report_data)
        save_report(report1, temp_reports_dir)

        # 2024-01-16 저장
        data2 = sample_report_data.copy()
        data2["task_id"] = "task-015"
        data2["completed_at"] = "2024-01-16T10:30:00"
        report2 = TaskReport(**data2)
        save_report(report2, temp_reports_dir)

        # 2024-01-16 이후만 로드
        since = datetime.fromisoformat("2024-01-16T00:00:00")
        filtered_reports = load_reports(temp_reports_dir, since=since)

        # 집계
        result = aggregate(filtered_reports)
        assert result["total"] == 1
        assert result["completed"] == 1
