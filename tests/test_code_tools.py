"""
tests/test_code_tools.py

tools/code_tools.py 단위 테스트.
외부 의존성 없음 — tmp_path fixture로 실제 Python 파일 I/O 검증.

실행:
    pytest tests/test_code_tools.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tools.code_tools import get_function_src, get_imports, get_outline


# ── 공통 픽스처 ────────────────────────────────────────────────────────────────

SAMPLE_SOURCE = '''\
import os
import sys
from pathlib import Path
from typing import Optional, List

CONSTANT = 42


def standalone(x, y, *args, **kwargs):
    """독립 함수 docstring."""
    return x + y


async def async_func(value: int) -> str:
    return str(value)


class MyClass(object):
    """클래스 docstring."""

    def __init__(self, name: str):
        self.name = name

    def greet(self) -> str:
        """인사 메서드."""
        return f"Hello, {self.name}"

    @staticmethod
    def static_method():
        pass
'''


@pytest.fixture
def sample_py(tmp_path) -> str:
    f = tmp_path / "sample.py"
    f.write_text(SAMPLE_SOURCE, encoding="utf-8")
    return str(f)


# ── get_imports ────────────────────────────────────────────────────────────────


class TestGetImports:
    def test_detects_all_imports(self, sample_py):
        result = get_imports(sample_py)

        assert result.success is True
        assert "import os" in result.output
        assert "import sys" in result.output
        assert "from pathlib import Path" in result.output
        assert "from typing import Optional, List" in result.output

    def test_output_includes_line_numbers(self, sample_py):
        result = get_imports(sample_py)

        # 각 줄이 "L숫자" 로 시작해야 함
        for line in result.output.splitlines():
            assert line.startswith("L"), f"줄 번호 없음: {line!r}"

    def test_sorted_by_line_number(self, sample_py):
        result = get_imports(sample_py)

        line_nums = [
            int(line.split()[0][1:])  # "L3" → 3
            for line in result.output.splitlines()
        ]
        assert line_nums == sorted(line_nums)

    def test_no_imports_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("x = 1\n", encoding="utf-8")

        result = get_imports(str(f))

        assert result.success is True
        assert "import 문 없음" in result.output

    def test_missing_file_returns_error(self, tmp_path):
        result = get_imports(str(tmp_path / "ghost.py"))

        assert result.success is False
        assert result.error is not None
        assert "ghost.py" in result.error

    def test_syntax_error_returns_error(self, tmp_path):
        f = tmp_path / "broken.py"
        f.write_text("def (:\n", encoding="utf-8")

        result = get_imports(str(f))

        assert result.success is False
        assert "문법 오류" in result.error


# ── get_outline ────────────────────────────────────────────────────────────────


class TestGetOutline:
    def test_detects_top_level_functions(self, sample_py):
        result = get_outline(sample_py)

        assert result.success is True
        assert "standalone" in result.output
        assert "async_func" in result.output

    def test_async_function_marked(self, sample_py):
        result = get_outline(sample_py)

        assert "async def async_func" in result.output

    def test_detects_class_and_methods(self, sample_py):
        result = get_outline(sample_py)

        assert "class MyClass" in result.output
        assert "__init__" in result.output
        assert "greet" in result.output
        assert "static_method" in result.output

    def test_methods_are_indented(self, sample_py):
        result = get_outline(sample_py)

        method_lines = [
            l for l in result.output.splitlines() if "__init__" in l or "greet" in l
        ]
        assert all("  " in l for l in method_lines), "메서드가 들여쓰기 없음"

    def test_base_class_shown(self, sample_py):
        result = get_outline(sample_py)

        assert "MyClass(object)" in result.output

    def test_docstring_shown(self, sample_py):
        result = get_outline(sample_py)

        assert "독립 함수 docstring" in result.output
        assert "인사 메서드" in result.output

    def test_output_includes_line_numbers(self, sample_py):
        result = get_outline(sample_py)

        for line in result.output.splitlines():
            assert line.startswith("L"), f"줄 번호 없음: {line!r}"

    def test_empty_file_returns_indicator(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("x = 1\n", encoding="utf-8")

        result = get_outline(str(f))

        assert result.success is True
        assert "없음" in result.output

    def test_missing_file_returns_error(self, tmp_path):
        result = get_outline(str(tmp_path / "ghost.py"))

        assert result.success is False
        assert result.error is not None

    def test_syntax_error_returns_error(self, tmp_path):
        f = tmp_path / "broken.py"
        f.write_text("def (:\n", encoding="utf-8")

        result = get_outline(str(f))

        assert result.success is False
        assert "문법 오류" in result.error


# ── get_function_src ───────────────────────────────────────────────────────────


class TestGetFunctionSrc:
    def test_extracts_top_level_function(self, sample_py):
        result = get_function_src(sample_py, "standalone")

        assert result.success is True
        assert "def standalone" in result.output
        assert "return x + y" in result.output

    def test_extracts_method(self, sample_py):
        result = get_function_src(sample_py, "greet")

        assert result.success is True
        assert "def greet" in result.output
        assert "Hello" in result.output

    def test_output_includes_line_numbers(self, sample_py):
        result = get_function_src(sample_py, "standalone")

        assert result.success is True
        # 각 줄이 "숫자:" 형태로 시작해야 함
        for line in result.output.splitlines():
            prefix = line.split(":")[0].strip()
            assert prefix.isdigit(), f"줄 번호 없음: {line!r}"

    def test_not_found_returns_error(self, sample_py):
        result = get_function_src(sample_py, "nonexistent_func")

        assert result.success is False
        assert "nonexistent_func" in result.error

    def test_missing_file_returns_error(self, tmp_path):
        result = get_function_src(str(tmp_path / "ghost.py"), "foo")

        assert result.success is False
        assert result.error is not None

    def test_syntax_error_returns_error(self, tmp_path):
        f = tmp_path / "broken.py"
        f.write_text("def (:\n", encoding="utf-8")

        result = get_function_src(str(f), "foo")

        assert result.success is False
        assert "문법 오류" in result.error

    def test_async_function_extracted(self, sample_py):
        result = get_function_src(sample_py, "async_func")

        assert result.success is True
        assert "async def async_func" in result.output
