# tools/file_tools.py

import base64
import hashlib
import os
import re
import shutil
from pathlib import Path
from .schemas import ToolResult

# 삭제로부터 보호할 디렉토리 이름 (워크스페이스 최상위 기준)
_PROTECTED_DIRS = {"context"}


def _resolve_within_workspace(path: str) -> tuple[Path, Path]:
    """
    path를 현재 작업 디렉토리(워크스페이스 루트) 기준으로 resolve하고,
    워크스페이스 경계를 벗어나는지 검증한다.

    Returns:
        (resolved_path, workspace_root) 튜플

    Raises:
        ValueError: 경계 이탈 또는 보호 경로 접근 시
    """
    workspace = Path.cwd().resolve()
    resolved = (workspace / path).resolve()

    if not str(resolved).startswith(str(workspace)):
        raise ValueError(f"워크스페이스 경계 이탈: {path!r}")

    # 보호 디렉토리 검사 (워크스페이스 루트 직속 또는 하위)
    relative = resolved.relative_to(workspace)
    top = relative.parts[0] if relative.parts else ""
    if top in _PROTECTED_DIRS:
        raise ValueError(f"보호 경로는 삭제할 수 없습니다: {top}/")

    return resolved, workspace

# ── 해시 헬퍼 ────────────────────────────────────


def _line_hash(line: str) -> str:
    """줄 내용의 3자 대문자 콘텐츠 해시 (shake_128 2바이트 → base32 앞 3자)."""
    digest = hashlib.shake_128(line.encode("utf-8")).digest(2)
    return base64.b32encode(digest).decode("ascii").rstrip("=")[:3]


def _build_hashline_table(lines: list[str]) -> list[tuple[str, str]]:
    """각 줄에 line_ref (LLL#HHH)를 부여한다. 줄번호가 고유성을 보장하므로 suffix 없음."""
    pad_width = max(3, len(str(len(lines))))
    return [
        (f"{str(i).zfill(pad_width)}#{_line_hash(line)}", line)
        for i, line in enumerate(lines, 1)
    ]


# ── 예외 타입 ─────────────────────────────────────


class HashMismatchError(Exception):
    """line_ref의 라인 번호는 존재하지만 해시가 현재 내용과 다름 (stale ref)."""

    def __init__(self, ref: str, actual_hash: str, current_line: str, recovery: str):
        self.ref = ref
        self.actual_hash = actual_hash
        self.current_line = current_line
        self.recovery = recovery
        super().__init__(
            f"HashMismatchError: ref='{ref}' actual_hash='{actual_hash}' "
            f"current_line={current_line!r} recovery='{recovery}'"
        )


class LineNotFoundError(Exception):
    """line_ref의 라인 번호가 파일 범위를 벗어남."""

    def __init__(self, ref: str, reason: str):
        self.ref = ref
        self.reason = reason
        super().__init__(f"LineNotFoundError: ref='{ref}' reason='{reason}'")


# ── 읽기 ──────────────────────────────────────────


def read_file(
    path: str,
    start: int | None = None,
    end: int | None = None,
    max_lines: int = 150,
) -> ToolResult:
    """
    파일을 줄 단위로 페이지네이션하여 읽는다 (1-indexed, end 포함).

    - start, end 둘 다 지정 → 해당 범위
    - start만 지정 → start부터 start + max_lines - 1 까지
    - end만 지정 → 1부터 end 까지
    - 둘 다 None → 1부터 max_lines 까지 (초과 시 경고 헤더)

    출력은 항상 `=== {path} [lines s-e of total] ===` 헤더 뒤에 줄 번호 prefix된 본문.
    """
    if max_lines <= 0:
        raise ValueError("max_lines must be > 0")
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        total = len(lines)

        if total == 0:
            return ToolResult(success=True, output=f"=== {path} [empty file] ===")

        warnings: list[str] = []

        if start is not None and end is not None:
            s, e = start, end
        elif start is not None:
            s, e = start, start + max_lines - 1
        elif end is not None:
            s, e = 1, end
        else:
            s, e = 1, min(max_lines, total)
            if total > max_lines:
                warnings.append(
                    f"⚠️ File has {total} lines. Showing lines 1-{max_lines}. "
                    f"Call read_file(path, start=..., end=...) for the rest."
                )

        if s < 1:
            return ToolResult(success=False, output="", error="start must be >= 1")
        if s > total:
            return ToolResult(
                success=False,
                output="",
                error=f"start exceeds file length ({total} lines)",
            )
        if s > e:
            return ToolResult(success=False, output="", error="invalid range")

        if e > total:
            warnings.append(f"⚠️ end={e} clamped to {total} (file end).")
            e = total

        sliced = lines[s - 1 : e]
        numbered = "\n".join(f"{i + s}: {line}" for i, line in enumerate(sliced))
        header = f"=== {path} [lines {s}-{e} of {total}] ==="
        output = "\n".join(warnings + [header, numbered])
        return ToolResult(success=True, output=output)
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


