"""
orchestrator/run.py — 파이프라인 CLI 진입점

사용법:
    python -m orchestrator.run --tasks data/tasks.yaml --repo .
    python -m orchestrator.run --tasks data/tasks.yaml --repo . --yes   # 확인 없이 실행
    python -m orchestrator.run --tasks data/tasks.yaml --repo . --id task-001  # 특정 태스크만

실행 흐름:
  1. tasks.yaml 로드 → 사람이 확인
  2. 사전 조건 검사 (git, gh, repo 클린)
  3. 각 태스크에 대해 TDDPipeline 실행
  4. 성공 시 GitWorkflow → PR 생성
  5. tasks.yaml 에 상태 저장 (체크포인트)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path 에 추가 (직접 실행 시)
sys.path.insert(0, str(Path(__file__).parent.parent))

from docker.runner import DockerTestRunner
from llm import LLMConfig, create_client
from orchestrator.git_workflow import GitWorkflow, GitWorkflowError, check_prerequisites
from orchestrator.pipeline import TDDPipeline
from orchestrator.task import Task, TaskStatus, load_tasks, save_tasks
from orchestrator.workspace import WorkspaceManager

# ── ANSI 컬러 (터미널 출력용) ─────────────────────────────────────────────────
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"


def _ok(msg: str)   -> str: return f"{_GREEN}✓{_RESET} {msg}"
def _fail(msg: str) -> str: return f"{_RED}✗{_RESET} {msg}"
def _warn(msg: str) -> str: return f"{_YELLOW}⚠{_RESET} {msg}"
def _info(msg: str) -> str: return f"{_CYAN}→{_RESET} {msg}"


def main() -> int:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # ── 1. 태스크 로드 ────────────────────────────────────────────────────────
    tasks_path = Path(args.tasks)
    try:
        all_tasks = load_tasks(tasks_path)
    except (FileNotFoundError, KeyError, ValueError) as e:
        print(_fail(f"태스크 파일 로드 실패: {e}"))
        return 1

    # --id 필터
    if args.id:
        all_tasks = [t for t in all_tasks if t.id == args.id]
        if not all_tasks:
            print(_fail(f"태스크 ID '{args.id}' 를 찾을 수 없습니다."))
            return 1

    pending = [t for t in all_tasks if t.status != TaskStatus.DONE]
    if not pending:
        print(_ok("모든 태스크가 이미 완료되었습니다."))
        return 0

    # ── 2. 태스크 목록 출력 및 확인 ──────────────────────────────────────────
    print(f"\n{_BOLD}실행할 태스크{_RESET} ({len(pending)}개):\n")
    for t in pending:
        status_str = f"[{t.status.value}]" if t.status != TaskStatus.PENDING else ""
        print(f"  {_CYAN}{t.id}{_RESET}  {t.title}  {_YELLOW}{status_str}{_RESET}")

    if not args.yes:
        print()
        answer = input("파이프라인을 시작하시겠습니까? [y/N] ").strip().lower()
        if answer != "y":
            print("취소되었습니다.")
            return 0

    # ── 3. 사전 조건 검사 ─────────────────────────────────────────────────────
    repo_path = Path(args.repo).resolve()
    issues = check_prerequisites(repo_path)
    # --no-pr 모드에서는 gh 관련 이슈 무시
    if args.no_pr:
        issues = [i for i in issues if "gh" not in i.lower() and "github" not in i.lower() and "auth" not in i.lower()]
    if issues:
        print(f"\n{_RED}사전 조건 미충족:{_RESET}")
        for issue in issues:
            print(f"  {_warn(issue)}")
        return 1

    # ── 4. Docker 이미지 빌드 (없으면) ────────────────────────────────────────
    runner = DockerTestRunner()
    if not runner._image_exists():
        print(_info("Docker 테스트 이미지 빌드 중..."))
        try:
            runner.build_image()
            print(_ok("이미지 빌드 완료"))
        except RuntimeError as e:
            print(_fail(f"Docker 이미지 빌드 실패: {e}"))
            return 1

    # ── 5. LLM 클라이언트 초기화 ──────────────────────────────────────────────
    try:
        haiku = create_client(
            "claude",
            LLMConfig(model="claude-haiku-4-5", max_tokens=8192),
        )
    except ValueError as e:
        print(_fail(f"LLM 클라이언트 초기화 실패: {e}"))
        return 1

    # ── 6. 태스크 실행 루프 ───────────────────────────────────────────────────
    pipeline = TDDPipeline(agent_llm=haiku, test_runner=runner)
    git = GitWorkflow(repo_path, base_branch=args.base_branch)

    success_count = 0
    fail_count = 0

    for task in pending:
        print(f"\n{'─' * 60}")
        print(f"{_BOLD}[{task.id}]{_RESET} {task.title}")
        print(f"{'─' * 60}")

        with WorkspaceManager(task, repo_path, keep_on_failure=True) as ws:
            # 파이프라인 실행
            print(_info("TestWriter → Implementer → Docker → Reviewer ..."))
            result = pipeline.run(task, ws)

            if not result.succeeded:
                print(_fail(f"파이프라인 실패: {result.failure_reason}"))
                print(f"  workspace 보존됨: {ws.path}")
                fail_count += 1
                save_tasks(all_tasks, tasks_path)  # 실패 상태 저장
                continue

            # 테스트 결과 출력
            if result.test_result:
                print(_ok(f"테스트: {result.test_result.summary}"))
            if result.review:
                icon = "✅" if result.review.approved else "⚠️"
                print(f"  {icon} 리뷰: {result.review.verdict} — {result.review.summary}")

            # Git 워크플로우
            if args.no_pr:
                print(_warn("--no-pr 옵션: PR 생성 건너뜀"))
                task.status = TaskStatus.DONE
                success_count += 1
            else:
                print(_info("브랜치 생성 → 커밋 → 푸시 → PR 생성 ..."))
                try:
                    pr_url = git.run(task, ws, result)
                    task.pr_url = pr_url
                    task.status = TaskStatus.DONE
                    print(_ok(f"PR: {pr_url}"))
                    success_count += 1
                except GitWorkflowError as e:
                    print(_fail(f"Git 워크플로우 실패: {e}"))
                    fail_count += 1

        save_tasks(all_tasks, tasks_path)  # 태스크 완료 후 즉시 저장

    # ── 7. 최종 요약 ─────────────────────────────────────────────────────────
    print(f"\n{'═' * 60}")
    print(f"{_BOLD}실행 완료{_RESET}  성공: {_GREEN}{success_count}{_RESET}  실패: {_RED}{fail_count}{_RESET}")
    print(f"{'═' * 60}\n")

    return 0 if fail_count == 0 else 1


# ── 인자 파싱 ─────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.run",
        description="AI Coding Agent — TDD 파이프라인 실행",
    )
    parser.add_argument(
        "--tasks", "-t",
        required=True,
        help="태스크 정의 YAML 파일 경로 (예: data/tasks.yaml)",
    )
    parser.add_argument(
        "--repo", "-r",
        default=".",
        help="대상 git 저장소 경로 (기본값: 현재 디렉토리)",
    )
    parser.add_argument(
        "--base-branch", "-b",
        default="dev",
        help="PR 의 base branch (기본값: dev)",
    )
    parser.add_argument(
        "--id",
        default=None,
        help="특정 태스크 ID 만 실행 (예: --id task-001)",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="실행 전 확인 없이 바로 시작",
    )
    parser.add_argument(
        "--no-pr",
        action="store_true",
        help="PR 생성 없이 파이프라인만 실행 (로컬 테스트용)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="DEBUG 로그 출력",
    )
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
