"""
execution_brief 테스트용 공통 fixture 및 설정
"""
import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta

# src 디렉토리를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from metrics.collector import TaskReport


@pytest.fixture
def sample_completed_report():
    """완료된 태스크 리포트"""
    return TaskReport(
        task_id="task-001",
        title="데이터 수집",
        status="COMPLETED",
        time_elapsed_seconds=120,
        retry_count=0,
        completed_at=datetime(2024, 1, 15, 10, 30, 0)
    )


@pytest.fixture
def sample_failed_report():
    """실패한 태스크 리포트"""
    return TaskReport(
        task_id="task-002",
        title="데이터 처리",
        status="FAILED",
        time_elapsed_seconds=300,
        retry_count=2,
        completed_at=datetime(2024, 1, 15, 11, 0, 0)
    )


@pytest.fixture
def sample_reports_list(sample_completed_report, sample_failed_report):
    """여러 태스크 리포트 목록"""
    return [
        sample_completed_report,
        TaskReport(
            task_id="task-003",
            title="검증",
            status="COMPLETED",
            time_elapsed_seconds=60,
            retry_count=1,
            completed_at=datetime(2024, 1, 15, 11, 30, 0)
        ),
        sample_failed_report,
        TaskReport(
            task_id="task-004",
            title="리포팅",
            status="COMPLETED",
            time_elapsed_seconds=45,
            retry_count=0,
            completed_at=datetime(2024, 1, 15, 12, 0, 0)
        ),
    ]


@pytest.fixture
def reports_with_various_times():
    """다양한 시간대의 리포트"""
    base_time = datetime(2024, 1, 15, 10, 0, 0)
    return [
        TaskReport(
            task_id="task-early",
            title="초기 작업",
            status="COMPLETED",
            time_elapsed_seconds=100,
            retry_count=0,
            completed_at=base_time
        ),
        TaskReport(
            task_id="task-middle",
            title="중간 작업",
            status="COMPLETED",
            time_elapsed_seconds=200,
            retry_count=1,
            completed_at=base_time + timedelta(hours=2)
        ),
        TaskReport(
            task_id="task-late",
            title="후기 작업",
            status="FAILED",
            time_elapsed_seconds=300,
            retry_count=2,
            completed_at=base_time + timedelta(hours=4)
        ),
    ]
