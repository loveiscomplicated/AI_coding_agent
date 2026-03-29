"""
tools/git_tools.py — Git 작업 도구

functions:
  git_status(repo_path)          — 워킹 트리 상태
  git_diff(repo_path, staged)    — 변경 diff
  git_log(repo_path, n)          — 최근 커밋 로그
  git_add(repo_path, paths)      — 파일 스테이징
  git_commit(repo_path, message) — 커밋

모두 ToolResult 반환.
"""

from __future__ import annotations

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


def _is_git_repo_error(stderr: str) -> bool:
    """git 저장소가 아닌 경우의 에러인지 확인합니다."""
    return "not a git repository" in stderr.lower()


def git_status(repo_path: str) -> ToolResult:
    """워킹 트리 상태를 반환합니다."""
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


def git_diff(repo_path: str, staged: bool = False) -> ToolResult:
    """변경 사항의 diff를 반환합니다."""
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


def git_log(repo_path: str, n: int = 10) -> ToolResult:
    """최근 커밋 로그를 반환합니다."""
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


def git_add(repo_path: str, paths: list[str]) -> ToolResult:
    """파일을 스테이징합니다."""
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


def git_commit(repo_path: str, message: str) -> ToolResult:
    """스테이징된 변경사항을 커밋합니다."""
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
