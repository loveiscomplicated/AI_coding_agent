"""
execution_brief 테스트용 공통 fixture
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
    """완료된 태스크 보고서"""
    return TaskReport(
        task_id="task-001",
        title="데이터 수집",
        status="COMPLETED",
        time_elapsed_seconds=120,
        retry_count=0,
        completed_at=datetime(2024, 1, 15, 10, 0, 0)
    )


@pytest.fixture
def sample_failed_report():
    """실패한 태스크 보고서"""
    return TaskReport(
        task_id="task-002",
        title="데이터 처리",
        status="FAILED",
        time_elapsed_seconds=300,
        retry_count=2,
        completed_at=datetime(2024, 1, 15, 11, 0, 0)
    )


@pytest.fixture
def sample_completed_with_retry():
    """재시도 후 완료된 태스크 보고서"""
    return TaskReport(
        task_id="task-003",
        title="검증 작업",
        status="COMPLETED",
        time_elapsed_seconds=180,
        retry_count=1,
        completed_at=datetime(2024, 1, 15, 12, 0, 0)
    )


@pytest.fixture
def multiple_reports(sample_completed_report, sample_failed_report, sample_completed_with_retry):
    """여러 보고서 리스트"""
    return [sample_completed_report, sample_failed_report, sample_completed_with_retry]


@pytest.fixture
def reports_with_different_dates():
    """다양한 날짜의 보고서들"""
    return [
        TaskReport(
            task_id="task-001",
            title="작업 1",
            status="COMPLETED",
            time_elapsed_seconds=100,
            retry_count=0,
            completed_at=datetime(2024, 1, 10, 10, 0, 0)
        ),
        TaskReport(
            task_id="task-002",
            title="작업 2",
            status="COMPLETED",
            time_elapsed_seconds=200,
            retry_count=0,
            completed_at=datetime(2024, 1, 15, 10, 0, 0)
        ),
        TaskReport(
            task_id="task-003",
            title="작업 3",
            status="COMPLETED",
            time_elapsed_seconds=150,
            retry_count=0,
            completed_at=datetime(2024, 1, 20, 10, 0, 0)
        ),
    ]
