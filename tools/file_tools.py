# tools/file_tools.py

import os
import re
from pathlib import Path
from .schemas import ToolResult

# ── 읽기 ──────────────────────────────────────────


def read_file(path: str, start: int | None = None, end: int | None = None) -> ToolResult:
    """파일 내용 읽기. start/end(1-indexed 줄 번호)를 지정하면 해당 범위만 반환한다."""
    try:
        content = Path(path).read_text(encoding="utf-8")
        if start is not None or end is not None:
            lines = content.splitlines()
            s = (start - 1) if start is not None else 0
            e = end if end is not None else len(lines)
            sliced = lines[s:e]
            content = "\n".join(f"{i+s+1}: {line}" for i, line in enumerate(sliced))
        return ToolResult(success=True, output=content)
    except FileNotFoundError:
        return ToolResult(success=False, output="", error=f"파일 없음: {path}")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


def read_file_lines(path: str, start: int, end: int) -> ToolResult:
    """특정 줄 범위만 읽기 (대용량 파일 대응)"""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        sliced = lines[start - 1 : end]  # 1-indexed
        numbered = [f"{i+start}: {line}" for i, line in enumerate(sliced)]
        return ToolResult(success=True, output="\n".join(numbered))
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


def list_directory(path: str = ".", recursive: bool = False) -> ToolResult:
    """디렉토리 구조 출력"""
    try:
        p = Path(path)
        if recursive:
            entries = [str(f.relative_to(p)) for f in p.rglob("*")]
        else:
            entries = [
                f.name + ("/" if f.is_dir() else "") for f in sorted(p.iterdir())
            ]
        return ToolResult(success=True, output="\n".join(entries))
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


# ── 검색 ──────────────────────────────────────────


def search_in_file(path: str, pattern: str) -> ToolResult:
    """파일 내 패턴 검색 (grep 역할)"""
    try:
        results = []
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line):
                results.append(f"{i}: {line}")
        output = "\n".join(results) if results else "매칭 없음"
        return ToolResult(success=True, output=output)
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


def search_files(directory: str, pattern: str, file_ext: str = "") -> ToolResult:
    """디렉토리 전체에서 패턴 검색"""
    try:
        results = []
        glob = f"**/*{file_ext}" if file_ext else "**/*"
        for file_path in Path(directory).rglob(glob):
            if not file_path.is_file():
                continue
            try:
                for i, line in enumerate(
                    file_path.read_text(encoding="utf-8").splitlines(), 1
                ):
                    if re.search(pattern, line):
                        results.append(f"{file_path}:{i}: {line.strip()}")
            except (UnicodeDecodeError, PermissionError):
                continue  # 바이너리/권한 없는 파일 skip
        return ToolResult(success=True, output="\n".join(results) or "매칭 없음")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


# ── 쓰기 ──────────────────────────────────────────


def write_file(path: str, content: str) -> ToolResult:
    """파일 생성 또는 덮어쓰기"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return ToolResult(success=True, output=f"저장 완료: {path}")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


def edit_file(path: str, old_str: str, new_str: str) -> ToolResult:
    """
    특정 문자열만 교체 (전체 덮어쓰기 방지)

    에이전트가 파일 전체를 재생성하지 않고
    정확한 위치만 수정할 수 있어서 토큰 절약 + 안전
    """
    try:
        content = Path(path).read_text(encoding="utf-8")
        if old_str not in content:
            return ToolResult(success=False, output="", error="old_str을 찾을 수 없음")
        count = content.count(old_str)
        if count > 1:
            return ToolResult(
                success=False,
                output="",
                error=f"old_str이 {count}곳에 있음. 더 구체적으로 지정하세요",
            )
        new_content = content.replace(old_str, new_str, 1)
        Path(path).write_text(new_content, encoding="utf-8")
        return ToolResult(success=True, output=f"수정 완료: {path}")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


def append_to_file(path: str, content: str) -> ToolResult:
    """파일 끝에 내용 추가"""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(success=True, output=f"추가 완료: {path}")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))