def hashline_read(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> ToolResult:
    """
    파일을 읽되 각 줄 앞에 `LLL#HH|` 태그를 붙여 반환한다.

    출력 형식: 001#VK| def calculate(a, b):
    - LLL: 파일 라인 수 기준 zero-padded 번호 (최소 3자리)
    - HH: 2자 대문자 콘텐츠 해시 (충돌 시 suffix: HH2, HH3, …)
    - | : 고정 separator (파이프 + 공백)

    파일이 1000라인 초과 또는 100KB 초과 시 첫 줄에 경고 prepend.
    end_line=None 이면 끝까지 읽는다.
    """
    try:
        p = Path(path)
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            return ToolResult(success=False, output="", error=f"파일 없음: {path}")

        try:
            file_text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            return ToolResult(
                success=False,
                output="",
                error=f"UTF-8 디코딩 실패: {path} — {exc.reason} (position {exc.start})",
            )

        lines = file_text.splitlines()
        total = len(lines)

        if total == 0:
            return ToolResult(success=True, output="")

        # Large file warning
        warnings: list[str] = []
        if total > 1000 or len(raw) > 100 * 1024:
            warnings.append(
                f"# WARNING: large file ({total} lines). "
                "Use read_file if you only need to read."
            )

        # Range validation
        s = start_line
        e = end_line if end_line is not None else total

        if s < 1:
            return ToolResult(success=False, output="", error="start_line must be >= 1")
        if s > total:
            return ToolResult(
                success=False,
                output="",
                error=f"start_line {s} exceeds file length ({total} lines)",
            )
        if s > e:
            return ToolResult(success=False, output="", error="start_line > end_line")
        e = min(e, total)

        # Build full table first (collision counters need the full file)
        table = _build_hashline_table(lines)
        sliced = table[s - 1 : e]
        formatted = [f"{ref}| {line}" for ref, line in sliced]

        return ToolResult(success=True, output="\n".join(warnings + formatted))
    except Exception as exc:
        return ToolResult(success=False, output="", error=str(exc))


def hashline_edit(path: str, edits: list[dict]) -> ToolResult:
    """
    edits 목록을 파일에 원자적으로 적용한다.

    edits 형식:
        {"action": "replace",       "line_ref": "022#XJ", "new_content": "..."}
        {"action": "delete",        "line_ref": "022#XJ"}
        {"action": "insert_after",  "line_ref": "022#XJ", "content": "..."}
        {"action": "insert_before", "line_ref": "022#XJ", "content": "..."}

    반환:
        성공: "applied N edits. new state:\\n" + 영향 구간 ±2줄의 새 hashline 표현
        실패: HashMismatchError 또는 LineNotFoundError 메시지 (파일 무변경)
    """
    try:
        p = Path(path)
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            return ToolResult(success=False, output="", error=f"파일 없음: {path}")

        try:
            file_text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            return ToolResult(
                success=False,
                output="",
                error=f"UTF-8 디코딩 실패: {path} — {exc.reason}",
            )

        lines = file_text.splitlines()
        total = len(lines)
        ends_with_newline = file_text.endswith("\n")

        # Build full hashline table
        table = _build_hashline_table(lines)
        ref_to_idx: dict[str, int] = {ref: i for i, (ref, _) in enumerate(table)}

        # ── 검증 단계 ──────────────────────────────────────────────────────────
        # 중복 line_ref 검사
        seen_refs: set[str] = set()
        for edit in edits:
            ref = edit.get("line_ref", "")
            if ref in seen_refs:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"중복 line_ref '{ref}': 동일 ref에 2개 이상의 action 불가",
                )
            seen_refs.add(ref)

        # 각 edit의 line_ref 유효성 검사
        validated: list[tuple[str, int, dict]] = []  # (action, 0-based-idx, edit)
        for edit in edits:
            action = edit.get("action", "")
            ref = edit.get("line_ref", "")

            if ref not in ref_to_idx:
                # 라인 번호가 존재하는지 판별
                try:
                    line_num = int(ref.split("#")[0])
                except (ValueError, IndexError, AttributeError):
                    line_num = -1

                if 1 <= line_num <= total:
                    actual_ref, _ = table[line_num - 1]
                    actual_hash = actual_ref.split("#")[1]
                    raise HashMismatchError(
                        ref=ref,
                        actual_hash=actual_hash,
                        current_line=lines[line_num - 1],
                        recovery="call hashline_read again to refresh",
                    )
                else:
                    raise LineNotFoundError(
                        ref=ref,
                        reason=f"line {line_num} does not exist",
                    )

            validated.append((action, ref_to_idx[ref], edit))

        # ── 적용 단계 (아래부터 위 순서로 — 인덱스 보존) ─────────────────────
        validated.sort(key=lambda x: x[1], reverse=True)
        lines_mut = list(lines)
        affected_original: list[int] = [idx for _, idx, _ in validated]

        for action, idx, edit in validated:
            if action == "replace":
                lines_mut[idx] = edit.get("new_content", "")
            elif action == "delete":
                del lines_mut[idx]
            elif action == "insert_after":
                lines_mut.insert(idx + 1, edit.get("content", ""))
            elif action == "insert_before":
                lines_mut.insert(idx, edit.get("content", ""))
            else:
                return ToolResult(
                    success=False, output="", error=f"알 수 없는 action: {action!r}"
                )

        # 파일 저장
        if lines_mut:
            new_text = "\n".join(lines_mut)
            if ends_with_newline:
                new_text += "\n"
        else:
            new_text = ""
        p.write_text(new_text, encoding="utf-8")

        # ── next_state: 영향 구간 ±2줄 ────────────────────────────────────────
        new_lines = new_text.splitlines()
        new_table = _build_hashline_table(new_lines)

        if affected_original and new_lines:
            lo = max(0, min(affected_original) - 2)
            hi = min(len(new_lines) - 1, max(affected_original) + 2)
            next_state = "\n".join(
                f"{ref}| {line}" for ref, line in new_table[lo : hi + 1]
            )
        else:
            next_state = ""

        n = len(edits)
        output = f"applied {n} edit{'s' if n != 1 else ''}. new state:\n{next_state}"
        return ToolResult(success=True, output=output)

    except (HashMismatchError, LineNotFoundError) as exc:
        return ToolResult(success=False, output="", error=str(exc))
    except Exception as exc:
        return ToolResult(success=False, output="", error=str(exc))


