"""
tools/git_tools.py — Git 작업 도구

functions:
  git_status(repo_path)          — 워킹 트리 상태
  git_diff(repo_path, staged)    — 변경 diff
  git_log(repo_path, n)          — 최근 커밋 로그
  git_add(repo_path, paths)      — 파일 스테이징
  git_commit(repo_path, message) — 커밋
  analyze_uncommitted_changes    — 미커밋 변경 분석
  propose_commit_groups          — 변경을 커밋 그룹으로 제안
  stage_group                    — 명시 경로만 안전하게 stage
  commit_group                   — 기대 staged 파일 검증 후 commit

모두 ToolResult 반환.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.schemas import ToolResult


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    """git 명령어를 실행하고 결과를 반환합니다."""
    return subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _normalize_repo_path(repo_path: str) -> str:
    return repo_path or "."


def _is_git_repo_error(stderr: str) -> bool:
    """git 저장소가 아닌 경우의 에러인지 확인합니다."""
    return "not a git repository" in stderr.lower()


def _parse_status_lines(repo_path: str) -> tuple[list[dict[str, str]], ToolResult | None]:
    result = _run_git(
        ["status", "--short", "--untracked-files=all"],
        cwd=repo_path,
    )
    if result.returncode != 0:
        if _is_git_repo_error(result.stderr):
            return [], ToolResult(
                success=False,
                output="",
                error=f"git 저장소가 아닙니다: {repo_path}",
            )
        return [], ToolResult(
            success=False,
            output="",
            error=result.stderr.strip() or "git status 실패",
        )

    entries: list[dict[str, str]] = []
    for raw_line in result.stdout.splitlines():
        if not raw_line:
            continue
        index_status = raw_line[0]
        worktree_status = raw_line[1]
        path_text = raw_line[3:].strip()
        old_path = ""
        path = path_text
        if " -> " in path_text:
            old_path, path = path_text.split(" -> ", 1)
        entries.append({
            "index_status": index_status,
            "worktree_status": worktree_status,
            "path": path,
            "old_path": old_path,
            "raw": raw_line,
        })
    return entries, None


def _changed_paths(repo_path: str) -> tuple[set[str], ToolResult | None]:
    entries, error = _parse_status_lines(repo_path)
    if error is not None:
        return set(), error
    return {entry["path"] for entry in entries}, None


def _staged_paths(repo_path: str) -> tuple[list[str], ToolResult | None]:
    entries, error = _parse_status_lines(repo_path)
    if error is not None:
        return [], error
    staged = sorted(
        entry["path"]
        for entry in entries
        if entry["index_status"] not in {" ", "?"}
    )
    return staged, None


def _has_head(repo_path: str) -> bool:
    result = _run_git(["rev-parse", "--verify", "HEAD"], cwd=repo_path)
    return result.returncode == 0


def _unstage_all(repo_path: str) -> ToolResult:
    staged_paths, error = _staged_paths(repo_path)
    if error is not None:
        return error
    if not staged_paths:
        return ToolResult(success=True, output="staged 상태가 이미 비어 있습니다.")

    if _has_head(repo_path):
        result = _run_git(["restore", "--staged", "--"] + staged_paths, cwd=repo_path)
    else:
        result = _run_git(["rm", "--cached", "-r", "--quiet", "--"] + staged_paths, cwd=repo_path)

    if result.returncode != 0:
        if _is_git_repo_error(result.stderr):
            return ToolResult(
                success=False,
                output="",
                error=f"git 저장소가 아닙니다: {repo_path}",
            )
        return ToolResult(
            success=False,
            output="",
            error=result.stderr.strip() or result.stdout.strip() or "staged 초기화 실패",
        )

    return ToolResult(success=True, output="\n".join(staged_paths))


def _top_level_area(path: str) -> str:
    parts = Path(path).parts
    if not parts:
        return "misc"
    top = parts[0]
    if top == "tests":
        stem = Path(path).stem
        if stem.startswith("test_"):
            stem = stem[5:]
        token = stem.split("_", 1)[0]
        return token or "tests"
    if len(parts) == 1:
        return Path(path).stem or top
    return top


def _suggest_commit_message(area: str, paths: list[str]) -> str:
    if len(paths) == 1:
        return f"Update {paths[0]}"
    return f"Update {area} changes"


def git_status(repo_path: str = ".") -> ToolResult:
    """워킹 트리 상태를 반환합니다."""
    repo_path = _normalize_repo_path(repo_path)
    p = Path(repo_path)
    if not p.exists():
        return ToolResult(success=False, output="", error=f"경로가 존재하지 않습니다: {repo_path}")

    result = _run_git(["status"], cwd=repo_path)

    if result.returncode != 0:
        if _is_git_repo_error(result.stderr):
            return ToolResult(
                success=False,
                output="",
                error=f"git 저장소가 아닙니다: {repo_path}",
            )
        return ToolResult(
            success=False,
            output="",
            error=result.stderr.strip() or "git status 실패",
        )

    return ToolResult(success=True, output=result.stdout.strip())


def git_diff(repo_path: str = ".", staged: bool = False) -> ToolResult:
    """변경 사항의 diff를 반환합니다."""
    repo_path = _normalize_repo_path(repo_path)
    p = Path(repo_path)
    if not p.exists():
        return ToolResult(success=False, output="", error=f"경로가 존재하지 않습니다: {repo_path}")

    args = ["diff"]
    if staged:
        args.append("--staged")

    result = _run_git(args, cwd=repo_path)

    if result.returncode != 0:
        if _is_git_repo_error(result.stderr):
            return ToolResult(
                success=False,
                output="",
                error=f"git 저장소가 아닙니다: {repo_path}",
            )
        return ToolResult(
            success=False,
            output="",
            error=result.stderr.strip() or "git diff 실패",
        )

    return ToolResult(success=True, output=result.stdout)


def git_log(repo_path: str = ".", n: int = 10) -> ToolResult:
    """최근 커밋 로그를 반환합니다."""
    repo_path = _normalize_repo_path(repo_path)
    p = Path(repo_path)
    if not p.exists():
        return ToolResult(success=False, output="", error=f"경로가 존재하지 않습니다: {repo_path}")

    result = _run_git(["log", f"-{n}", "--oneline"], cwd=repo_path)

    if result.returncode != 0:
        if _is_git_repo_error(result.stderr):
            return ToolResult(
                success=False,
                output="",
                error=f"git 저장소가 아닙니다: {repo_path}",
            )
        # 커밋이 없는 저장소는 에러로 반환 (빈 출력도 허용)
        return ToolResult(
            success=False,
            output="",
            error=result.stderr.strip() or "git log 실패",
        )

    return ToolResult(success=True, output=result.stdout.strip())


def git_add(repo_path: str = ".", paths: list[str] | None = None) -> ToolResult:
    """파일을 스테이징합니다."""
    repo_path = _normalize_repo_path(repo_path)
    paths = paths or []
    if not paths:
        return ToolResult(
            success=False,
            output="",
            error="추가할 파일 경로를 지정해야 합니다.",
        )

    p = Path(repo_path)
    if not p.exists():
        return ToolResult(success=False, output="", error=f"경로가 존재하지 않습니다: {repo_path}")

    result = _run_git(["add"] + paths, cwd=repo_path)

    if result.returncode != 0:
        if _is_git_repo_error(result.stderr):
            return ToolResult(
                success=False,
                output="",
                error=f"git 저장소가 아닙니다: {repo_path}",
            )
        return ToolResult(
            success=False,
            output="",
            error=result.stderr.strip() or "git add 실패",
        )

    return ToolResult(success=True, output=result.stdout.strip())


def git_commit(repo_path: str = ".", message: str = "") -> ToolResult:
    """스테이징된 변경사항을 커밋합니다."""
    repo_path = _normalize_repo_path(repo_path)
    if not message or not message.strip():
        return ToolResult(
            success=False,
            output="",
            error="커밋 메시지를 입력해야 합니다.",
        )

    p = Path(repo_path)
    if not p.exists():
        return ToolResult(success=False, output="", error=f"경로가 존재하지 않습니다: {repo_path}")

    result = _run_git(["commit", "-m", message], cwd=repo_path)

    if result.returncode != 0:
        if _is_git_repo_error(result.stderr):
            return ToolResult(
                success=False,
                output="",
                error=f"git 저장소가 아닙니다: {repo_path}",
            )
        return ToolResult(
            success=False,
            output="",
            error=result.stderr.strip() or result.stdout.strip() or "git commit 실패",
        )

    return ToolResult(success=True, output=result.stdout.strip())


def analyze_uncommitted_changes(repo_path: str = ".") -> ToolResult:
    """미커밋 변경의 상태/통계를 구조화하여 반환합니다."""
    repo_path = _normalize_repo_path(repo_path)
    entries, error = _parse_status_lines(repo_path)
    if error is not None:
        return error

    unstaged_stat = _run_git(["diff", "--stat"], cwd=repo_path)
    staged_stat = _run_git(["diff", "--staged", "--stat"], cwd=repo_path)

    payload = {
        "repo_path": str(Path(repo_path).resolve()),
        "total_changed_files": len(entries),
        "entries": entries,
        "unstaged_diff_stat": unstaged_stat.stdout.strip(),
        "staged_diff_stat": staged_stat.stdout.strip(),
    }
    return ToolResult(
        success=True,
        output=json.dumps(payload, ensure_ascii=False, indent=2),
    )


def propose_commit_groups(repo_path: str = ".", max_groups: int = 5) -> ToolResult:
    """변경 파일을 경로 기반 휴리스틱으로 커밋 그룹에 묶어 제안합니다."""
    repo_path = _normalize_repo_path(repo_path)
    entries, error = _parse_status_lines(repo_path)
    if error is not None:
        return error
    if not entries:
        return ToolResult(success=False, output="", error="미커밋 변경사항이 없습니다.")

    grouped: dict[str, list[dict[str, str]]] = {}
    for entry in entries:
        area = _top_level_area(entry["path"])
        grouped.setdefault(area, []).append(entry)

    ordered = sorted(
        grouped.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    if max_groups > 0 and len(ordered) > max_groups:
        kept = ordered[: max_groups - 1]
        merged = ordered[max_groups - 1 :]
        misc_entries: list[dict[str, str]] = []
        for _, values in merged:
            misc_entries.extend(values)
        ordered = kept + [("misc", misc_entries)]

    groups: list[dict[str, object]] = []
    for idx, (area, values) in enumerate(ordered, 1):
        paths = sorted(entry["path"] for entry in values)
        groups.append({
            "id": f"group-{idx}",
            "title": f"{area} 관련 변경",
            "rationale": f"상위 경로/테스트 연관성을 기준으로 '{area}' 영역 변경을 묶음",
            "paths": paths,
            "suggested_message": _suggest_commit_message(area, paths),
        })

    payload = {
        "repo_path": str(Path(repo_path).resolve()),
        "groups": groups,
    }
    return ToolResult(
        success=True,
        output=json.dumps(payload, ensure_ascii=False, indent=2),
    )


def stage_group(
    repo_path: str = ".",
    paths: list[str] | None = None,
    replace: bool = False,
) -> ToolResult:
    """명시된 변경 파일만 스테이징한다. 필요하면 기존 staged 상태를 먼저 비운다."""
    repo_path = _normalize_repo_path(repo_path)
    paths = paths or []
    if not paths:
        return ToolResult(success=False, output="", error="stage할 파일 경로를 지정해야 합니다.")
    if any(path.strip() in {".", ""} for path in paths):
        return ToolResult(
            success=False,
            output="",
            error="'.' 또는 빈 경로는 stage_group에서 허용되지 않습니다.",
        )

    changed, error = _changed_paths(repo_path)
    if error is not None:
        return error

    unknown = sorted(path for path in paths if path not in changed)
    if unknown:
        return ToolResult(
            success=False,
            output="",
            error=f"미커밋 변경에 없는 경로는 stage할 수 없습니다: {unknown}",
        )

    if replace:
        reset_result = _unstage_all(repo_path)
        if not reset_result.success:
            return reset_result

    return git_add(repo_path=repo_path, paths=paths)


def commit_group(
    repo_path: str = ".",
    message: str = "",
    expected_paths: list[str] | None = None,
) -> ToolResult:
    """현재 staged 파일이 기대 목록과 일치할 때만 commit한다."""
    repo_path = _normalize_repo_path(repo_path)
    expected_paths = expected_paths or []

    staged = _run_git(["diff", "--staged", "--name-only"], cwd=repo_path)
    if staged.returncode != 0:
        if _is_git_repo_error(staged.stderr):
            return ToolResult(
                success=False,
                output="",
                error=f"git 저장소가 아닙니다: {repo_path}",
            )
        return ToolResult(
            success=False,
            output="",
            error=staged.stderr.strip() or "staged 파일 목록 조회 실패",
        )

    staged_paths = [line.strip() for line in staged.stdout.splitlines() if line.strip()]
    if expected_paths:
        if sorted(staged_paths) != sorted(expected_paths):
            return ToolResult(
                success=False,
                output="",
                error=(
                    "staged 파일이 기대 경로와 다릅니다. "
                    f"expected={sorted(expected_paths)} actual={sorted(staged_paths)}. "
                    "staging 구성을 다시 잡으려면 stage_group(..., replace=True)을 사용하세요."
                ),
            )

    return git_commit(repo_path=repo_path, message=message)
