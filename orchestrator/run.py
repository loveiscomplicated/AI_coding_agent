"""
orchestrator/run.py — 파이프라인 CLI 진입점

사용법:
    python -m orchestrator.run --tasks data/tasks.yaml --repo .
    python -m orchestrator.run --tasks data/tasks.yaml --repo . --yes
    python -m orchestrator.run --tasks data/tasks.yaml --repo . --id task-001

실행 흐름:
  1. tasks.yaml 로드 → depends_on 기반 실행 그룹 계산
  2. 사람이 확인 (--yes 로 생략 가능)
  3. 사전 조건 검사 (git, gh, repo 클린)
  4. 그룹 순서대로 순차 실행
  5. 완료마다 Task Report 저장 + tasks.yaml 체크포인트
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from docker.runner import DockerTestRunner
from hotline.notifier import DiscordNotifier
from llm import LLMConfig, create_client
from orchestrator.git_workflow import GitWorkflow, GitWorkflowError, check_prerequisites
from orchestrator.merge_agent import MergeAgent
from orchestrator.pipeline import TDDPipeline
from orchestrator.report import build_report, save_report
from orchestrator.task import Task, TaskStatus, load_tasks, save_tasks
from orchestrator.workspace import WorkspaceManager

# ── ANSI 컬러 ─────────────────────────────────────────────────────────────────
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


# ── 위상 정렬 ─────────────────────────────────────────────────────────────────

def resolve_execution_groups(tasks: list[Task]) -> list[list[Task]]:
    """
    depends_on 관계를 분석하여 실행 그룹을 반환한다.
    같은 그룹 내 태스크는 순차 실행 (Phase 3에서 병렬화).

    Returns:
        [[task-001, task-003], [task-002, task-004]] 형태의 그룹 리스트.
        앞 그룹이 모두 완료된 후 다음 그룹을 실행한다.

    Raises:
        ValueError: 존재하지 않는 ID 참조 또는 순환 의존성.
    """
    task_map = {t.id: t for t in tasks}

    # 존재하지 않는 ID 참조 검사
    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id not in task_map:
                raise ValueError(
                    f"태스크 '{task.id}'의 depends_on에 존재하지 않는 ID: '{dep_id}'"
                )

    # Kahn's algorithm
    in_degree = {t.id: len(t.depends_on) for t in tasks}
    dependents: dict[str, list[str]] = {t.id: [] for t in tasks}
    for task in tasks:
        for dep_id in task.depends_on:
            dependents[dep_id].append(task.id)

    groups: list[list[Task]] = []
    ready = [t for t in tasks if in_degree[t.id] == 0]

    while ready:
        groups.append(ready)
        next_ready = []
        for task in ready:
            for dep_id in dependents[task.id]:
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    next_ready.append(task_map[dep_id])
        ready = next_ready

    total_resolved = sum(len(g) for g in groups)
    if total_resolved != len(tasks):
        raise ValueError("태스크 의존성에 순환 참조가 있습니다.")

    return groups


# ── 파이프라인 실행 (CLI + API 공용) ──────────────────────────────────────────

def run_pipeline(
    tasks_path: Path,
    repo_path: Path,
    base_branch: str = "dev",
    task_id: str | None = None,
    no_pr: bool = False,
    verbose: bool = False,
    on_progress: object = None,  # 향후 콜백 확장용
) -> dict:
    """
    파이프라인 실행 핵심 로직. CLI와 FastAPI 백엔드 양쪽에서 호출된다.

    Returns:
        {"success": int, "fail": int, "tasks": [task.to_dict(), ...]}
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # 태스크 로드
    all_tasks = load_tasks(tasks_path)

    if task_id:
        all_tasks = [t for t in all_tasks if t.id == task_id]
        if not all_tasks:
            raise ValueError(f"태스크 ID '{task_id}'를 찾을 수 없습니다.")

    pending = [t for t in all_tasks if t.status not in (TaskStatus.DONE, TaskStatus.FAILED)]
    if not pending:
        return {"success": 0, "fail": 0, "tasks": [t.to_dict() for t in all_tasks]}

    # 의존성 기반 실행 그룹 계산
    groups = resolve_execution_groups(pending)

    # LLM 클라이언트 + 파이프라인 초기화
    haiku = create_client("claude", LLMConfig(model="claude-haiku-4-5", max_tokens=8192))
    sonnet = create_client("claude", LLMConfig(model="claude-sonnet-4-6", max_tokens=8192))
    runner = DockerTestRunner()
    if not runner._image_exists():
        runner.build_image()

    pipeline = TDDPipeline(agent_llm=haiku, implementer_llm=sonnet, test_runner=runner)
    git = GitWorkflow(repo_path, base_branch=base_branch)
    notifier = DiscordNotifier.from_env()

    success_count = 0
    fail_count = 0

    # 파이프라인 시작 알림
    _notify(notifier, f"📋 파이프라인 시작 — {len(pending)}개 태스크")

    for group in groups:
        for task in group:
            start_time = time.monotonic()

            # 태스크 시작 알림
            _notify(notifier, f"🚀 [{task.id}] \"{task.title}\" 시작")

            with WorkspaceManager(task, repo_path, keep_on_failure=True) as ws:
                result = pipeline.run(task, ws)
                elapsed = time.monotonic() - start_time

                pr_url = ""
                if result.succeeded and not no_pr:
                    try:
                        pr_url = git.run(task, ws, result)
                        task.pr_url = pr_url
                        task.status = TaskStatus.DONE
                        success_count += 1
                        _notify(
                            notifier,
                            f"✅ [{task.id}] \"{task.title}\" 완료! "
                            f"PR: {pr_url}  (⏱ {elapsed:.0f}s)",
                        )
                    except GitWorkflowError as e:
                        result = type(result).failed(task, str(e))  # type: ignore[attr-defined]
                        fail_count += 1
                        _notify_failure(notifier, task, str(e), elapsed)
                elif result.succeeded:
                    task.status = TaskStatus.DONE
                    success_count += 1
                    _notify(
                        notifier,
                        f"✅ [{task.id}] \"{task.title}\" 완료! (⏱ {elapsed:.0f}s)",
                    )
                else:
                    fail_count += 1
                    hint = _notify_failure(notifier, task, result.failure_reason or "알 수 없음", elapsed)
                    if hint:
                        # 사용자 힌트를 last_error에 추가해 report에 기록
                        task.last_error = f"[Discord 힌트] {hint}\n{task.last_error}"

                # Task Report 저장
                report = build_report(task, result, elapsed_seconds=elapsed, pr_url=pr_url)
                save_report(report)

            # tasks.yaml 체크포인트
            save_tasks(all_tasks, tasks_path)

    _notify(
        notifier,
        f"🏁 파이프라인 완료 — 성공: {success_count}  실패: {fail_count}",
    )

    return {
        "success": success_count,
        "fail": fail_count,
        "tasks": [t.to_dict() for t in all_tasks],
    }


