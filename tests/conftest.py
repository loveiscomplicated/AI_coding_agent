"""
테스트 공통 fixture
"""
import pytest
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import shutil


@pytest.fixture
def temp_workspace():
    """임시 워크스페이스 디렉토리 생성"""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_python_file(temp_workspace):
    """샘플 파이썬 파일 생성"""
    content = '''"""모듈 docstring"""

def top_level_function():
    """최상위 함수 docstring"""
    pass

def another_function(x, y):
    """또 다른 함수
    여러 줄 docstring"""
    return x + y

class SampleClass:
    """샘플 클래스 docstring"""
    
    def method_one(self):
        """메서드 1"""
        pass
    
    def method_two(self, arg):
        """메서드 2"""
        pass

class AnotherClass:
    """또 다른 클래스"""
    
    def __init__(self):
        pass
    
    def public_method(self):
        pass
    
    def _private_method(self):
        pass
'''
    file_path = temp_workspace / "sample.py"
    file_path.write_text(content)
    return file_path


@pytest.fixture
def syntax_error_file(temp_workspace):
    """구문 오류가 있는 파이썬 파일"""
    content = '''
def broken_function(
    # 괄호가 닫히지 않음
'''
    file_path = temp_workspace / "broken.py"
    file_path.write_text(content)
    return file_path


@pytest.fixture
def complex_directory_structure(temp_workspace):
    """복잡한 디렉토리 구조 생성"""
    # src 디렉토리
    src_dir = temp_workspace / "src"
    src_dir.mkdir()
    
    # src/module1.py
    (src_dir / "module1.py").write_text('''
class ClassA:
    """ClassA docstring"""
    def method_a(self):
        pass

def func_a():
    """func_a docstring"""
    pass
''')
    
    # src/module2.py
    (src_dir / "module2.py").write_text('''
class ClassB:
    """ClassB docstring"""
    def method_b(self):
        pass
''')
    
    # src/subdir 디렉토리
    subdir = src_dir / "subdir"
    subdir.mkdir()
    
    # src/subdir/module3.py
    (subdir / "module3.py").write_text('''
def func_c():
    """func_c docstring"""
    pass
''')
    
    # __pycache__ 디렉토리 (제외되어야 함)
    pycache = temp_workspace / "__pycache__"
    pycache.mkdir()
    (pycache / "cache.py").write_text('# 이 파일은 무시되어야 함')
    
    # venv 디렉토리 (제외되어야 함)
    venv = temp_workspace / "venv"
    venv.mkdir()
    (venv / "ignored.py").write_text('# 이 파일도 무시되어야 함')
    
    # .git 디렉토리 (제외되어야 함)
    git_dir = temp_workspace / ".git"
    git_dir.mkdir()
    (git_dir / "config.py").write_text('# 이 파일도 무시되어야 함')
    
    return temp_workspace


@pytest.fixture
def empty_directory(temp_workspace):
    """파이썬 파일이 없는 디렉토리"""
    (temp_workspace / "empty_dir").mkdir()
    return temp_workspace


@pytest.fixture
def temp_reports_dir():
    """임시 reports 디렉토리 생성 및 정리"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def sample_task_report_data():
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
        "reviewer_feedback": "좋은 구현입니다"
    }


@pytest.fixture
def sample_task_report_data_failed():
    """실패한 TaskReport 샘플 데이터"""
    return {
        "task_id": "task-002",
        "title": "버그 수정",
        "status": "FAILED",
        "completed_at": "2024-01-16T14:20:00",
        "retry_count": 5,
        "test_count": 10,
        "test_pass_first_try": False,
        "reviewer_verdict": "REJECTED",
        "time_elapsed_seconds": 7200.0,
        "failure_reasons": ["테스트 실패", "성능 이슈"],
        "reviewer_feedback": "수정 필요"
    }


@pytest.fixture
def sample_task_report_data_pending():
    """진행 중인 TaskReport 샘플 데이터"""
    return {
        "task_id": "task-003",
        "title": "기능 개발",
        "status": "IN_PROGRESS",
        "completed_at": None,
        "retry_count": 1,
        "test_count": 8,
        "test_pass_first_try": True,
        "reviewer_verdict": "PENDING",
        "time_elapsed_seconds": 1800.0,
        "failure_reasons": [],
        "reviewer_feedback": ""
    }
