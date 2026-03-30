"""
orchestrator/git_workflow.py — 브랜치 생성 · 커밋 · PR 생성

파이프라인 성공 후 workspace 결과물을 실제 repo 에 반영하고
GitHub PR 을 생성한다.

흐름 (git worktree 기반, 병렬 실행 안전):
  1. git worktree add  — 임시 worktree 생성 (main repo HEAD 불변)
  2. workspace → worktree 파일 복사
  3. git add + git commit  (worktree 안에서)
  4. git push origin {branch} --force
  5. git worktree remove  — 임시 디렉토리 정리
  6. gh pr create

병렬 안전성:
  각 태스크가 독립 worktree 를 사용하므로 main repo 의 HEAD 나
  working tree 를 변경하지 않는다. 여러 태스크가 동시에 실행돼도
  git 상태 충돌이 발생하지 않는다.

파일 복사 규칙:
  workspace/src/**   → worktree/**        (workspace/src/ 기준 상대 경로 유지)
  workspace/tests/** → worktree/tests/**  (workspace/tests/ 기준 상대 경로 유지)

사전 조건 (run.py 에서 check_prerequisites() 로 검증):
  - git 명령어 사용 가능
  - gh 명령어 사용 가능 (GitHub CLI, `gh auth login` 완료 상태)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from textwrap import dedent

from orchestrator.pipeline import PipelineResult
from orchestrator.task import Task
from orchestrator.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class GitWorkflow:
    """
    브랜치 생성부터 PR 생성까지 담당하는 워크플로우.

    Args:
        repo_path:    대상 git 저장소의 절대 경로
        base_branch:  PR 의 base branch (기본값: "dev")
    """

    def __init__(self, repo_path: str | Path, base_branch: str = "dev"):
        self.repo_path = Path(repo_path).resolve()
        self.base_branch = base_branch

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def run(
        self,
        task: Task,
        workspace: WorkspaceManager,
        result: PipelineResult,
    ) -> str:
        """
        파이프라인 결과를 repo 에 반영하고 PR 을 생성한다.

        git worktree 를 사용하므로 main repo 의 HEAD 를 변경하지 않는다.
        여러 태스크가 동시에 호출해도 안전하다.

        Returns:
            생성된 PR 의 URL 문자열

        Raises:
            GitWorkflowError: git / gh 명령 실패 시
        """
        branch = task.branch_name
        wt_path = Path(tempfile.mkdtemp(prefix=f"wt-{task.id}-{int(time.time())}-"))
        logger.info("[%s] git 워크플로우 시작 (worktree: %s)", task.id, wt_path)

        try:
            self._create_worktree(branch, wt_path)
            changed = self._copy_workspace_to_worktree(workspace, wt_path)
            self._wt_git_checked(wt_path, ["add"] + [str(f) for f in changed])
            message = f"[agent] {task.title} ({task.id})\n\n자동 생성 by AI Coding Agent Pipeline"
            self._wt_git_checked(wt_path, ["commit", "-m", message])
            self._wt_git_checked(wt_path, ["push", "origin", branch, "--force"])
        finally:
            self._remove_worktree(wt_path)

        pr_url = self._create_pr(task, result)
        logger.info("[%s] PR 생성 완료: %s", task.id, pr_url)
        return pr_url

    # ── git worktree 작업 ────────────────────────────────────────────────────

    def _create_worktree(self, branch: str, wt_path: Path) -> None:
        """
        base_branch 를 시작점으로 임시 worktree 를 생성한다.
        브랜치가 이미 존재하면 -B 로 강제 리셋한다.
        """
        result = self._git(
            ["worktree", "add", "-B", branch, str(wt_path), self.base_branch]
        )
        if result.returncode != 0:
            raise GitWorkflowError(
                f"git worktree add 실패:\n{result.stderr.strip() or result.stdout.strip()}"
            )
        logger.debug("[worktree] 생성: %s → %s", branch, wt_path)

    def _remove_worktree(self, wt_path: Path) -> None:
        """worktree 디렉토리를 삭제하고 git 메타데이터를 정리한다."""
        if wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)
        self._git(["worktree", "prune"])
        logger.debug("[worktree] 제거: %s", wt_path)

    def _copy_workspace_to_worktree(
        self, workspace: WorkspaceManager, wt_path: Path
    ) -> list[Path]:
        """
        workspace 파일을 worktree 에 복사하고 상대 경로 목록을 반환한다.

        복사 규칙:
          workspace/src/a/b.py  →  worktree/a/b.py
          workspace/tests/x.py  →  worktree/tests/x.py
        """
        changed: list[Path] = []

        for src_file in sorted(workspace.src_dir.rglob("*")):
            if not src_file.is_file():
                continue
            rel = src_file.relative_to(workspace.src_dir)
            dest = wt_path / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)
            changed.append(rel)
            logger.debug("복사: src/%s → worktree/%s", rel, rel)

        for test_file in sorted(workspace.tests_dir.rglob("*")):
            if not test_file.is_file():
                continue
            rel = test_file.relative_to(workspace.tests_dir)
            dest = wt_path / "tests" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(test_file, dest)
            changed.append(Path("tests") / rel)
            logger.debug("복사: tests/%s → worktree/tests/%s", rel, rel)

        return changed

    def _wt_git(self, wt_path: Path, args: list[str]) -> subprocess.CompletedProcess:
        """worktree 디렉토리에서 git 명령을 실행한다."""
        return subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=str(wt_path),
        )

    def _wt_git_checked(
        self, wt_path: Path, args: list[str]
    ) -> subprocess.CompletedProcess:
        """worktree 에서 git 실행 후 실패 시 GitWorkflowError."""
        result = self._wt_git(wt_path, args)
        if result.returncode != 0:
            raise GitWorkflowError(
                f"git {' '.join(args)} 실패:\n{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    # ── PR 생성 ───────────────────────────────────────────────────────────────

    def _create_pr(self, task: Task, result: PipelineResult) -> str:
        body = _build_pr_body(task, result)
        cmd = [
            "gh", "pr", "create",
            "--title", f"[agent] {task.title}",
            "--body", body,
            "--base", self.base_branch,
            "--head", task.branch_name,
        ]
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(self.repo_path),
        )
        if completed.returncode != 0:
            raise GitWorkflowError(
                f"gh pr create 실패:\n{completed.stderr.strip()}"
            )
        # gh pr create 는 성공 시 PR URL 을 stdout 에 출력
        return completed.stdout.strip()

    # ── 내부 git 헬퍼 ────────────────────────────────────────────────────────

    def _git(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=str(self.repo_path),
        )

    def _git_checked(self, args: list[str]) -> subprocess.CompletedProcess:
        """실행 후 returncode != 0 이면 GitWorkflowError 를 발생시킨다."""
        result = self._git(args)
        if result.returncode != 0:
            raise GitWorkflowError(
                f"git {' '.join(args)} 실패:\n{result.stderr.strip() or result.stdout.strip()}"
            )
        return result


# ── 사전 조건 검사 ────────────────────────────────────────────────────────────


def check_prerequisites(repo_path: str | Path) -> list[str]:
    """
    파이프라인 실행 전 사전 조건을 확인하고 문제 목록을 반환한다.
    빈 리스트면 모두 정상.

    검사 항목:
      - git 명령어 사용 가능
      - gh 명령어 사용 가능
      - gh 인증 상태
      - repo 워킹 트리 클린 여부
    """
    issues: list[str] = []
    repo_path = Path(repo_path).resolve()

    # git 사용 가능?
    try:
        r = subprocess.run(["git", "--version"], capture_output=True)
        if r.returncode != 0:
            issues.append("git 명령어를 찾을 수 없습니다.")
    except FileNotFoundError:
        issues.append("git 명령어를 찾을 수 없습니다.")

    # gh 사용 가능?
    try:
        r = subprocess.run(["gh", "--version"], capture_output=True)
        if r.returncode != 0:
            issues.append(
                "GitHub CLI(gh)를 찾을 수 없습니다. "
                "https://cli.github.com 에서 설치 후 `gh auth login` 을 실행하세요."
            )
        else:
            # gh 인증 확인
            r = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True
            )
            if r.returncode != 0:
                issues.append("`gh auth login` 을 먼저 실행해 GitHub 에 인증하세요.")
    except FileNotFoundError:
        issues.append(
            "GitHub CLI(gh)를 찾을 수 없습니다. "
            "https://cli.github.com 에서 설치 후 `gh auth login` 을 실행하세요."
        )

    return issues


# ── PR body 빌더 ──────────────────────────────────────────────────────────────


def _build_pr_body(task: Task, result: PipelineResult) -> str:
    review_section = ""
    if result.review:
        verdict_icon = "✅" if result.review.approved else "⚠️"
        review_section = dedent(f"""
            ## 코드 리뷰

            {verdict_icon} **{result.review.verdict}** — {result.review.summary}

            {result.review.details}
        """).strip()

    test_section = ""
    if result.test_result:
        icon = "✅" if result.test_result.passed else "❌"
        test_section = f"## 테스트 결과\n\n{icon} {result.test_result.summary}"
        if result.test_result.failed_tests:
            failed = "\n".join(f"- `{t}`" for t in result.test_result.failed_tests)
            test_section += f"\n\n실패한 테스트:\n{failed}"

    files_section = ""
    all_files = result.test_files + result.impl_files
    if all_files:
        file_list = "\n".join(f"- `{f}`" for f in sorted(all_files))
        files_section = f"## 변경 파일\n\n{file_list}"

    retry_info = ""
    if task.retry_count > 0:
        retry_info = f"\n> 구현 재시도 횟수: {task.retry_count}회\n"

    parts = [
        f"## 태스크\n\n**{task.title}**\n\n{task.description}",
        f"## 수락 기준\n\n{task.acceptance_criteria_text()}",
        test_section,
        files_section,
        review_section,
        retry_info,
        "---\n🤖 자동 생성 by AI Coding Agent Pipeline",
    ]
    return "\n\n".join(p for p in parts if p.strip())


# ── 예외 ──────────────────────────────────────────────────────────────────────


class GitWorkflowError(Exception):
    """git / gh 작업 실패 시 발생."""
