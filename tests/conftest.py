"""
메트릭 수집기 테스트 공통 fixture
"""
import pytest
from datetime import datetime
from pathlib import Path
import tempfile
import shutil


@pytest.fixture
def temp_reports_dir():
    """임시 reports 디렉토리 생성 및 정리"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def sample_report_data():
    """샘플 TaskReport 데이터"""
    return {
        "task_id": "task-001",
        "title": "메트릭 수집기 구현",
        "status": "COMPLETED",
        "completed_at": "2024-01-15T10:30:00",
        "retry_count": 2,
        "test_count": 15,
        "test_pass_first_try": True,
        "reviewer_verdict": "APPROVED",
        "time_elapsed_seconds": 3600.5,
        "failure_reasons": [],
        "reviewer_feedback": "좋은 구현입니다.",
    }


@pytest.fixture
def sample_report_data_failed():
    """실패한 TaskReport 샘플 데이터"""
    return {
        "task_id": "task-002",
        "title": "데이터 검증 모듈",
        "status": "FAILED",
        "completed_at": "2024-01-16T14:20:00",
        "retry_count": 5,
        "test_count": 10,
        "test_pass_first_try": False,
        "reviewer_verdict": "REJECTED",
        "time_elapsed_seconds": 7200.0,
        "failure_reasons": ["테스트 실패", "성능 미달"],
        "reviewer_feedback": "재작업 필요합니다.",
    }


@pytest.fixture
def sample_report_data_in_progress():
    """진행 중인 TaskReport 샘플 데이터"""
    return {
        "task_id": "task-003",
        "title": "API 서버 구현",
        "status": "IN_PROGRESS",
        "completed_at": None,
        "retry_count": 1,
        "test_count": 20,
        "test_pass_first_try": False,
        "reviewer_verdict": "PENDING",
        "time_elapsed_seconds": 1800.0,
        "failure_reasons": [],
        "reviewer_feedback": None,
    }