# ── 단일 태스크 실행 (스레드 안전) ────────────────────────────────────────────


def _run_single_task(
    task: Task,
    pipeline: "TDDPipeline",
    git: "GitWorkflow",
    repo_path: Path,
    no_pr: bool,
    notifier,
    save_lock: threading.Lock,
    all_tasks: list[Task],
    tasks_path: Path,
) -> tuple[bool, str]:
    """
    태스크 하나를 실행하고 (succeeded, branch_name) 을 반환한다.

    Thread-safe:
    - WorkspaceManager 는 태스크마다 독립 디렉토리 사용
    - GitWorkflow.run() 은 worktree 기반으로 main repo HEAD 불변
    - save_lock 은 tasks.yaml 파일 쓰기 직렬화에 사용
    """
    print(f"\n{'─' * 60}")
    print(f"{_BOLD}[{task.id}]{_RESET} {task.title}")
    if task.depends_on:
        print(f"  선행 완료: {', '.join(task.depends_on)}")
    print(f"{'─' * 60}")

    start_time = time.monotonic()
    _notify(notifier, f"🚀 [{task.id}] \"{task.title}\" 시작")

    with WorkspaceManager(task, repo_path, keep_on_failure=True) as ws:
        print(_info(f"[{task.id}] TestWriter → Implementer → Docker → Reviewer ..."))
        result = pipeline.run(task, ws)
        elapsed = time.monotonic() - start_time

        pr_url = ""
        branch = ""
        succeeded = False

        if not result.succeeded:
            print(_fail(f"[{task.id}] 파이프라인 실패: {result.failure_reason}"))
            print(f"  workspace 보존됨: {ws.path}")
            _notify_failure(notifier, task, result.failure_reason or "알 수 없음", elapsed)
        else:
            if result.test_result:
                print(_ok(f"[{task.id}] 테스트: {result.test_result.summary}"))
            if result.review:
                icon = "✅" if result.review.approved else "⚠️"
                print(f"  {icon} [{task.id}] 리뷰: {result.review.verdict} — {result.review.summary}")

            if no_pr:
                print(_warn(f"[{task.id}] --no-pr: PR 생성 건너뜀"))
                task.status = TaskStatus.DONE
                succeeded = True
                _notify(notifier, f"✅ [{task.id}] \"{task.title}\" 완료! (⏱ {elapsed:.0f}s)")
            else:
                print(_info(f"[{task.id}] 브랜치 → 커밋 → 푸시 → PR ..."))
                try:
                    pr_url = git.run(task, ws, result)
                    task.pr_url = pr_url
                    task.status = TaskStatus.DONE
                    branch = task.branch_name
                    succeeded = True
                    print(_ok(f"[{task.id}] PR: {pr_url}"))
                    _notify(
                        notifier,
                        f"✅ [{task.id}] \"{task.title}\" 완료! PR: {pr_url}  (⏱ {elapsed:.0f}s)",
                    )
                except GitWorkflowError as e:
                    print(_fail(f"[{task.id}] Git 워크플로우 실패: {e}"))
                    _notify_failure(notifier, task, str(e), elapsed)

        report = build_report(task, result, elapsed_seconds=elapsed, pr_url=pr_url)
        report_path = save_report(report)
        print(f"  [{task.id}] 리포트: {report_path}")

        with save_lock:
            save_tasks(all_tasks, tasks_path)

    return succeeded, branch


