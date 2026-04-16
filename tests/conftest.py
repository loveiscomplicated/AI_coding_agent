import sys
from datetime import datetime
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from reports.task_report import TaskReport


# ── structure.updater 테스트용 fixture ───────────────────────────────────────

@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sample_python_file(tmp_path: Path) -> Path:
    """SampleClass·AnotherClass + 최상위 함수 2개를 포함한 Python 파일."""
    f = tmp_path / "sample.py"
    f.write_text(
        '''\
class SampleClass:
    """샘플 클래스 docstring"""

    def method_one(self):
        pass

    def method_two(self):
        pass


class AnotherClass:
    pass


def top_level_function():
    """최상위 함수 docstring"""
    pass


def another_function(x, y):
    """두 번째 함수"""
    return x + y
''',
        encoding="utf-8",
    )
    return f


@pytest.fixture
def syntax_error_file(tmp_path: Path) -> Path:
    """문법 오류가 있는 Python 파일."""
    f = tmp_path / "broken.py"
    f.write_text("def broken(\n    # 괄호 닫히지 않음\n", encoding="utf-8")
    return f


@pytest.fixture
def complex_directory_structure(tmp_path: Path) -> Path:
    """재귀 탐색·제외 디렉토리 테스트용 디렉토리 구조."""
    # src/module1.py  — ClassA + func_a
    src = tmp_path / "src"
    src.mkdir()
    (src / "module1.py").write_text(
        '''\
class ClassA:
    """ClassA docstring"""
    def method_a(self): pass


def func_a():
    """func_a docstring"""
    pass
''',
        encoding="utf-8",
    )

    # src/module2.py  — 단순 함수
    (src / "module2.py").write_text(
        '''\
def func_b():
    pass
''',
        encoding="utf-8",
    )

    # src/subdir/module3.py  — 재귀 탐색 확인용
    subdir = src / "subdir"
    subdir.mkdir()
    (subdir / "module3.py").write_text("def func_c(): pass\n", encoding="utf-8")

    # 제외 디렉토리들
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cache.pyc").write_text("", encoding="utf-8")

    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "site.py").write_text("def site(): pass\n", encoding="utf-8")

    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("[core]\n", encoding="utf-8")

    return tmp_path


@pytest.fixture
def empty_directory(tmp_path: Path) -> Path:
    """파일이 전혀 없는 빈 디렉토리."""
    d = tmp_path / "empty"
    d.mkdir()
    return d


# ── metrics/collector 테스트용 fixture ───────────────────────────────────────

@pytest.fixture
def temp_reports_dir(tmp_path: Path) -> str:
    d = tmp_path / "reports"
    d.mkdir()
    return str(d)


@pytest.fixture
def sample_task_report_data():
    return {
        "task_id": "task-001",
        "title": "메트릭 수집기 구현",
        "status": "COMPLETED",
        "completed_at": "2024-01-15T10:30:00",
        "retry_count": 2,
        "time_elapsed_seconds": 3600.5,
        "test_count": 15,
        "test_pass_first_try": True,
        "reviewer_verdict": "APPROVED",
        "failure_reasons": [],
        "reviewer_feedback": "좋은 구현입니다",
    }


@pytest.fixture
def sample_task_report_data_failed():
    return {
        "task_id": "task-002",
        "title": "실패 태스크",
        "status": "FAILED",
        "completed_at": "2024-01-16T11:00:00",  # since 필터 테스트: 2024-01-16 이후
        "retry_count": 5,
        "time_elapsed_seconds": 7200.0,
        "test_count": 5,
        "test_pass_first_try": False,
        "reviewer_verdict": "CHANGES_REQUESTED",
        "failure_reasons": ["테스트 실패", "성능 이슈"],
        "reviewer_feedback": "수정 필요",
    }


@pytest.fixture
def sample_task_report_data_pending():
    return {
        "task_id": "task-003",
        "title": "대기 태스크",
        "status": "IN_PROGRESS",
        "completed_at": None,
        "retry_count": 1,
        "time_elapsed_seconds": 1800.0,
        "test_pass_first_try": True,  # complex_scenario: first_try_rate=67(2/3)
    }


# ── 기존 fixture ─────────────────────────────────────────────────────────────

@pytest.fixture
def egg_doneness():
    def _egg_doneness(seconds):
        if seconds < 2:
            return {'name': '날계란', 'emoji': '🥚💧'}
        elif seconds < 5:
            return {'name': '반반숙', 'emoji': '흐르는 느낌'}
        elif seconds < 10:
            return {'name': '반숙', 'emoji': '🟡🏆'}
        elif seconds < 20:
            return {'name': '완숙', 'emoji': '🟠'}
        else:
            return {'name': '터짐', 'emoji': '💥💀'}
    return _egg_doneness


@pytest.fixture
def sample_completed_report() -> TaskReport:
    return TaskReport(
        task_id="task-001",
        title="데이터 수집",
        status="COMPLETED",
        completed_at="2026-01-01T10:00:00+00:00",
        retry_count=0,
        time_elapsed_seconds=120.0,
        test_count=5,
        test_pass_first_try=True,
        reviewer_verdict="APPROVED",
    )


@pytest.fixture
def sample_failed_report() -> TaskReport:
    return TaskReport(
        task_id="task-002",
        title="데이터 처리",
        status="FAILED",
        completed_at="2026-01-01T11:00:00+00:00",
        retry_count=2,
        time_elapsed_seconds=300.0,
        test_count=3,
        test_pass_first_try=False,
        reviewer_verdict="CHANGES_REQUESTED",
        failure_reasons=["테스트 실패"],
    )


@pytest.fixture
def sample_reports_list(
    sample_completed_report: TaskReport,
    sample_failed_report: TaskReport,
) -> list[TaskReport]:
    return [
        sample_completed_report,
        TaskReport(
            task_id="task-003",
            title="API 연동",
            status="COMPLETED",
            completed_at="2026-01-02T10:00:00+00:00",
            retry_count=1,
            time_elapsed_seconds=180.0,
            test_count=4,
            test_pass_first_try=False,
            reviewer_verdict="APPROVED",
        ),
        sample_failed_report,
        TaskReport(
            task_id="task-004",
            title="배치 최적화",
            status="COMPLETED",
            completed_at="2026-01-03T10:00:00+00:00",
            retry_count=0,
            time_elapsed_seconds=240.0,
            test_count=6,
            test_pass_first_try=True,
            reviewer_verdict="APPROVED",
        ),
    ]


@pytest.fixture
def reports_with_various_times() -> list[TaskReport]:
    return [
        TaskReport(
            task_id="task-early",
            title="초기 작업",
            status="COMPLETED",
            completed_at=datetime(2024, 1, 15, 10, 0, 0),
            time_elapsed_seconds=60.0,
        ),
        TaskReport(
            task_id="task-middle",
            title="중간 작업",
            status="COMPLETED",
            completed_at=datetime(2024, 1, 15, 12, 0, 0),
            time_elapsed_seconds=90.0,
        ),
        TaskReport(
            task_id="task-late",
            title="후기 작업",
            status="FAILED",
            completed_at=datetime(2024, 1, 15, 14, 0, 0),
            time_elapsed_seconds=120.0,
        ),
    ]
