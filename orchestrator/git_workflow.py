"""
orchestrator/git_workflow.py — 브랜치 생성 · 커밋 · PR 생성

파이프라인 성공 후 workspace 결과물을 실제 repo 에 반영하고
GitHub PR 을 생성한다.

흐름:
  1. 현재 브랜치 저장
  2. agent/task-{id} 브랜치 생성 (git checkout -b)
  3. workspace → repo 파일 복사
  4. git add + git commit
  5. git push origin {branch}
  6. 원래 브랜치로 복귀 (git checkout)
  7. gh pr create

파일 복사 규칙:
  workspace/src/**  → repo/**        (workspace/src/ 기준 상대 경로 유지)
  workspace/tests/** → repo/tests/** (workspace/tests/ 기준 상대 경로 유지)

사전 조건 (run.py 에서 _check_prerequisites() 로 검증):
  - git 명령어 사용 가능
  - gh 명령어 사용 가능 (GitHub CLI, `gh auth login` 완료 상태)
  - repo 워킹 트리가 클린 (uncommitted 변경 없음)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
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

        Returns:
            생성된 PR 의 URL 문자열

        Raises:
            GitWorkflowError: git / gh 명령 실패 시
        """
        branch = task.branch_name
        original = self._current_branch()
        logger.info("[%s] git 워크플로우 시작 (branch: %s)", task.id, branch)

        try:
            self._create_branch(branch)
            changed = self._copy_workspace_to_repo(workspace)
            self._git_add(changed)
            self._git_commit(task)
            self._git_push(branch)
        except Exception:
            # 브랜치 전환에 실패했더라도 원래 브랜치로 복귀 시도
            self._safe_checkout(original)
            raise

        self._safe_checkout(original)
        pr_url = self._create_pr(task, result)
        logger.info("[%s] PR 생성 완료: %s", task.id, pr_url)
        return pr_url

    # ── git 작업 ─────────────────────────────────────────────────────────────

    def _current_branch(self) -> str:
        result = self._git(["rev-parse", "--abbrev-ref", "HEAD"])
        return result.stdout.strip()

    def _create_branch(self, branch: str) -> None:
        """base_branch 에서 새 브랜치를 만든다. 이미 존재하면 삭제 후 재생성."""
        # 혹시 이미 있으면 삭제 (이전 실패한 시도 잔재)
        existing = self._git(["branch", "--list", branch])
        if existing.stdout.strip():
            self._git(["branch", "-D", branch])

        self._git_checked(["checkout", "-b", branch])
        logger.debug("브랜치 생성: %s", branch)

    def _copy_workspace_to_repo(self, workspace: WorkspaceManager) -> list[Path]:
        """
        workspace 파일을 repo 에 복사하고 변경된 파일 목록을 반환한다.

        복사 규칙:
          workspace/src/a/b.py  →  repo/a/b.py
          workspace/tests/x.py  →  repo/tests/x.py
        """
        changed: list[Path] = []

        # src/ 아래 파일: workspace/src/ 기준 상대 경로를 repo 루트에 적용
        for src_file in sorted(workspace.src_dir.rglob("*")):
            if not src_file.is_file():
                continue
            rel = src_file.relative_to(workspace.src_dir)
            dest = self.repo_path / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)
            changed.append(dest.relative_to(self.repo_path))
            logger.debug("복사: %s → %s", rel, dest)

        # tests/ 아래 파일: workspace/tests/ 기준 상대 경로를 repo/tests/ 에 적용
        for test_file in sorted(workspace.tests_dir.rglob("*")):
            if not test_file.is_file():
                continue
            rel = test_file.relative_to(workspace.tests_dir)
            dest = self.repo_path / "tests" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(test_file, dest)
            changed.append(dest.relative_to(self.repo_path))
            logger.debug("복사: tests/%s → %s", rel, dest)

        return changed

    def _git_add(self, files: list[Path]) -> None:
        if not files:
            return
        self._git_checked(["add"] + [str(f) for f in files])

    def _git_commit(self, task: Task) -> None:
        message = f"[agent] {task.title} ({task.id})\n\n자동 생성 by AI Coding Agent Pipeline"
        self._git_checked(["commit", "-m", message])

    def _git_push(self, branch: str) -> None:
        self._git_checked(["push", "origin", branch])

    def _safe_checkout(self, branch: str) -> None:
        """예외를 삼키는 checkout (정리용)."""
        try:
            self._git(["checkout", branch])
        except Exception as e:
            logger.warning("checkout %s 실패 (무시): %s", branch, e)

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

    # repo 워킹 트리 클린?
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
    )
    if r.returncode == 0 and r.stdout.strip():
        issues.append(
            "repo 에 uncommitted 변경이 있습니다. "
            "파이프라인 실행 전 커밋하거나 스태시하세요.\n"
            f"  {r.stdout.strip()}"
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