# ── 자동 머지 헬퍼 ────────────────────────────────────────────────────────────


def _auto_merge_group(
    branches: list[str],
    base_branch: str,
    repo_path: Path,
    merge_agent: MergeAgent,
) -> None:
    """
    그룹 내 agent 브랜치들을 base_branch 에 순서대로 머지하고 push 한다.

    충돌 발생 시 MergeAgent(LLM)가 자동 해결한다.
    머지 실패는 경고로 출력하고 계속 진행한다 (태스크 자체는 DONE).
    """
    import subprocess

    def git(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + args, capture_output=True, text=True, cwd=str(repo_path)
        )

    # 현재 브랜치 저장 → base_branch 로 이동
    original = git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    checkout = git(["checkout", base_branch])
    if checkout.returncode != 0:
        print(_warn(f"자동 머지 건너뜀 — {base_branch} checkout 실패: {checkout.stderr.strip()}"))
        return

    print(f"\n{_info(f'{base_branch} 자동 머지 시작 ({len(branches)}개 브랜치)')}")

    for branch in branches:
        merge_result = merge_agent.merge_branch(branch, base_branch=base_branch)
        if merge_result.success:
            resolved = merge_result.conflicts_resolved
            if resolved:
                print(_ok(f"머지: {branch}  (충돌 {resolved}개 자동 해결)"))
            else:
                print(_ok(f"머지: {branch}"))
        else:
            print(_warn(f"머지 실패: {branch} — {merge_result.error}"))

    # StructureUpdater — 머지 완료 후 PROJECT_STRUCTURE.md 갱신
    _update_project_structure(repo_path, git)

    # base_branch push
    push = git(["push", "origin", base_branch])
    if push.returncode == 0:
        print(_ok(f"{base_branch} push 완료"))
    else:
        print(_warn(f"{base_branch} push 실패: {push.stderr.strip()}"))

    # 원래 브랜치로 복귀
    git(["checkout", original])


