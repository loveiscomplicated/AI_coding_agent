"""테스트 픽스처 및 공통 설정"""
import pytest
from pathlib import Path
import tempfile
import shutil
import sys

# src 디렉토리를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def temp_workspace():
    """임시 워크스페이스 디렉토리 생성"""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_python_file(temp_workspace):
    """샘플 Python 파일 생성"""
    sample_code = '''"""모듈 docstring"""

def top_level_function():
    """최상위 함수 docstring"""
    pass

def another_function(x, y):
    """다른 함수"""
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
    def method_a(self):
        pass
'''
    file_path = temp_workspace / "sample.py"
    file_path.write_text(sample_code)
    return file_path


@pytest.fixture
def syntax_error_file(temp_workspace):
    """구문 오류가 있는 Python 파일"""
    error_code = '''
def broken_function(
    # 괄호가 닫히지 않음
'''
    file_path = temp_workspace / "broken.py"
    file_path.write_text(error_code)
    return file_path


@pytest.fixture
def complex_directory_structure(temp_workspace):
    """복잡한 디렉토리 구조 생성"""
    # src 디렉토리
    src_dir = temp_workspace / "src"
    src_dir.mkdir()
    
    # src/module1.py
    (src_dir / "module1.py").write_text('''
def func1():
    """함수 1"""
    pass

class Class1:
    def method1(self):
        pass
''')
    
    # src/module2.py
    (src_dir / "module2.py").write_text('''
def func2():
    pass
''')
    
    # tests 디렉토리
    tests_dir = temp_workspace / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_module.py").write_text('''
def test_something():
    pass
''')
    
    # __pycache__ 디렉토리 (제외되어야 함)
    pycache_dir = temp_workspace / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "module.pyc").write_text("binary")
    
    # venv 디렉토리 (제외되어야 함)
    venv_dir = temp_workspace / "venv"
    venv_dir.mkdir()
    (venv_dir / "lib.py").write_text("def lib(): pass")
    
    return temp_workspace


@pytest.fixture
def empty_directory(temp_workspace):
    """빈 디렉토리"""
    return temp_workspace
