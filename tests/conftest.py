"""테스트 공통 설정 및 fixture"""
import pytest
from datetime import datetime, timezone
from metrics.collector import TaskReport, TaskStatus


@pytest.fixture
def sample_reports():
    """샘플 TaskReport 리스트"""
    return [
        TaskReport(
            task_id="task_1",
            status=TaskStatus.COMPLETED,
            completed_at=datetime(2026, 1, 5, 10, 30, 0, tzinfo=timezone.utc),  # 월요일
            elapsed_seconds=3600.0,
            retries=0,
            first_try=True,
        ),
        TaskReport(
            task_id="task_2",
            status=TaskStatus.COMPLETED,
            completed_at=datetime(2026, 1, 7, 14, 15, 0, tzinfo=timezone.utc),  # 수요일
            elapsed_seconds=7200.0,
            retries=1,
            first_try=False,
        ),
        TaskReport(
            task_id="task_3",
            status=TaskStatus.FAILED,
            completed_at=datetime(2026, 1, 9, 16, 45, 0, tzinfo=timezone.utc),  # 금요일
            elapsed_seconds=5400.0,
            retries=2,
            first_try=False,
        ),
        TaskReport(
            task_id="task_4",
            status=TaskStatus.COMPLETED,
            completed_at=datetime(2026, 1, 11, 9, 0, 0, tzinfo=timezone.utc),  # 일요일
            elapsed_seconds=1800.0,
            retries=0,
            first_try=True,
        ),
        TaskReport(
            task_id="task_5",
            status=TaskStatus.PENDING,
            completed_at=None,
            elapsed_seconds=0.0,
            retries=0,
            first_try=True,
        ),
    ]


@pytest.fixture
def reports_different_weeks():
    """다른 주에 속한 TaskReport 리스트"""
    return [
        # 1주차 (2026-01-05 ~ 2026-01-11)
        TaskReport(
            task_id="task_w1_1",
            status=TaskStatus.COMPLETED,
            completed_at=datetime(2026, 1, 5, 10, 0, 0, tzinfo=timezone.utc),
            elapsed_seconds=3600.0,
            retries=0,
            first_try=True,
        ),
        # 2주차 (2026-01-12 ~ 2026-01-18)
        TaskReport(
            task_id="task_w2_1",
            status=TaskStatus.COMPLETED,
            completed_at=datetime(2026, 1, 12, 10, 0, 0, tzinfo=timezone.utc),
            elapsed_seconds=3600.0,
            retries=0,
            first_try=True,
        ),
        # 3주차 (2026-01-19 ~ 2026-01-25)
        TaskReport(
            task_id="task_w3_1",
            status=TaskStatus.COMPLETED,
            completed_at=datetime(2026, 1, 19, 10, 0, 0, tzinfo=timezone.utc),
            elapsed_seconds=3600.0,
            retries=0,
            first_try=True,
        ),
    ]