def _update_project_structure(repo_path: Path, git_fn) -> None:
    """
    StructureUpdater 로 PROJECT_STRUCTURE.md 를 갱신하고 커밋한다.

    다음 그룹의 에이전트가 최신 코드베이스 구조를 볼 수 있도록
    각 그룹 머지 완료 직후 호출한다.
    """
    try:
        from structure.updater import update as structure_update

        structure_update(
            root=str(repo_path),
            output="PROJECT_STRUCTURE.md",
        )

        git_fn(["add", "PROJECT_STRUCTURE.md"])
        result = git_fn(["commit", "-m", "[auto] PROJECT_STRUCTURE.md 업데이트"])

        if result.returncode == 0:
            print(_ok("PROJECT_STRUCTURE.md 업데이트 완료"))
        elif "nothing to commit" in (result.stdout + result.stderr):
            pass  # 변경 없음 — 정상
        else:
            print(_warn(f"PROJECT_STRUCTURE.md 커밋 실패: {result.stderr.strip()}"))
    except Exception as exc:
        print(_warn(f"StructureUpdater 실패 (건너뜀): {exc}"))


# ── Discord 헬퍼 ──────────────────────────────────────────────────────────────

def _notify(notifier: DiscordNotifier | None, content: str) -> str | None:
    """Discord 알림을 안전하게 전송한다. 오류 발생 시 로깅 후 무시."""
    if not notifier:
        return None
    try:
        return notifier.send(content)
    except Exception as e:
        logging.getLogger(__name__).warning("Discord 알림 실패: %s", e)
        return None


def _notify_failure(
    notifier: DiscordNotifier | None,
    task: Task,
    reason: str,
    elapsed: float,
) -> str | None:
    """
    태스크 실패를 Discord에 알리고 사용자 힌트를 기다린다.

    Returns:
        사용자가 입력한 힌트 문자열, 없으면 None.
    """
    msg = (
        f"❌ [{task.id}] \"{task.title}\" 실패 (⏱ {elapsed:.0f}s)\n"
        f"원인: {reason[:300]}\n"
        f"힌트를 입력하거나 '건너뜀'을 입력하세요. (5분 후 자동 건너뜀)"
    )
    message_id = _notify(notifier, msg)
    if not message_id or not notifier:
        return None

    reply = notifier.wait_for_reply(message_id, timeout=300)
    if reply and reply.strip().lower() not in ("건너뜀", "skip"):
        return reply
    return None


# ── CLI 진입점 ────────────────────────────────────────────────────────────────

