import pytest
from pathlib import Path


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
