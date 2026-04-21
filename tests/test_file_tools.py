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
    _build_hashline_table,
    _line_hash,
    append_to_file,
    edit_file,
    hashline_edit,
    hashline_read,
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
        assert "1: hello world" in result.output
        assert f"=== {f} [lines 1-1 of 1] ===" in result.output
        assert "⚠️" not in result.output
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
        assert "[empty file]" in result.output


# ── read_file 페이지네이션 ────────────────────────────────────────────────


class TestReadFilePagination:
    @pytest.fixture
    def big_file(self, tmp_path):
        """200줄 파일"""
        f = tmp_path / "big.txt"
        f.write_text("\n".join(f"line{i}" for i in range(1, 201)), encoding="utf-8")
        return f

    def test_default_truncates_large_file_with_warning(self, big_file):
        result = read_file(str(big_file))

        assert result.success is True
        assert "⚠️ File has 200 lines" in result.output
        assert "Showing lines 1-150" in result.output
        assert f"=== {big_file} [lines 1-150 of 200] ===" in result.output
        assert "150: line150" in result.output
        assert "151: line151" not in result.output

    def test_start_only_reads_max_lines(self, tmp_path):
        f = tmp_path / "huge.txt"
        f.write_text("\n".join(f"row{i}" for i in range(1, 501)), encoding="utf-8")

        result = read_file(str(f), start=50)

        assert result.success is True
        assert f"=== {f} [lines 50-199 of 500] ===" in result.output
        assert "50: row50" in result.output
        assert "199: row199" in result.output
        assert "200: row200" not in result.output
        assert "49: row49" not in result.output

    def test_full_range(self, big_file):
        result = read_file(str(big_file), start=50, end=80)

        assert result.success is True
        assert f"=== {big_file} [lines 50-80 of 200] ===" in result.output
        assert "50: line50" in result.output
        assert "80: line80" in result.output
        assert "49: line49" not in result.output
        assert "81: line81" not in result.output

    def test_end_only_no_warning(self, tmp_path):
        f = tmp_path / "small.txt"
        f.write_text("\n".join(f"a{i}" for i in range(1, 51)), encoding="utf-8")

        result = read_file(str(f), end=10)

        assert result.success is True
        assert f"=== {f} [lines 1-10 of 50] ===" in result.output
        assert "⚠️" not in result.output
        assert "10: a10" in result.output
        assert "11: a11" not in result.output

    def test_start_exceeds_file_length_errors(self, tmp_path):
        f = tmp_path / "tiny.txt"
        f.write_text("\n".join(f"x{i}" for i in range(1, 11)), encoding="utf-8")

        result = read_file(str(f), start=1000)

        assert result.success is False
        assert result.error == "start exceeds file length (10 lines)"

    def test_start_greater_than_end_errors(self, big_file):
        result = read_file(str(big_file), start=10, end=5)

        assert result.success is False
        assert result.error == "invalid range"

    def test_end_clamped_to_total_with_warning(self, tmp_path):
        f = tmp_path / "ten.txt"
        f.write_text("\n".join(f"n{i}" for i in range(1, 11)), encoding="utf-8")

        result = read_file(str(f), end=999)

        assert result.success is True
        assert "clamped to 10" in result.output
        assert f"[lines 1-10 of 10]" in result.output
        assert "10: n10" in result.output

    def test_max_lines_zero_raises_value_error(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("hi", encoding="utf-8")

        with pytest.raises(ValueError):
            read_file(str(f), max_lines=0)

    def test_file_exactly_at_max_lines_no_warning(self, tmp_path):
        f = tmp_path / "exact.txt"
        f.write_text("\n".join(f"k{i}" for i in range(1, 151)), encoding="utf-8")

        result = read_file(str(f))

        assert result.success is True
        assert "⚠️" not in result.output
        assert f"[lines 1-150 of 150]" in result.output

    def test_line_numbers_are_1_indexed(self, tmp_path):
        f = tmp_path / "one.txt"
        f.write_text("only", encoding="utf-8")

        result = read_file(str(f))

        assert result.success is True
        assert "1: only" in result.output
        assert "0: only" not in result.output


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

    def test_appends_multiple_times(self, tmp_path):
        f = tmp_path / "multi.txt"
        f.write_text("", encoding="utf-8")

        for i in range(3):
            append_to_file(str(f), f"line{i}\n")

        assert f.read_text(encoding="utf-8") == "line0\nline1\nline2\n"

    def test_appends_unicode(self, tmp_path):
        f = tmp_path / "unicode.txt"
        f.write_text("첫줄\n", encoding="utf-8")

        result = append_to_file(str(f), "둘째줄\n")

        assert result.success is True
        assert "둘째줄" in f.read_text(encoding="utf-8")

    def test_append_to_directory_returns_error(self, tmp_path):
        result = append_to_file(str(tmp_path), "data")
        assert result.success is False
        assert result.error is not None


# ── 추가 엣지 케이스 ────────────────────────────────────────────────────────


class TestReadFileEdgeCases:
    def test_unicode_content(self, tmp_path):
        f = tmp_path / "kor.txt"
        content = "안녕하세요\n반갑습니다"
        f.write_text(content, encoding="utf-8")

        result = read_file(str(f))

        assert result.success is True
        assert "1: 안녕하세요" in result.output
        assert "2: 반갑습니다" in result.output
        assert f"=== {f} [lines 1-2 of 2] ===" in result.output

    def test_binary_like_large_content(self, tmp_path):
        f = tmp_path / "big.txt"
        content = "x" * 10_000
        f.write_text(content, encoding="utf-8")

        result = read_file(str(f))

        assert result.success is True
        assert "1: " + content in result.output
        assert "[lines 1-1 of 1]" in result.output


class TestReadFileLinesEdgeCases:
    def test_range_beyond_file_length(self, tmp_path):
        f = tmp_path / "short.txt"
        f.write_text("only one line", encoding="utf-8")

        result = read_file_lines(str(f), start=1, end=999)

        assert result.success is True
        assert "only one line" in result.output

    def test_single_line_file(self, tmp_path):
        f = tmp_path / "single.txt"
        f.write_text("sole", encoding="utf-8")

        result = read_file_lines(str(f), start=1, end=1)

        assert result.success is True
        assert "sole" in result.output


class TestWriteFileEdgeCases:
    def test_unicode_content(self, tmp_path):
        path = str(tmp_path / "kor.py")
        result = write_file(path, "# 한국어 주석\nprint('안녕')\n")

        assert result.success is True
        assert "안녕" in (tmp_path / "kor.py").read_text(encoding="utf-8")

    def test_write_empty_string(self, tmp_path):
        path = str(tmp_path / "empty.txt")
        result = write_file(path, "")

        assert result.success is True
        assert (tmp_path / "empty.txt").read_text(encoding="utf-8") == ""


class TestEditFileEdgeCases:
    def test_replace_with_empty_string_deletes_substring(self, tmp_path):
        f = tmp_path / "del.py"
        f.write_text("foo = bar + baz\n", encoding="utf-8")

        result = edit_file(str(f), old_str=" + baz", new_str="")

        assert result.success is True
        assert f.read_text(encoding="utf-8") == "foo = bar\n"

    def test_multiline_replacement(self, tmp_path):
        f = tmp_path / "multi.py"
        f.write_text("def old():\n    return 1\n", encoding="utf-8")

        result = edit_file(
            str(f),
            old_str="def old():\n    return 1",
            new_str="def new():\n    return 2",
        )

        assert result.success is True
        content = f.read_text(encoding="utf-8")
        assert "def new()" in content
        assert "def old()" not in content

    def test_unicode_replacement(self, tmp_path):
        f = tmp_path / "kor.py"
        f.write_text("# 구버전 주석\n", encoding="utf-8")

        result = edit_file(str(f), old_str="구버전", new_str="신버전")

        assert result.success is True
        assert "신버전" in f.read_text(encoding="utf-8")


class TestSearchInFileEdgeCases:
    def test_empty_file_no_match(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("", encoding="utf-8")

        result = search_in_file(str(f), pattern="anything")

        assert result.success is True
        assert "매칭 없음" in result.output

    def test_invalid_regex_returns_error(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\n", encoding="utf-8")

        result = search_in_file(str(f), pattern="[invalid(")

        assert result.success is False
        assert result.error is not None

    def test_multiple_matches_returns_all(self, tmp_path):
        f = tmp_path / "dup.py"
        f.write_text("foo\nfoo\nfoo\n", encoding="utf-8")

        result = search_in_file(str(f), pattern="foo")

        assert result.success is True
        assert result.output.count("foo") == 3


# ── hashline 테스트 공통 헬퍼 ────────────────────────────────────────────────


def _find_collision_pair() -> tuple[str, str]:
    """같은 _line_hash를 가진 서로 다른 두 문자열을 찾는다.
    2자 base32 해시(1024 버킷)이므로 ~32회 내에 충돌 발생.
    """
    seen: dict[str, str] = {}
    for i in range(100_000):
        s = f"synthetic_collision_line_{i}"
        h = _line_hash(s)
        if h in seen:
            return seen[h], s
        seen[h] = s
    raise RuntimeError("충돌 쌍을 찾지 못함")


# ── hashline_read ─────────────────────────────────────────────────────────────


class TestHashlineReadFormat:
    def test_hashline_read_format(self, tmp_path):
        """줄번호 zero-padding, 해시 길이, separator 형식 검증."""
        import re
        f = tmp_path / "fmt.py"
        f.write_text("def foo():\n    return 1\n    pass\n", encoding="utf-8")

        result = hashline_read(str(f))

        assert result.success is True
        lines_out = result.output.splitlines()
        # 각 줄: 3자리 번호 # 2자리 해시 | 공백 내용
        pattern = re.compile(r"^\d{3}#[A-Z2-7]{3}\| .*$")
        for line in lines_out:
            assert pattern.match(line), f"형식 불일치: {line!r}"
        # 첫 줄은 001
        assert lines_out[0].startswith("001#")
        # 파일이 3줄이면 pad_width=3
        assert lines_out[2].startswith("003#")

    def test_hashline_read_empty_file(self, tmp_path):
        """빈 파일은 빈 문자열 반환 (에러 아님)."""
        f = tmp_path / "empty.py"
        f.write_text("", encoding="utf-8")

        result = hashline_read(str(f))

        assert result.success is True
        assert result.output == ""

    def test_hashline_read_range(self, tmp_path):
        """start_line / end_line 옵션이 올바르게 슬라이싱된다."""
        f = tmp_path / "range.txt"
        f.write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n", encoding="utf-8")

        result = hashline_read(str(f), start_line=3, end_line=6)

        assert result.success is True
        out = result.output
        # 줄 3-6만 포함
        assert "003#" in out
        assert "006#" in out
        assert "002#" not in out
        assert "007#" not in out

    def test_hashline_read_collision_handling(self, tmp_path):
        """동일 base hash를 가진 줄도 줄번호(LLL)로 고유한 line_ref를 갖는다."""
        line_a, line_b = _find_collision_pair()
        # 충돌하는 두 줄이 같은 base hash를 가짐을 전제
        assert _line_hash(line_a) == _line_hash(line_b)

        f = tmp_path / "collide.txt"
        f.write_text(f"{line_a}\n{line_b}\nother_line\n", encoding="utf-8")

        result = hashline_read(str(f))

        assert result.success is True
        lines_out = result.output.splitlines()

        # suffix 없이 줄번호로 구분됨: 001#HHH| 와 002#HHH| (동일 해시지만 LLL이 다름)
        base_h = _line_hash(line_a)
        assert f"001#{base_h}|" in lines_out[0]
        assert f"002#{base_h}|" in lines_out[1]  # suffix 없음

        # 모든 line_ref가 고유한지 확인 (줄번호 덕분에 suffix 없이도 고유)
        refs = [l.split("|")[0].strip() for l in lines_out]
        assert len(refs) == len(set(refs)), "line_ref 중복 발생"

    def test_hashline_read_large_file_warning(self, tmp_path):
        """1000라인 초과 시 첫 줄에 WARNING이 prepend된다."""
        f = tmp_path / "large.txt"
        content = "\n".join(f"line_{i}" for i in range(1001)) + "\n"
        f.write_text(content, encoding="utf-8")

        result = hashline_read(str(f))

        assert result.success is True
        first_line = result.output.splitlines()[0]
        assert first_line.startswith("# WARNING")
        assert "1001" in first_line

    def test_hashline_read_missing_file(self, tmp_path):
        result = hashline_read(str(tmp_path / "ghost.py"))

        assert result.success is False
        assert result.error is not None

    def test_hashline_read_hash_deterministic(self):
        assert _line_hash("same") == _line_hash("same")
        assert _line_hash("") == _line_hash("")

    def test_hashline_read_hash_length(self):
        for line in ["", "x", "    indented", "\ttab", "한글"]:
            h = _line_hash(line)
            assert len(h) == 3, f"해시 길이 != 3: {h!r} for {line!r}"

    def test_hashline_read_separator_is_pipe_space(self, tmp_path):
        """separator는 항상 파이프+공백('| ')이어야 한다."""
        f = tmp_path / "sep.txt"
        f.write_text("hello\n", encoding="utf-8")

        result = hashline_read(str(f))

        line = result.output.splitlines()[0]
        # 형식: 001#HH| hello
        tag, content = line.split("| ", 1)
        assert tag.startswith("001#")
        assert content == "hello"


# ── hashline_edit ─────────────────────────────────────────────────────────────


class TestHashlineEditBasic:
    def test_hashline_edit_replace_success(self, tmp_path):
        """정상 replace 편집."""
        f = tmp_path / "code.py"
        f.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
        table = _build_hashline_table(["x = 1", "y = 2", "z = 3"])
        ref_y = table[1][0]  # "002#HH"

        result = hashline_edit(str(f), [{"action": "replace", "line_ref": ref_y, "new_content": "y = 99"}])

        assert result.success is True
        assert f.read_text(encoding="utf-8").splitlines()[1] == "y = 99"

    def test_hashline_edit_atomic_rejection(self, tmp_path):
        """5개 edit 중 3번째가 mismatch면 1, 2번도 적용되지 않는다."""
        lines = ["alpha", "beta", "gamma", "delta", "epsilon"]
        f = tmp_path / "atomic.txt"
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        original_content = f.read_text(encoding="utf-8")

        table = _build_hashline_table(lines)
        # 3번째 ref를 의도적으로 잘못된 해시로 변조
        bad_ref = table[2][0].split("#")[0] + "#ZZ"

        edits = [
            {"action": "replace", "line_ref": table[0][0], "new_content": "EDIT1"},
            {"action": "replace", "line_ref": table[1][0], "new_content": "EDIT2"},
            {"action": "replace", "line_ref": bad_ref,     "new_content": "EDIT3"},
            {"action": "replace", "line_ref": table[3][0], "new_content": "EDIT4"},
            {"action": "replace", "line_ref": table[4][0], "new_content": "EDIT5"},
        ]

        result = hashline_edit(str(f), edits)

        assert result.success is False
        # 파일은 변경 전 상태 그대로
        assert f.read_text(encoding="utf-8") == original_content

    def test_hashline_edit_hash_mismatch_returns_actual_hash(self, tmp_path):
        """HashMismatchError 메시지에 actual_hash와 recovery 지침이 포함된다."""
        f = tmp_path / "mm.txt"
        f.write_text("foo\nbar\n", encoding="utf-8")

        # 줄 1은 존재하지만 해시가 틀린 ref
        bad_ref = "001#ZZ"
        result = hashline_edit(str(f), [{"action": "replace", "line_ref": bad_ref, "new_content": "x"}])

        assert result.success is False
        assert "actual_hash" in result.error
        assert "hashline_read" in result.error

    def test_hashline_edit_line_not_found(self, tmp_path):
        """존재하지 않는 줄 번호의 line_ref."""
        f = tmp_path / "short.txt"
        f.write_text("only_one_line\n", encoding="utf-8")

        result = hashline_edit(str(f), [{"action": "replace", "line_ref": "099#AB", "new_content": "x"}])

        assert result.success is False
        assert "LineNotFoundError" in result.error

    def test_hashline_edit_insert_actions(self, tmp_path):
        """insert_before / insert_after 동작 검증."""
        f = tmp_path / "insert.txt"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        table = _build_hashline_table(["line1", "line2", "line3"])
        ref2 = table[1][0]  # line2

        # insert_after line2
        result = hashline_edit(str(f), [{"action": "insert_after", "line_ref": ref2, "content": "inserted_after"}])

        assert result.success is True
        content_lines = f.read_text(encoding="utf-8").splitlines()
        assert content_lines == ["line1", "line2", "inserted_after", "line3"]

        # insert_before (재구성)
        f.write_text("A\nB\nC\n", encoding="utf-8")
        table2 = _build_hashline_table(["A", "B", "C"])
        ref_b = table2[1][0]
        result2 = hashline_edit(str(f), [{"action": "insert_before", "line_ref": ref_b, "content": "BEFORE_B"}])

        assert result2.success is True
        assert f.read_text(encoding="utf-8").splitlines() == ["A", "BEFORE_B", "B", "C"]

    def test_hashline_edit_returns_next_state(self, tmp_path):
        """적용 후 영향 구간 ±2줄의 새 hashline 표현이 반환된다."""
        f = tmp_path / "ns.txt"
        lines = [f"line{i}" for i in range(1, 8)]
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        table = _build_hashline_table(lines)
        ref4 = table[3][0]  # line4 (index 3)

        result = hashline_edit(str(f), [{"action": "replace", "line_ref": ref4, "new_content": "REPLACED"}])

        assert result.success is True
        assert result.output.startswith("applied 1 edit. new state:")
        # next_state 부분
        next_state = result.output.split("new state:\n", 1)[1]
        # REPLACED 가 새 해시와 함께 등장해야 함
        assert "REPLACED" in next_state
        # 해시 형식 포함
        assert "#" in next_state and "| " in next_state

    def test_hashline_edit_concurrent_modification_detected(self, tmp_path):
        """hashline_read 후 파일이 변경되면 이전 ref로 edit 시 거부된다."""
        f = tmp_path / "concurrent.txt"
        f.write_text("original_line1\noriginal_line2\n", encoding="utf-8")

        # 1) read
        read_result = hashline_read(str(f))
        assert read_result.success is True
        first_line = read_result.output.splitlines()[0]
        stale_ref = first_line.split("| ")[0]  # "001#HH"

        # 2) 파일을 직접 수정 (concurrent write)
        f.write_text("CHANGED_line1\noriginal_line2\n", encoding="utf-8")

        # 3) 이전 ref로 edit 시도 → HashMismatchError
        result = hashline_edit(str(f), [{"action": "replace", "line_ref": stale_ref, "new_content": "oops"}])

        assert result.success is False
        assert "HashMismatchError" in result.error

    def test_hashline_edit_duplicate_line_ref_rejected(self, tmp_path):
        """같은 line_ref에 2개 이상의 action이 있으면 거부된다."""
        f = tmp_path / "dup.txt"
        f.write_text("only\n", encoding="utf-8")
        table = _build_hashline_table(["only"])
        ref = table[0][0]

        result = hashline_edit(str(f), [
            {"action": "replace", "line_ref": ref, "new_content": "first"},
            {"action": "replace", "line_ref": ref, "new_content": "second"},
        ])

        assert result.success is False
        assert "중복" in result.error

    def test_hashline_edit_missing_file(self, tmp_path):
        result = hashline_edit(str(tmp_path / "no.txt"), [{"action": "replace", "line_ref": "001#AB", "new_content": "x"}])

        assert result.success is False
        assert result.error is not None

    def test_hashline_edit_delete_action(self, tmp_path):
        """delete action으로 줄을 삭제한다."""
        f = tmp_path / "del.txt"
        f.write_text("keep\ndelete_me\nkeep_too\n", encoding="utf-8")
        table = _build_hashline_table(["keep", "delete_me", "keep_too"])
        ref = table[1][0]

        result = hashline_edit(str(f), [{"action": "delete", "line_ref": ref}])

        assert result.success is True
        assert f.read_text(encoding="utf-8").splitlines() == ["keep", "keep_too"]


# ── 역할 할당 통합 테스트 ─────────────────────────────────────────────────────


class TestHashlineRoleAssignment:
    def test_implementer_role_has_hashline_tools(self):
        from agents.roles import IMPLEMENTER
        assert "hashline_read" in IMPLEMENTER.allowed_tools
        assert "hashline_edit" in IMPLEMENTER.allowed_tools

    def test_test_writer_role_has_hashline_tools(self):
        from agents.roles import TEST_WRITER
        assert "hashline_read" in TEST_WRITER.allowed_tools
        assert "hashline_edit" in TEST_WRITER.allowed_tools

    def test_reviewer_role_does_not_have_hashline_edit(self):
        from agents.roles import REVIEWER
        assert "hashline_edit" not in REVIEWER.allowed_tools
        # hashline_read는 읽기 전용이므로 REVIEWER에게 허용됨
        assert "hashline_read" in REVIEWER.allowed_tools