def main() -> int:
    args = _parse_args()

    tasks_path = Path(args.tasks)
    repo_path = Path(args.repo).resolve()

    # 태스크 로드 (목록 출력용)
    try:
        all_tasks = load_tasks(tasks_path)
    except (FileNotFoundError, KeyError, ValueError) as e:
        print(_fail(f"태스크 파일 로드 실패: {e}"))
        return 1

    if args.id:
        if not any(t.id == args.id for t in all_tasks):
            print(_fail(f"태스크 ID '{args.id}' 를 찾을 수 없습니다."))
            return 1
        # --id 모드: 지정 태스크를 pending으로 강제
        for t in all_tasks:
            if t.id == args.id:
                t.status = TaskStatus.PENDING
        target = next(t for t in all_tasks if t.id == args.id)
        # 의존성 충족 여부 확인 (완료된 것만 허용)
        done_ids = {t.id for t in all_tasks if t.status == TaskStatus.DONE}
        unmet = [d for d in target.depends_on if d not in done_ids and d != target.id]
        if unmet:
            print(_fail(f"의존성 미충족: '{target.id}'의 선행 태스크가 완료되지 않았습니다: {unmet}"))
            return 1
        # 단독 실행을 위해 depends_on 없이 그룹 구성
        import copy
        solo = copy.copy(target)
        solo.depends_on = []
        pending = [target]
        groups = [[solo]]
    else:
        pending = [t for t in all_tasks if t.status not in (TaskStatus.DONE, TaskStatus.FAILED)]
        if not pending:
            print(_ok("모든 태스크가 이미 완료되었습니다."))
            return 0

        # 의존성 그룹 계산
        try:
            groups = resolve_execution_groups(pending)
        except ValueError as e:
            print(_fail(f"의존성 오류: {e}"))
            return 1

    # 태스크 목록 출력
    print(f"\n{_BOLD}실행할 태스크{_RESET} ({len(pending)}개):\n")
    for i, group in enumerate(groups, 1):
        for t in group:
            status_str = f"[{t.status.value}]" if t.status != TaskStatus.PENDING else ""
            deps = f"  ← {', '.join(t.depends_on)}" if t.depends_on else ""
            print(f"  [{i}] {_CYAN}{t.id}{_RESET}  {t.title}  {_YELLOW}{status_str}{_RESET}{deps}")

    # 사람 확인
    if not args.yes:
        print()
        answer = input("파이프라인을 시작하시겠습니까? [y/N] ").strip().lower()
        if answer != "y":
            print("취소되었습니다.")
            return 0

    # 사전 조건 검사
    issues = check_prerequisites(repo_path)
    if args.no_pr:
        issues = [i for i in issues if "gh" not in i.lower() and "github" not in i.lower() and "auth" not in i.lower()]
    if issues:
        print(f"\n{_RED}사전 조건 미충족:{_RESET}")
        for issue in issues:
            print(f"  {_warn(issue)}")
        return 1

    # Docker 이미지
    runner = DockerTestRunner()
    if not runner._image_exists():
        print(_info("Docker 테스트 이미지 빌드 중..."))
        try:
            runner.build_image()
            print(_ok("이미지 빌드 완료"))
        except RuntimeError as e:
            print(_fail(f"Docker 이미지 빌드 실패: {e}"))
            return 1

    # LLM 클라이언트
    try:
        haiku = create_client("claude", LLMConfig(model="claude-haiku-4-5", max_tokens=8192))
        sonnet = create_client("claude", LLMConfig(model="claude-sonnet-4-6", max_tokens=8192))
    except ValueError as e:
        print(_fail(f"LLM 클라이언트 초기화 실패: {e}"))
        return 1

    pipeline = TDDPipeline(agent_llm=haiku, implementer_llm=sonnet, test_runner=runner)
    git = GitWorkflow(repo_path, base_branch=args.base_branch)
    merge_agent = MergeAgent(llm=haiku, repo_path=repo_path)

    save_lock = threading.Lock()  # tasks.yaml 쓰기 직렬화
    success_count = 0
    fail_count = 0
    max_parallel = args.parallel

    for group_idx, group in enumerate(groups, 1):
        parallel_str = f"  병렬 {min(max_parallel, len(group))}개" if max_parallel > 1 else ""
        if len(groups) > 1 or max_parallel > 1:
            print(f"\n{_BOLD}── 실행 그룹 {group_idx}/{len(groups)} ({len(group)}개 태스크{parallel_str}) ──{_RESET}")

        merged_branches: list[str] = []

        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            futures = {
                executor.submit(
                    _run_single_task,
                    task, pipeline, git, repo_path,
                    args.no_pr, None,  # notifier=None (CLI에서는 Discord 미사용)
                    save_lock, all_tasks, tasks_path,
                ): task
                for task in group
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    succeeded, branch = future.result()
                    if succeeded:
                        success_count += 1
                        if branch:
                            merged_branches.append(branch)
                    else:
                        fail_count += 1
                except Exception as exc:
                    print(_fail(f"[{task.id}] 예외 발생: {exc}"))
                    fail_count += 1

        # ── 그룹 완료 후 base_branch 에 자동 머지 ─────────────────────────────
        if merged_branches and not args.no_pr:
            _auto_merge_group(
                branches=merged_branches,
                base_branch=args.base_branch,
                repo_path=repo_path,
                merge_agent=merge_agent,
            )

    print(f"\n{'═' * 60}")
    print(f"{_BOLD}실행 완료{_RESET}  성공: {_GREEN}{success_count}{_RESET}  실패: {_RED}{fail_count}{_RESET}")
    print(f"{'═' * 60}\n")

    return 0 if fail_count == 0 else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.run",
        description="AI Coding Agent — TDD 파이프라인 실행",
    )
    parser.add_argument("--tasks", "-t", required=True,
                        help="태스크 정의 YAML 파일 경로")
    parser.add_argument("--repo", "-r", default=".",
                        help="대상 git 저장소 경로 (기본값: 현재 디렉토리)")
    parser.add_argument("--base-branch", "-b", default="dev",
                        help="PR base branch (기본값: dev)")
    parser.add_argument("--id", default=None,
                        help="특정 태스크 ID만 실행")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="확인 없이 바로 시작")
    parser.add_argument("--no-pr", action="store_true",
                        help="PR 생성 없이 로컬 실행")
    parser.add_argument("--parallel", "-p", type=int, default=1,
                        metavar="N",
                        help="그룹 내 태스크 병렬 실행 수 (기본값: 1 = 순차)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="DEBUG 로그 출력")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