def append_to_file(path: str, content: str) -> ToolResult:
    """파일 끝에 내용 추가"""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(success=True, output=f"추가 완료: {path}")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


# ── 삭제 ──────────────────────────────────────────


def delete_file(path: str) -> ToolResult:
    """
    파일 삭제. 워크스페이스(현재 작업 디렉토리) 내부 파일만 삭제 가능.
    context/ 디렉토리는 보호되어 삭제 불가.
    """
    try:
        resolved, _ = _resolve_within_workspace(path)
        if not resolved.exists():
            return ToolResult(success=False, output="", error=f"파일 없음: {path}")
        if resolved.is_dir():
            return ToolResult(success=False, output="", error=f"디렉토리입니다. delete_directory를 사용하세요: {path}")
        resolved.unlink()
        return ToolResult(success=True, output=f"삭제 완료: {path}")
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


def delete_directory(path: str) -> ToolResult:
    """
    디렉토리와 하위 내용을 모두 삭제. 워크스페이스 내부만 허용.
    context/ 디렉토리는 보호되어 삭제 불가.
    워크스페이스 루트 자체는 삭제 불가.
    """
    try:
        resolved, workspace = _resolve_within_workspace(path)
        if resolved == workspace:
            return ToolResult(success=False, output="", error="워크스페이스 루트는 삭제할 수 없습니다.")
        if not resolved.exists():
            return ToolResult(success=False, output="", error=f"디렉토리 없음: {path}")
        if not resolved.is_dir():
            return ToolResult(success=False, output="", error=f"파일입니다. delete_file을 사용하세요: {path}")
        shutil.rmtree(resolved)
        return ToolResult(success=True, output=f"삭제 완료: {path}")
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))
