"""
tests/test_file_tools.py

tools/file_tools.py 단위 테스트.
외부 의존성 없음 — tmp_path fixture로 실제 파일 I/O 검증.

실행:
    pytest tests/test_file_tools.py -v
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tools.file_tools import (
    append_to_file,
    edit_file,
    list_directory,
    read_file,
    read_file_lines,
    search_files,
    search_in_file,
    write_file,
)


# ── read_file ──────────────────────────────────────────────────────────────


class TestReadFile:
    def test_reads_existing_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world", encoding="utf-8")

        result = read_file(str(f))

        assert result.success is True
        assert result.output == "hello world"
        assert result.error is None

    def test_missing_file_returns_error(self, tmp_path):
        result = read_file(str(tmp_path / "missing.txt"))

        assert result.success is False
        assert result.error is not None
        assert "missing.txt" in result.error

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")

        result = read_file(str(f))

        assert result.success is True
        assert result.output == ""


# ── read_file_lines ────────────────────────────────────────────────────────


class TestReadFileLines:
    @pytest.fixture
    def sample_file(self, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("line1\nline2\nline3\nline4\nline5", encoding="utf-8")
        return str(f)

    def test_reads_partial_range(self, sample_file):
        result = read_file_lines(sample_file, start=2, end=4)

        assert result.success is True
        assert "line2" in result.output
        assert "line3" in result.output
        assert "line4" in result.output
        assert "line1" not in result.output
        assert "line5" not in result.output

    def test_line_numbers_are_1_indexed(self, sample_file):
        result = read_file_lines(sample_file, start=1, end=1)

        assert result.success is True
        assert result.output.startswith("1:")

    def test_missing_file_returns_error(self, tmp_path):
        result = read_file_lines(str(tmp_path / "no.txt"), start=1, end=3)

        assert result.success is False
        assert result.error is not None


# ── list_directory ─────────────────────────────────────────────────────────


class TestListDirectory:
    def test_lists_files_non_recursive(self, tmp_path):
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "b.txt").write_text("", encoding="utf-8")
        sub = tmp_path / "subdir"
        sub.mkdir()

        result = list_directory(str(tmp_path), recursive=False)

        assert result.success is True
        assert "a.py" in result.output
        assert "b.txt" in result.output
        assert "subdir/" in result.output

    def test_lists_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.py").write_text("", encoding="utf-8")

        result = list_directory(str(tmp_path), recursive=True)

        assert result.success is True
        assert "nested.py" in result.output

    def test_missing_directory_returns_error(self, tmp_path):
        result = list_directory(str(tmp_path / "nonexistent"))

        assert result.success is False
        assert result.error is not None


# ── search_in_file ─────────────────────────────────────────────────────────


class TestSearchInFile:
    @pytest.fixture
    def code_file(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    pass\n# TODO: fix this\n", encoding="utf-8")
        return str(f)

    def test_finds_matching_lines(self, code_file):
        result = search_in_file(code_file, pattern="TODO")

        assert result.success is True
        assert "TODO" in result.output
        assert "3:" in result.output  # 3번째 줄

    def test_no_match_returns_indicator(self, code_file):
        result = search_in_file(code_file, pattern="FIXME")

        assert result.success is True
        assert "매칭 없음" in result.output

    def test_missing_file_returns_error(self, tmp_path):
        result = search_in_file(str(tmp_path / "ghost.py"), pattern="foo")

        assert result.success is False
        assert result.error is not None

    def test_regex_pattern(self, code_file):
        result = search_in_file(code_file, pattern=r"def \w+\(\)")

        assert result.success is True
        assert "def foo()" in result.output


# ── search_files ───────────────────────────────────────────────────────────


class TestSearchFiles:
    def test_finds_across_files(self, tmp_path):
        (tmp_path / "a.py").write_text("# TODO: a\nfoo = 1\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("bar = 2\n# TODO: b\n", encoding="utf-8")

        result = search_files(str(tmp_path), pattern="TODO")

        assert result.success is True
        assert "a.py" in result.output
        assert "b.py" in result.output

    def test_filters_by_extension(self, tmp_path):
        (tmp_path / "a.py").write_text("# TODO python\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("# TODO text\n", encoding="utf-8")

        result = search_files(str(tmp_path), pattern="TODO", file_ext=".py")

        assert result.success is True
        assert "a.py" in result.output
        assert "b.txt" not in result.output

    def test_no_match_returns_indicator(self, tmp_path):
        (tmp_path / "x.py").write_text("nothing here\n", encoding="utf-8")

        result = search_files(str(tmp_path), pattern="XYZNOTFOUND")

        assert result.success is True
        assert "매칭 없음" in result.output

    def test_missing_directory_returns_no_match(self, tmp_path):
        # Python 3.12의 rglob은 존재하지 않는 경로에서 빈 iterator 반환
        # → 예외 없이 "매칭 없음"으로 처리됨
        result = search_files(str(tmp_path / "ghost_dir"), pattern="foo")

        assert result.success is True
        assert "매칭 없음" in result.output


# ── write_file ─────────────────────────────────────────────────────────────


class TestWriteFile:
    def test_creates_new_file(self, tmp_path):
        path = str(tmp_path / "new.txt")
        result = write_file(path, "hello")

        assert result.success is True
        assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello"

    def test_overwrites_existing_file(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_text("old content", encoding="utf-8")

        result = write_file(str(f), "new content")

        assert result.success is True
        assert f.read_text(encoding="utf-8") == "new content"

    def test_creates_intermediate_directories(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "c.txt")
        result = write_file(path, "deep")

        assert result.success is True
        assert (tmp_path / "a" / "b" / "c.txt").exists()


# ── edit_file ──────────────────────────────────────────────────────────────


class TestEditFile:
    @pytest.fixture
    def source_file(self, tmp_path):
        f = tmp_path / "source.py"
        f.write_text("def hello():\n    print('hi')\n", encoding="utf-8")
        return str(f)

    def test_replaces_string(self, source_file):
        result = edit_file(source_file, old_str="print('hi')", new_str="print('hello')")

        assert result.success is True
        content = open(source_file, encoding="utf-8").read()
        assert "print('hello')" in content
        assert "print('hi')" not in content

    def test_old_str_not_found(self, source_file):
        result = edit_file(source_file, old_str="DOES_NOT_EXIST", new_str="anything")

        assert result.success is False
        assert result.error is not None
        assert "찾을 수 없음" in result.error

    def test_old_str_appears_multiple_times(self, tmp_path):
        f = tmp_path / "dup.py"
        f.write_text("x = 1\nx = 1\n", encoding="utf-8")

        result = edit_file(str(f), old_str="x = 1", new_str="x = 2")

        assert result.success is False
        assert result.error is not None
        assert "2곳" in result.error

    def test_missing_file_returns_error(self, tmp_path):
        result = edit_file(str(tmp_path / "no.py"), old_str="foo", new_str="bar")

        assert result.success is False
        assert result.error is not None


# ── append_to_file ─────────────────────────────────────────────────────────


class TestAppendToFile:
    def test_appends_to_existing_file(self, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("line1\n", encoding="utf-8")

        result = append_to_file(str(f), "line2\n")

        assert result.success is True
        assert f.read_text(encoding="utf-8") == "line1\nline2\n"

    def test_creates_file_if_missing(self, tmp_path):
        path = str(tmp_path / "new_log.txt")
        result = append_to_file(path, "first line\n")

        assert result.success is True
        assert open(path, encoding="utf-8").read() == "first line\n"
