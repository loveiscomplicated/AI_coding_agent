"""
orchestrator/run.py — 파이프라인 CLI 진입점

사용법:
    python -m orchestrator.run --tasks agent-data/tasks.yaml --repo .
    python -m orchestrator.run --tasks agent-data/tasks.yaml --repo . --yes
    python -m orchestrator.run --tasks agent-data/tasks.yaml --repo . --id task-001

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
import unicodedata

logger = logging.getLogger(__name__)
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from docker.runner import DockerTestRunner
from hotline.notifier import DiscordNotifier
from tools.hotline_tools import (
    set_notifier as _set_hotline_notifier,
    set_repo_path as _set_hotline_repo_path,
    set_tasks_path as _set_hotline_tasks_path,
    set_llm as _set_hotline_llm,
    set_pause_controller as _set_hotline_pause_controller,
    is_hotline_active as _is_hotline_active,
    create_hotline_llms,
)
from llm import LLMConfig, Message, BaseLLMClient, create_client
from orchestrator.git_workflow import GitWorkflow, GitWorkflowError, check_prerequisites
from orchestrator.merge_agent import MergeAgent
from orchestrator.milestone import generate_milestone_report
from orchestrator.intervention import (
    classify_and_analyze as orch_classify_and_analyze,
    generate_report_with_metrics as orch_report_with_metrics,
    save_report as orch_save_report,
    set_llm as _set_intervention_llm,
    set_model_config as _set_intervention_model_config,
    create_intervention_llms,
)
from agents.roles import RoleModelConfig, resolve_model_for_role, ROLE_MERGE_AGENT
from orchestrator.pipeline import TDDPipeline
from orchestrator.report import build_report, load_reports, save_report
from orchestrator.task import Task, TaskStatus, load_tasks, save_tasks
from orchestrator.workspace import WorkspaceManager
from project_paths import resolve_reports_dir

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


def _accumulate_external_tokens(metrics, role: str, token_usage: tuple[int, int, int, int], call_log: list[dict]) -> None:
    """ReactLoop 밖 단일 LLM 호출의 토큰/로그를 PipelineMetrics에 누적한다."""
    prev = metrics.token_usage.get(role, (0, 0, 0, 0))
    if len(prev) == 2:
        prev = (*prev, 0, 0)
    metrics.token_usage[role] = (
        prev[0] + (token_usage[0] or 0),
        prev[1] + (token_usage[1] or 0),
        prev[2] + (token_usage[2] or 0),
        prev[3] + (token_usage[3] or 0),
    )
    if call_log:
        start = len(metrics.call_logs.get(role, []))
        normalized = []
        for idx, entry in enumerate(call_log, start=1):
            normalized.append({**entry, "iteration": start + idx})
        metrics.call_logs.setdefault(role, []).extend(normalized)


# ── 위상 정렬 ─────────────────────────────────────────────────────────────────

def resolve_execution_groups(
    tasks: list[Task],
    all_valid_ids: set[str] | None = None,
) -> list[list[Task]]:
    """
    depends_on 관계를 분석하여 실행 그룹을 반환한다.
    같은 그룹 내 태스크는 순차 실행 (Phase 3에서 병렬화).

    Args:
        tasks: 실행할 태스크 목록 (pending 태스크만)
        all_valid_ids: 유효한 태스크 ID 전체 집합.
                       None이면 tasks 내 ID만 허용.
                       재개 시 이미 완료된 태스크 ID를 전달하면
                       depends_on 검증을 통과시킬 수 있다.

    Returns:
        [[task-001, task-003], [task-002, task-004]] 형태의 그룹 리스트.
        앞 그룹이 모두 완료된 후 다음 그룹을 실행한다.

    Raises:
        ValueError: 존재하지 않는 ID 참조 또는 순환 의존성.
    """
    task_map = {t.id: t for t in tasks}
    valid_ids = all_valid_ids if all_valid_ids is not None else set(task_map.keys())

    # 존재하지 않는 ID 참조 검사 (완료된 태스크 포함 전체 기준)
    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id not in valid_ids:
                raise ValueError(
                    f"태스크 '{task.id}'의 depends_on에 존재하지 않는 ID: '{dep_id}'"
                )

    # Kahn's algorithm — 이미 완료된 의존성은 in_degree에서 제외
    in_degree = {t.id: sum(1 for d in t.depends_on if d in task_map) for t in tasks}
    dependents: dict[str, list[str]] = {t.id: [] for t in tasks}
    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id in task_map:   # pending 태스크 간 의존성만 추적
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


# ── 일시정지 컨트롤러 ─────────────────────────────────────────────────────────

class PauseController:
    """
    파이프라인 일시정지/재개/중단 상태를 스레드 안전하게 관리한다.

    Discord 명령 리스너 스레드가 상태를 변경하고,
    파이프라인 루프가 각 태스크 완료 후 wait_if_paused()를 호출한다.

    추가로, attach_notifier()로 Discord notifier를 연결하면 is_stopped 호출 시
    메인 스레드에서 직접 Discord를 폴링하여 리스너 스레드 장애에도 "중단" 감지가 가능하다.

    명령어:
        멈춰 / pause  → 다음 태스크 전 일시정지
        계속 / resume → 일시정지 해제 후 재개
        중단 / stop   → 파이프라인 즉시 종료
    """

    _PAUSE_KEYWORDS  = {"멈춰", "pause", "멈춤", "정지"}
    _RESUME_KEYWORDS = {"계속", "resume", "재개"}
    _STOP_KEYWORDS   = {"중단", "stop", "종료", "abort"}

    _PAUSE_RESUME_POLL = 30   # wait_if_paused 타임아웃 단위(초) — 루프로 재확인
    _DISCORD_POLL_INTERVAL = 2  # 직접 Discord 폴링 최소 간격(초) — API 부하 제어

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paused = False
        self._stopped = False
        self._resume_event = threading.Event()
        self._resume_event.set()  # 초기엔 정지 안 됨
        # 직접 Discord 폴링용
        self._notifier = None
        self._discord_cursor: str | None = None
        self._last_poll_time: float = 0.0
        self._discord_error_tracker = None  # attach_notifier 시 생성

    # ── Discord 직접 폴링 (리스너 스레드 백업) ────────────────────────────────

    def attach_notifier(self, notifier, after_message_id: str | None = None) -> None:
        """Discord notifier를 연결하여 is_stopped 호출 시 직접 폴링을 활성화한다."""
        from hotline.notifier import _TransientErrorTracker
        self._notifier = notifier
        self._discord_cursor = after_message_id
        self._discord_error_tracker = _TransientErrorTracker("PauseController")

    def _poll_discord_for_stop(self) -> bool:
        """
        Discord REST API를 직접 호출하여 "중단" 메시지가 있는지 확인한다.
        리스너 스레드와 독립적으로 메인 스레드에서 호출된다.
        API 부하 방지를 위해 최소 _DISCORD_POLL_INTERVAL초 간격으로만 실제 요청한다.
        """
        if self._notifier is None:
            return False

        now = time.monotonic()
        if now - self._last_poll_time < self._DISCORD_POLL_INTERVAL:
            return False
        self._last_poll_time = now

        import httpx
        try:
            url = f"https://discord.com/api/v10/channels/{self._notifier.channel_id}/messages"
            params: dict = {"limit": 10}
            if self._discord_cursor:
                params["after"] = self._discord_cursor
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(url, headers=self._notifier._headers, params=params)
                if not resp.is_success:
                    status = resp.status_code
                    if self._discord_error_tracker is not None and (
                        status == 429 or 500 <= status < 600
                    ):
                        self._discord_error_tracker.record_failure(
                            f"HTTP {status} body={resp.text[:200]}"
                        )
                    else:
                        # 4xx auth/설정 문제 — 즉시 WARNING
                        logger.warning(
                            "[PauseController] Discord API 오류 (status=%d, body=%s)",
                            status, resp.text[:200],
                        )
                    return False

                if self._discord_error_tracker is not None:
                    self._discord_error_tracker.record_success()

                messages = resp.json()
                if not messages:
                    return False
                # 커서 전진 (모든 메시지, 봇 포함)
                self._discord_cursor = str(max(int(m["id"]) for m in messages))
                # 사용자 메시지만 추출
                user_msgs = [
                    m for m in messages
                    if not m.get("author", {}).get("bot", False)
                ]
                for msg in user_msgs:
                    content = msg.get("content", "").strip().lower()
                    if any(kw in content for kw in self._STOP_KEYWORDS):
                        logger.warning("[PauseController] Discord 직접 폴링으로 중단 감지: %r", content)
                        with self._lock:
                            self._stopped = True
                            self._resume_event.set()
                        # Discord에 확인 메시지 전송
                        try:
                            self._notifier.send("🛑 파이프라인 중단 요청 수신 (직접 폴링). 현재 태스크 완료 후 종료합니다.")
                        except Exception:
                            pass
                        return True
        except httpx.TimeoutException as e:
            if self._discord_error_tracker is not None:
                self._discord_error_tracker.record_failure(f"timeout: {e}")
            else:
                logger.debug("[PauseController] 폴링 타임아웃: %s", e)
        except httpx.HTTPError as e:
            if self._discord_error_tracker is not None:
                self._discord_error_tracker.record_failure(f"HTTPError: {e}")
            else:
                logger.debug("[PauseController] HTTP 오류: %s", e)
        except Exception as e:
            # 예상치 못한 예외 — 상위로 전파하지 않되, 진단을 위해 기록
            logger.error("[PauseController] Discord 직접 폴링 예외 (무시): %s", e, exc_info=True)
        return False

    # ── 상태 변경 (리스너 스레드 호출) ────────────────────────────────────────

    def handle_command(self, text: str) -> str | None:
        """
        Discord 메시지 텍스트를 파싱하여 명령을 처리한다.

        Returns:
            처리된 명령 문자열('paused'|'resumed'|'stopped'), 무관한 메시지는 None.
        """
        lower = text.lower().strip()
        with self._lock:
            if any(kw in lower for kw in self._STOP_KEYWORDS):
                self._stopped = True
                self._resume_event.set()  # 대기 중인 wait_if_paused 해제
                logger.info("PauseController: 중단 요청")
                return "stopped"
            if any(kw in lower for kw in self._PAUSE_KEYWORDS):
                self._paused = True
                self._resume_event.clear()
                logger.info("PauseController: 일시정지 요청")
                return "paused"
            if any(kw in lower for kw in self._RESUME_KEYWORDS):
                if self._paused:
                    self._paused = False
                    self._resume_event.set()
                    logger.info("PauseController: 재개 요청")
                    return "resumed"
        return None

    # ── 파이프라인 루프에서 호출 ───────────────────────────────────────────────

    @property
    def is_stopped(self) -> bool:
        with self._lock:
            if self._stopped:
                return True
        # 리스너 스레드 백업: 메인 스레드에서 직접 Discord 폴링
        return self._poll_discord_for_stop()

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def wait_if_paused(self) -> bool:
        """
        일시정지 상태면 재개될 때까지 블로킹한다.
        타임아웃(_PAUSE_RESUME_POLL 초)마다 깨어나 중단 여부를 재확인하여
        Discord 연결 불안정으로 재개 명령이 유실되어도 무한 블로킹하지 않는다.

        Returns:
            True  → 중단 요청이 들어와 파이프라인을 종료해야 함
            False → 정상 재개
        """
        while True:
            signaled = self._resume_event.wait(timeout=self._PAUSE_RESUME_POLL)
            if signaled or self.is_stopped:
                break
            # 타임아웃: 아직 일시정지 중이면 계속 대기, 재개됐으면 탈출
            if not self._paused:
                break
        return self._stopped


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _extract_orch_summary(report_text: str, max_len: int = 200) -> str:
    """orchestrator_report 마크다운에서 첫 번째 의미 있는 단락을 한 줄 요약으로 추출한다."""
    for line in report_text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            return stripped[:max_len]
    return ""


# ── .gitignore 자동 등록 ──────────────────────────────────────────────────────

_GITIGNORE_ENTRIES = [
    ".agent-workspace/",
    "agent-data/",
]

def _ensure_gitignore(repo_path: Path) -> None:
    """
    대상 레포의 .gitignore 에 에이전트 전용 경로가 없으면 추가한다.
    이미 있으면 건드리지 않는다.
    """
    gitignore = repo_path / ".gitignore"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        existing_lines = {line.strip() for line in existing.splitlines()}
        missing = [e for e in _GITIGNORE_ENTRIES if e not in existing_lines]
        if not missing:
            return
        block = "\n# AI coding agent (자동 추가)\n" + "\n".join(missing) + "\n"
        with open(gitignore, "a", encoding="utf-8") as f:
            f.write(block)
        logger.info(".gitignore 에 추가: %s", missing)
    except Exception as e:
        logger.warning(".gitignore 자동 등록 실패 (건너뜀): %s", e)


def _default_reports_dir(repo_path: Path) -> Path:
    """
    기본 리포트 디렉토리를 해석한다.

    - 우선 `repo_path/agent-data/reports`
    - 해당 디렉토리가 없고 `repo_path/data/reports`만 있으면 legacy 경로 사용
    """
    return resolve_reports_dir("agent-data/reports", base=repo_path)


def _default_logs_dir(repo_path: Path) -> Path:
    """기본 call_log 디렉토리를 반환한다."""
    return repo_path / "agent-data" / "logs"


# ── 파이프라인 실행 (CLI + API 공용) ──────────────────────────────────────────

def run_pipeline(
    tasks_path: Path,
    repo_path: Path,
    base_branch: str = "main",
    task_id: str | None = None,
    no_pr: bool = False,
    no_push: bool = False,
    verbose: bool = False,
    on_progress: object = None,
    reports_dir: Path | None = None,
    logs_dir: Path | None = None,
    pause_controller: "PauseController | None" = None,
    max_workers: int = 1,
    discord_channel_id: int | None = None,
    max_orchestrator_retries: int = 2,
    auto_merge: bool = False,
    provider: str = "claude",
    model_fast: str = "claude-haiku-4-5",
    model_capable: str = "claude-sonnet-4-6",
    provider_fast: str | None = None,    # None이면 provider 사용
    provider_capable: str | None = None, # None이면 provider 사용
    role_models: dict[str, RoleModelConfig] | None = None,  # 역할별 모델 오버라이드
    role_compaction_tuning_enabled: bool = False,
    role_compaction_tuning_preset: str = "balanced",
    role_compaction_tuning_overrides: dict[str, str] | None = None,
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

    # reports_dir 기본값: 대상 레포 안의 agent-data/reports (legacy data/reports 자동 fallback)
    if reports_dir is None:
        reports_dir = _default_reports_dir(repo_path)
    if logs_dir is None:
        logs_dir = _default_logs_dir(repo_path)

    # 파이프라인 시작 전 PROJECT_STRUCTURE.md 초기 생성 (없거나 오래된 경우)
    try:
        from structure.updater import update as _structure_update
        _structure_update(root=str(repo_path), output="PROJECT_STRUCTURE.md")
        logger.info("PROJECT_STRUCTURE.md 초기 생성 완료")
    except Exception as _e:
        logger.warning("PROJECT_STRUCTURE.md 초기 생성 실패 (건너뜀): %s", _e)

    # .gitignore 에 에이전트 전용 경로 등록 (없으면 추가, 있으면 no-op)
    _ensure_gitignore(repo_path)

    # 태스크 로드 (전체 — 의존성 검증에 전체 ID가 필요)
    all_tasks = load_tasks(tasks_path)
    all_task_ids = {t.id for t in all_tasks}  # 전체 ID 집합 (의존성 검증용)

    if task_id:
        all_tasks = [t for t in all_tasks if t.id == task_id]
        if not all_tasks:
            raise ValueError(f"태스크 ID '{task_id}'를 찾을 수 없습니다.")

    def emit(event: dict) -> None:
        if on_progress:
            on_progress(event)  # type: ignore[operator]

    # frontend 태스크는 멀티 에이전트 파이프라인 제외 (사람이 직접 구현)
    frontend_tasks = [t for t in all_tasks if t.task_type == "frontend"]
    if frontend_tasks:
        logger.info(
            "frontend 태스크 %d개 제외 (파이프라인 미실행): %s",
            len(frontend_tasks), [t.id for t in frontend_tasks],
        )
        emit({"type": "frontend_skipped", "task_ids": [t.id for t in frontend_tasks]})

    # DONE만 제외 — FAILED는 재시도 대상, frontend는 파이프라인 미실행
    pending = [t for t in all_tasks if t.status != TaskStatus.DONE and t.task_type != "frontend"]
    # FAILED 태스크는 새 실행을 위해 상태 초기화
    for t in pending:
        if t.status == TaskStatus.FAILED:
            t.status = TaskStatus.PENDING

    if not pending:
        return {"success": 0, "fail": 0, "tasks": [t.to_dict() for t in all_tasks]}

    # depends_on 정리 — 존재하지 않는 ID(삭제된 태스크) 참조 제거
    for t in pending:
        before = t.depends_on
        t.depends_on = [d for d in before if d in all_task_ids]
        removed = set(before) - set(t.depends_on)
        for r in removed:
            logger.warning("태스크 '%s'의 depends_on에서 없는 ID '%s' 자동 제거", t.id, r)
            emit({"type": "warn", "message": f"태스크 '{t.id}'의 depends_on에서 삭제된 ID '{r}' 제거"})

    # 의존성 기반 실행 그룹 계산
    # all_valid_ids: 완료된 태스크 ID도 포함해야 depends_on 검증을 통과할 수 있다
    groups = resolve_execution_groups(pending, all_valid_ids=all_task_ids)

    # LLM 클라이언트 + 파이프라인 초기화
    _provider_fast = provider_fast or provider
    _provider_capable = provider_capable or provider
    fast_llm = create_client(_provider_fast, LLMConfig(model=model_fast, max_tokens=8192))
    capable_llm = create_client(_provider_capable, LLMConfig(model=model_capable, max_tokens=8192))
    runner = DockerTestRunner()

    pipeline = TDDPipeline(
        agent_llm=fast_llm, implementer_llm=fast_llm, test_runner=runner,
        role_models=role_models,
        provider=provider,
        model_fast=model_fast,
        model_capable=model_capable,
        provider_fast=provider_fast,
        provider_capable=provider_capable,
        role_compaction_tuning_enabled=role_compaction_tuning_enabled,
        role_compaction_tuning_preset=role_compaction_tuning_preset,
        role_compaction_tuning_overrides=role_compaction_tuning_overrides,
    )
    git = GitWorkflow(repo_path, base_branch=base_branch)
    _merge_provider, _merge_model = resolve_model_for_role(
        role=ROLE_MERGE_AGENT,
        role_models=role_models,
        provider=provider,
        model_fast=model_fast,
        model_capable=model_capable,
        provider_fast=provider_fast,
        provider_capable=provider_capable,
    )
    merge_llm = create_client(_merge_provider, LLMConfig(model=_merge_model, max_tokens=8192))
    merge_agent = MergeAgent(llm=merge_llm, repo_path=repo_path)
    # discord_channel_id가 없으면 notifier 생성 안 함 (채널 생성 실패 포함)
    notifier = DiscordNotifier.from_env(channel_id=discord_channel_id) if discord_channel_id else None
    # 에이전트 ask_user 도구에 notifier + LLM 주입 (None이면 stdin 폴백)
    _set_hotline_notifier(notifier)
    _set_hotline_repo_path(repo_path)
    _set_hotline_tasks_path(tasks_path)
    conv_llm, sum_llm = create_hotline_llms(_provider_capable, model_capable)
    _set_hotline_llm(conv_llm, sum_llm)
    # 오케스트레이터 개입 LLM 주입
    analyze_llm, report_llm = create_intervention_llms(_provider_capable, model_capable)
    _set_intervention_llm(analyze_llm, report_llm)
    _set_intervention_model_config(provider, model_fast, model_capable, provider_fast, provider_capable)

    # ── auto_merge catch-up: 이전 실행에서 완료됐지만 미머지된 브랜치 처리 ────────
    if auto_merge and not no_pr:
        _catchup_merge(
            all_tasks=all_tasks,
            pending_ids={t.id for t in pending},
            base_branch=base_branch,
            repo_path=repo_path,
            merge_agent=merge_agent,
            all_task_ids=all_task_ids,
            runner=runner,
            notifier=notifier,
            emit=emit,
        )

    # ── 일시정지 컨트롤러 + Discord 명령 리스너 스레드 ──────────────────────────
    pause_ctrl = pause_controller if pause_controller is not None else PauseController()
    _set_hotline_pause_controller(pause_ctrl)
    _listener_stop = threading.Event()

    def _on_discord_command(text: str) -> None:
        logger.info("[discord_cmd] 일반 콜백 수신: %r", text[:100])
        result_cmd = pause_ctrl.handle_command(text)
        logger.info("[discord_cmd] handle_command 결과: %s", result_cmd)
        if result_cmd == "paused":
            _notify(notifier, "⏸ 파이프라인 일시정지 예약됨. 현재 태스크 완료 후 대기합니다.\n재개하려면 '계속', 완전 중단은 '중단'을 입력하세요.")
            emit({"type": "step", "step": "paused",
                  "message": "일시정지 예약됨 — 현재 태스크 완료 후 대기"})
        elif result_cmd == "resumed":
            _notify(notifier, "▶ 파이프라인 재개합니다.")
            emit({"type": "step", "step": "resumed", "message": "파이프라인 재개"})
        elif result_cmd == "stopped":
            _notify(notifier, "🛑 파이프라인 중단 요청 수신. 현재 태스크 완료 후 종료합니다.")
            emit({"type": "step", "step": "stopped",
                  "message": "중단 요청 — 현재 태스크 완료 후 종료"})

    def _on_urgent_command(text: str) -> bool:
        """핫라인 대화 중에도 중단 명령은 즉시 처리한다. 처리했으면 True 반환.
        일시정지/재개는 핫라인 대화 맥락에서 오탐 가능성이 높아 여기서는 처리하지 않는다."""
        lower = text.strip().lower()
        if not any(kw in lower for kw in PauseController._STOP_KEYWORDS):
            return False
        logger.info("[urgent] 중단 키워드 감지: %r → handle_command 호출", text)
        result_cmd = pause_ctrl.handle_command(text)
        logger.info("[urgent] handle_command 결과: %s", result_cmd)
        if result_cmd == "stopped":
            _notify(notifier, "🛑 파이프라인 중단 요청 수신. 현재 태스크 완료 후 종료합니다.")
            emit({"type": "step", "step": "stopped",
                  "message": "중단 요청 — 현재 태스크 완료 후 종료"})
            return True
        return False

    def _on_empty_content_warning() -> None:
        logger.error(
            "[listener] content 빈 문자열 감지 — Message Content Intent 미활성 가능성이 큼"
        )
        emit(
            {
                "type": "warn",
                "message": (
                    "Discord 메시지 content가 비어 있습니다. "
                    "Message Content Intent가 비활성화되어 명령이 무시될 수 있습니다."
                ),
            }
        )
        _notify(
            notifier,
            "⚠️ Discord Message Content Intent 미활성 가능성 감지.\n"
            "사용자 메시지의 content가 비어 명령(멈춰/계속/중단/확정)이 무시될 수 있습니다.\n"
            "Developer Portal → Bot → Privileged Gateway Intents에서 "
            "Message Content Intent를 활성화하세요.",
        )

    if notifier:
        # 파이프라인 시작 전 최신 메시지 ID를 기준점으로 사용
        _baseline_id = notifier.get_latest_message_id()
        # 메인 스레드 직접 폴링 (리스너 스레드 백업) — is_stopped 호출 시 자동 폴링
        pause_ctrl.attach_notifier(notifier, after_message_id=_baseline_id)
        logger.info("[PauseController] Discord 직접 폴링 활성화 (channel=%s, baseline=%s)", notifier.channel_id, _baseline_id)
        _listener_thread = threading.Thread(
            target=notifier.listen_for_commands,
            kwargs={
                "callback": _on_discord_command,
                "after_message_id": _baseline_id,
                "stop_event": _listener_stop,
                "skip_check": _is_hotline_active,
                "urgent_callback": _on_urgent_command,
                "empty_content_warning_callback": _on_empty_content_warning,
            },
            daemon=True,
            name="discord-command-listener",
        )
        _listener_thread.start()
        logger.info("[listener] Discord 명령 리스너 스레드 시작 (baseline_id=%s)", _baseline_id)
    else:
        _listener_thread = None

    def _check_listener_alive() -> None:
        """리스너 스레드가 죽었으면 경고 로그를 남기고 재시작을 시도한다."""
        nonlocal _listener_thread
        if _listener_thread is None or not notifier:
            return
        if not _listener_thread.is_alive():
            logger.error("⚠️ Discord 명령 리스너 스레드가 죽었습니다! 재시작 시도…")
            _notify(notifier, "⚠️ 내부 오류: Discord 명령 리스너가 중단되었습니다. 재시작합니다.\n'중단' 명령이 작동하지 않았을 수 있습니다.")
            try:
                new_baseline = notifier.get_latest_message_id()
                _listener_thread = threading.Thread(
                    target=notifier.listen_for_commands,
                    kwargs={
                        "callback": _on_discord_command,
                        "after_message_id": new_baseline,
                        "stop_event": _listener_stop,
                        "skip_check": _is_hotline_active,
                        "urgent_callback": _on_urgent_command,
                        "empty_content_warning_callback": _on_empty_content_warning,
                    },
                    daemon=True,
                    name="discord-command-listener-respawn",
                )
                _listener_thread.start()
                logger.info("Discord 명령 리스너 스레드 재시작 성공 (baseline_id=%s)", new_baseline)
            except Exception as e:
                logger.error("Discord 명령 리스너 스레드 재시작 실패: %s", e, exc_info=True)

    success_count = 0
    fail_count = 0
    failed_ids: set[str] = set()     # 실패/스킵된 태스크 ID 추적 (의존성 스킵용)
    _save_lock = threading.Lock()   # tasks.yaml 파일 쓰기 직렬화

    def run_one(task: Task) -> None:
        """태스크 하나를 실행한다. 실패 시 오케스트레이터가 개입하여 재시도한다."""
        nonlocal success_count, fail_count

        start_time = time.monotonic()
        emit({"type": "task_start", "task_id": task.id, "title": task.title})
        _notify(notifier, f"🚀 [{task.id}] \"{task.title}\" 시작")

        hints_tried: list[str] = []

        for orch_attempt in range(max_orchestrator_retries + 1):
            # 리스너 스레드 생존 확인 + 중단 여부 확인
            _check_listener_alive()
            if pause_ctrl.is_stopped:
                logger.info("[%s] 중단 요청 — 오케스트레이터 루프 종료", task.id)
                return
            with WorkspaceManager(task, repo_path, keep_on_failure=True) as ws:
                # 선행 태스크 산출물 주입 (depends_on 연결된 완료 태스크)
                if task.depends_on:
                    dep_tasks = [t for t in all_tasks if t.id in task.depends_on]
                    ws.inject_dependency_context(dep_tasks)

                if orch_attempt > 0:
                    emit({"type": "step", "task_id": task.id, "step": "orch_retry",
                          "message": f"오케스트레이터 재시도 {orch_attempt}/{max_orchestrator_retries}…"})

                def _progress(event: dict, _tid=task.id) -> None:
                    emit({**event, "task_id": _tid})

                result = pipeline.run(task, ws, on_progress=_progress, pause_ctrl=pause_ctrl,
                                     all_tasks=all_tasks, repo_path=repo_path)
                elapsed = time.monotonic() - start_time

                # ── 성공 ────────────────────────────────────────────────────────
                if result.succeeded:
                    pr_url = ""
                    if not no_pr:
                        # test_pass / review 결과는 pipeline on_progress 콜백이 이미 emit했음
                        git_msg = ("브랜치 → 커밋 중… (push/PR 건너뜀)"
                                   if no_push else "브랜치 → 커밋 → 푸시 → PR 생성 중…")
                        emit({"type": "step", "task_id": task.id, "step": "git",
                              "message": git_msg})
                        try:
                            pr_url = git.run(task, ws, result, no_push=no_push)
                            task.pr_url = pr_url
                        except GitWorkflowError as e:
                            task.status = TaskStatus.FAILED
                            task.failure_reason = str(e)
                            with _save_lock:
                                fail_count += 1
                            emit({"type": "task_fail", "task_id": task.id, "title": task.title,
                                  "reason": str(e), "elapsed": round(elapsed, 1)})
                            _notify_failure(notifier, task, str(e), elapsed)
                            report = build_report(task, result, elapsed_seconds=elapsed, pr_url="",
                                                  models_used=result.models_used or None,
                                                  call_logs_dir=logs_dir)
                            save_report(report, reports_dir=reports_dir)
                            with _save_lock:
                                save_tasks(all_tasks, tasks_path)
                            return

                    task.status = TaskStatus.DONE
                    with _save_lock:
                        success_count += 1
                    if pr_url:
                        emit({"type": "task_done", "task_id": task.id, "title": task.title,
                              "pr_url": pr_url, "elapsed": round(elapsed, 1)})
                        _notify(notifier,
                                f"✅ [{task.id}] \"{task.title}\" 완료! PR: {pr_url}  (⏱ {elapsed:.0f}s)")
                    else:
                        emit({"type": "task_done", "task_id": task.id, "title": task.title,
                              "elapsed": round(elapsed, 1)})
                        _notify(notifier, f"✅ [{task.id}] \"{task.title}\" 완료! (⏱ {elapsed:.0f}s)")

                    report = build_report(task, result, elapsed_seconds=elapsed, pr_url=pr_url,
                                          models_used=result.models_used or None,
                                          call_logs_dir=logs_dir)
                    save_report(report, reports_dir=reports_dir)
                    with _save_lock:
                        save_tasks(all_tasks, tasks_path)
                    return

                # ── 실패 ────────────────────────────────────────────────────────
                failure_reason = result.failure_reason or "알 수 없음"
                is_aborted = failure_reason.startswith("[ABORTED]")
                is_max_iter = failure_reason.startswith("[MAX_ITER]")

                # 즉시 중단 요청 — 오케스트레이터 재시도 없이 바로 태스크 실패 처리
                if is_aborted:
                    logger.info("[%s] 즉시 중단으로 태스크 종료", task.id)
                    with _save_lock:
                        fail_count += 1
                    failed_ids.add(task.id)
                    emit({"type": "task_aborted", "task_id": task.id, "title": task.title,
                          "elapsed": round(elapsed, 1)})
                    with _save_lock:
                        save_tasks(all_tasks, tasks_path)
                    return

                if is_max_iter:
                    logger.warning(
                        "[%s] ⚠️ 최대 반복 횟수 초과 (오케스트레이터 시도 %d/%d): %s",
                        task.id, orch_attempt + 1, max_orchestrator_retries + 1, task.title,
                    )

                is_last_attempt = (orch_attempt >= max_orchestrator_retries)

                if not is_last_attempt:
                    # ── 오케스트레이터 개입 전 중단 체크 ─────────────────────
                    if pause_ctrl.is_stopped:
                        logger.info("[%s] 중단 요청 — 오케스트레이터 분석 생략", task.id)
                        return
                    # ── 오케스트레이터 개입: 분석 후 재시도 결정 ─────────────
                    reason_snippet = failure_reason.replace("[MAX_ITER] ", "")[:200]
                    logger.warning(
                        "[%s] 실패 (시도 %d/%d) — 오케스트레이터 분석 시작\n  원인: %s",
                        task.id, orch_attempt + 1, max_orchestrator_retries + 1, reason_snippet,
                    )
                    _notify(notifier,
                            f"🔍 [{task.id}] 실패 (시도 {orch_attempt + 1}/{max_orchestrator_retries + 1})"
                            f" — 오케스트레이터 분석 중…\n"
                            f"원인: {reason_snippet}")
                    emit({"type": "orchestrator_analyzing", "task_id": task.id,
                          "title": task.title, "attempt": orch_attempt + 1,
                          "max_attempts": max_orchestrator_retries + 1,
                          "failure_reason": failure_reason, "is_max_iter": is_max_iter})

                    test_stdout = result.test_result.stdout if result.test_result else ""
                    analysis = orch_classify_and_analyze(
                        task, failure_reason, orch_attempt + 1,
                        test_stdout=test_stdout,
                        previous_hints=hints_tried,
                        role_models=role_models,
                    )
                    _accumulate_external_tokens(
                        result.metrics,
                        "intervention",
                        analysis.token_usage,
                        analysis.call_log,
                    )
                    if analysis.call_log:
                        result.models_used["intervention"] = analysis.call_log[-1].get("model", "")

                    # orch_analyze 완료 후 중단 체크 (분석 중 중단 명령 수신 가능)
                    if pause_ctrl.is_stopped:
                        logger.info("[%s] 중단 요청 — 오케스트레이터 재시도 취소", task.id)
                        return

                    if analysis.should_retry:
                        hints_tried.append(analysis.hint)
                        task.last_error = (
                            f"[오케스트레이터 힌트 #{orch_attempt + 1}]\n{analysis.hint}\n\n"
                            f"이전 오류:\n{task.last_error or ''}"
                        )
                        task.failure_reason = ""  # 초기화 (다음 시도 전)
                        logger.warning(
                            "[%s] 오케스트레이터 → RETRY (시도 %d → %d)\n  힌트: %s",
                            task.id, orch_attempt + 1, orch_attempt + 2, analysis.hint[:150],
                        )
                        _notify(notifier,
                                f"🔄 [{task.id}] 오케스트레이터 재시도 결정 "
                                f"({orch_attempt + 1} → {orch_attempt + 2}회차)\n"
                                f"💡 힌트: {analysis.hint[:400]}")
                        emit({"type": "orchestrator_retry", "task_id": task.id,
                              "title": task.title, "attempt": orch_attempt + 1,
                              "next_attempt": orch_attempt + 2,
                              "max_attempts": max_orchestrator_retries + 1,
                              "hint": analysis.hint, "failure_reason": failure_reason})
                        continue  # 다음 orch_attempt — 새 WorkspaceManager로 재실행

                    else:
                        # GIVE_UP — 더 이상 시도하지 않음
                        logger.warning(
                            "[%s] 오케스트레이터 → GIVE_UP (시도 %d/%d)\n  이유: %s",
                            task.id, orch_attempt + 1, max_orchestrator_retries + 1, analysis.hint[:150],
                        )
                        _notify(notifier,
                                f"🛑 [{task.id}] 오케스트레이터 포기 결정 "
                                f"(시도 {orch_attempt + 1}/{max_orchestrator_retries + 1})\n"
                                f"이유: {analysis.hint[:300]}")
                        emit({"type": "orchestrator_giveup", "task_id": task.id,
                              "title": task.title, "attempt": orch_attempt + 1,
                              "reason": analysis.hint, "failure_reason": failure_reason})
                        is_last_attempt = True  # 아래 최종 실패 처리로 넘어감

                # ── 최종 실패 처리 ───────────────────────────────────────────
                task.status = TaskStatus.FAILED
                task.failure_reason = failure_reason
                with _save_lock:
                    fail_count += 1

                # 오케스트레이터가 1회 이상 개입한 경우 → 상세 보고서 생성
                if hints_tried:
                    logger.warning(
                        "[%s] 오케스트레이터 최종 실패 (%d회 시도) — 보고서 생성 중…",
                        task.id, orch_attempt + 1,
                    )
                    emit({"type": "orchestrator_report_generating", "task_id": task.id,
                          "title": task.title, "total_attempts": orch_attempt + 1})
                    _notify(notifier,
                            f"📊 [{task.id}] \"{task.title}\" "
                            f"오케스트레이터 {orch_attempt + 1}회 시도 후 최종 실패\n"
                            f"보고서 생성 중…")

                    report_result = orch_report_with_metrics(
                        task, failure_reason, orch_attempt + 1, hints_tried,
                        orchestrator_model=model_capable,
                        coding_agent_model=model_fast,
                        models_used=result.models_used or None,
                    )
                    _accumulate_external_tokens(
                        result.metrics,
                        "intervention",
                        report_result.token_usage,
                        report_result.call_log,
                    )
                    if report_result.call_log:
                        result.models_used["intervention"] = report_result.call_log[-1].get("model", "")
                    orch_report_text = report_result.text
                    report_path = orch_save_report(orch_report_text, task.id, reports_dir)
                    logger.warning(
                        "[%s] 오케스트레이터 보고서 저장 완료: %s", task.id, report_path,
                    )
                    _notify(notifier,
                            f"📋 [{task.id}] **오케스트레이터 실패 보고서**\n"
                            f"파일: {report_path.name}\n\n"
                            f"{orch_report_text[:800]}"
                            f"{'…(이하 생략)' if len(orch_report_text) > 800 else ''}")
                    emit({"type": "orchestrator_report", "task_id": task.id,
                          "title": task.title, "total_attempts": orch_attempt + 1,
                          "report": orch_report_text, "report_path": str(report_path)})
                else:
                    orch_report_text = ""
                    # 오케스트레이터 개입 없이 첫 시도에서 실패 (max_orchestrator_retries=0 등)
                    if is_max_iter:
                        _notify(notifier,
                                f"⚠️ [{task.id}] \"{task.title}\" **최대 반복 횟수 초과**\n"
                                f"에이전트가 루프를 탈출하지 못했습니다.\n"
                                f"태스크를 더 작게 분할하거나 아래에 힌트를 입력하세요.")

                emit({"type": "task_fail", "task_id": task.id, "title": task.title,
                      "reason": task.failure_reason, "is_max_iter": is_max_iter,
                      "elapsed": round(elapsed, 1)})
                _notify_failure(notifier, task, task.failure_reason, elapsed)

                report = build_report(
                    task, result, elapsed_seconds=elapsed, pr_url="",
                    orchestrator_attempts=orch_attempt + 1 if hints_tried else 0,
                    orchestrator_model=model_capable if hints_tried else "",
                    coding_agent_model=model_fast if hints_tried else "",
                    orchestrator_summary=_extract_orch_summary(orch_report_text),
                    models_used=result.models_used or None,
                    call_logs_dir=logs_dir,
                )
                save_report(report, reports_dir=reports_dir)
                with _save_lock:
                    save_tasks(all_tasks, tasks_path)
                return

    def skip_one(task: Task, blocked_by: list[str]) -> None:
        """의존 태스크 실패로 건너뛸 태스크를 처리한다."""
        nonlocal fail_count
        reason = f"의존 태스크 실패로 건너뜀: {', '.join(blocked_by)}"
        task.status = TaskStatus.FAILED
        task.failure_reason = reason
        failed_ids.add(task.id)
        with _save_lock:
            fail_count += 1
            save_tasks(all_tasks, tasks_path)
        emit({"type": "task_skip", "task_id": task.id, "title": task.title, "reason": reason})
        logger.info("스킵: [%s] %s", task.id, reason)

    emit({"type": "pipeline_start", "total": len(pending),
          "tasks": [t.id for t in pending]})
    _notify(notifier, f"📋 파이프라인 시작 — {len(pending)}개 태스크 / 에이전트 {max_workers}개\n'멈춰' 입력 시 일시정지, '중단' 입력 시 종료")

    _pipeline_aborted = False   # break 사유 추적 — finally 이후 이벤트 분기에 사용

    try:
        for group in groups:
            # ── 리스너 스레드 생존 확인 ────────────────────────────────────────
            _check_listener_alive()

            # ── 그룹 시작 전 일시정지/중단 체크 ─────────────────────────────────
            if pause_ctrl.is_paused:
                first_id = group[0].id if group else "?"
                _notify(notifier,
                        f"⏸ 일시정지됨. '계속' 입력 시 다음 그룹 [{first_id}…] 부터 재개합니다.")
                emit({"type": "paused", "next_task_id": first_id,
                      "message": f"일시정지 — '계속' 입력 시 {first_id} 재개"})
                should_stop = pause_ctrl.wait_if_paused()
                if should_stop:
                    _pipeline_aborted = True
                    emit({"type": "pipeline_aborted",
                          "message": "사용자 중단 요청으로 파이프라인 종료"})
                    break
                emit({"type": "resumed", "task_id": first_id, "message": f"{first_id} 재개"})

            if pause_ctrl.is_stopped:
                _pipeline_aborted = True
                emit({"type": "pipeline_aborted",
                      "message": "사용자 중단 요청으로 파이프라인 종료"})
                break

            # ── 그룹 내 태스크 실행 (의존성 실패 시 스킵) ────────────────────
            runnable = []
            for task in group:
                blocked_by = [d for d in task.depends_on if d in failed_ids]
                if blocked_by:
                    skip_one(task, blocked_by)
                else:
                    runnable.append(task)

            workers = min(max_workers, len(runnable))
            if workers > 1:
                emit({"type": "step", "step": "parallel",
                      "message": f"그룹 {len(runnable)}개 태스크를 에이전트 {workers}개로 병렬 실행"})
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {executor.submit(run_one, task): task for task in runnable}
                    for future in as_completed(futures):
                        exc = future.exception()
                        if exc:
                            t = futures[future]
                            logger.error("[%s] 예외 발생: %s", t.id, exc)
                            t.status = TaskStatus.FAILED
                            t.failure_reason = str(exc)
            else:
                for task in runnable:
                    if pause_ctrl.is_stopped:
                        break
                    run_one(task)

            # 이 그룹에서 실패한 태스크 ID를 failed_ids에 추가
            group_failed = [t.id for t in runnable if t.status == TaskStatus.FAILED]
            for tid in group_failed:
                failed_ids.add(tid)

            # ── 그룹 전체 실패 시 계속 진행 여부 확인 ────────────────────────
            remaining = groups[groups.index(group) + 1:] if group in groups else []
            if group_failed and len(group_failed) == len(runnable) and remaining:
                should_continue = _ask_continue(notifier, group_failed, len(remaining), pause_ctrl)
                if not should_continue:
                    _pipeline_aborted = True
                    emit({"type": "pipeline_aborted",
                          "message": "사용자 요청으로 파이프라인 중단"})
                    _notify(notifier, "🛑 파이프라인을 중단합니다.")
                    break

            # ── 그룹 완료 후 자동 머지 ────────────────────────────────────────
            if auto_merge and not no_pr:
                done_branches = [
                    t.branch_name for t in group
                    if t.status == TaskStatus.DONE
                ]
                if done_branches:
                    _auto_merge_group(
                        branches=done_branches,
                        base_branch=base_branch,
                        repo_path=repo_path,
                        merge_agent=merge_agent,
                        test_runner=runner,
                        notifier=notifier,
                        emit=emit,
                    )

            # 병렬/순차 실행 중 stop이 들어온 경우: 현재 그룹 태스크가 완료된 후 여기서 감지
            # (설계 의도: 실행 중인 태스크는 강제 중단 없이 완료 후 종료)
            if pause_ctrl.is_stopped:
                _pipeline_aborted = True
                emit({"type": "pipeline_aborted",
                      "message": "사용자 중단 요청으로 파이프라인 종료"})
                _notify(notifier, "🛑 파이프라인을 중단합니다.")
                break

    finally:
        # 리스너 스레드 종료
        _listener_stop.set()

    if _pipeline_aborted:
        done_tasks = [t for t in all_tasks if t.status == TaskStatus.DONE]
        failed_tasks = [t for t in all_tasks if t.status == TaskStatus.FAILED]
        pending_tasks = [t for t in all_tasks if t.status not in (TaskStatus.DONE, TaskStatus.FAILED)]
        lines = ["⛔ **파이프라인이 중단되었습니다.**", ""]
        if done_tasks:
            lines.append(f"✅ 완료: {', '.join(t.id for t in done_tasks)} ({len(done_tasks)}개)")
        if failed_tasks:
            lines.append(f"❌ 실패: {', '.join(t.id for t in failed_tasks)} ({len(failed_tasks)}개)")
        if pending_tasks:
            lines.append(f"⏸ 미실행: {', '.join(t.id for t in pending_tasks)} ({len(pending_tasks)}개)")
        emit({"type": "pipeline_aborted_summary",
              "success": success_count, "fail": fail_count})
        _notify(notifier, "\n".join(lines))
    else:
        emit({"type": "pipeline_done", "success": success_count, "fail": fail_count})
        # 파이프라인 메트릭 집계
        metrics_summary = _build_pipeline_metrics_summary(all_tasks, reports_dir)
        _notify(
            notifier,
            f"🏁 파이프라인 완료 — 성공: {success_count}  실패: {fail_count}\n{metrics_summary}",
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
    no_push: bool,
    notifier,
    save_lock: threading.Lock,
    all_tasks: list[Task],
    tasks_path: Path,
    reports_dir: Path = Path("agent-data/reports"),
    on_progress=None,   # Callable[[dict], None] | None
) -> tuple[bool, str]:
    """
    태스크 하나를 실행하고 (succeeded, branch_name) 을 반환한다.

    Thread-safe:
    - WorkspaceManager 는 태스크마다 독립 디렉토리 사용
    - GitWorkflow.run() 은 worktree 기반으로 main repo HEAD 불변
    - save_lock 은 tasks.yaml 파일 쓰기 직렬화에 사용
    """
    def emit(event: dict) -> None:
        if on_progress:
            on_progress(event)

    print(f"\n{'─' * 60}")
    print(f"{_BOLD}[{task.id}]{_RESET} {task.title}")
    if task.depends_on:
        print(f"  선행 완료: {', '.join(task.depends_on)}")
    print(f"{'─' * 60}")

    start_time = time.monotonic()
    _notify(notifier, f"🚀 [{task.id}] \"{task.title}\" 시작")
    emit({"type": "task_start", "task_id": task.id, "title": task.title})

    with WorkspaceManager(task, repo_path, keep_on_failure=True) as ws:
        # 선행 태스크 산출물 주입
        if task.depends_on:
            dep_tasks = [t for t in all_tasks if t.id in task.depends_on]
            ws.inject_dependency_context(dep_tasks)

        print(_info(f"[{task.id}] TestWriter → Implementer → Docker → Reviewer ..."))

        def _progress(event: dict, _tid=task.id) -> None:
            emit({**event, "task_id": _tid})

        result = pipeline.run(task, ws, on_progress=_progress,
                             all_tasks=all_tasks, repo_path=repo_path)
        elapsed = time.monotonic() - start_time

        pr_url = ""
        branch = ""
        succeeded = False

        if not result.succeeded:
            print(_fail(f"[{task.id}] 파이프라인 실패: {result.failure_reason}"))
            print(f"  workspace 보존됨: {ws.path}")
            emit({"type": "task_fail", "task_id": task.id, "title": task.title,
                  "reason": result.failure_reason or "알 수 없음", "elapsed": round(elapsed, 1)})
            _notify_failure(notifier, task, result.failure_reason or "알 수 없음", elapsed)
        else:
            # test_pass / review 결과는 pipeline on_progress 콜백이 이미 emit했음
            if result.test_result:
                print(_ok(f"[{task.id}] 테스트: {result.test_result.summary}"))
            if result.review:
                icon = "✅" if result.review.approved else "⚠️"
                print(f"  {icon} [{task.id}] 리뷰: {result.review.verdict} — {result.review.summary}")

            if no_pr:
                print(_warn(f"[{task.id}] --no-pr: PR 생성 건너뜀"))
                task.status = TaskStatus.DONE
                succeeded = True
                emit({"type": "task_done", "task_id": task.id, "title": task.title,
                      "elapsed": round(elapsed, 1)})
                _notify(notifier, f"✅ [{task.id}] \"{task.title}\" 완료! (⏱ {elapsed:.0f}s)")
            else:
                git_msg = ("브랜치 → 커밋 중… (push/PR 건너뜀)"
                           if no_push else "브랜치 → 커밋 → 푸시 → PR 생성 중…")
                print(_info(f"[{task.id}] {git_msg}"))
                emit({"type": "step", "task_id": task.id, "step": "git", "message": git_msg})
                try:
                    pr_url = git.run(task, ws, result, no_push=no_push)
                    task.pr_url = pr_url
                    task.status = TaskStatus.DONE
                    branch = task.branch_name
                    succeeded = True
                    print(_ok(f"[{task.id}] PR: {pr_url}"))
                    emit({"type": "task_done", "task_id": task.id, "title": task.title,
                          "pr_url": pr_url, "elapsed": round(elapsed, 1)})
                    _notify(
                        notifier,
                        f"✅ [{task.id}] \"{task.title}\" 완료! PR: {pr_url}  (⏱ {elapsed:.0f}s)",
                    )
                except GitWorkflowError as e:
                    print(_fail(f"[{task.id}] Git 워크플로우 실패: {e}"))
                    emit({"type": "task_fail", "task_id": task.id, "title": task.title,
                          "reason": str(e), "elapsed": round(elapsed, 1)})
                    _notify_failure(notifier, task, str(e), elapsed)

        report = build_report(task, result, elapsed_seconds=elapsed, pr_url=pr_url,
                              models_used=result.models_used or None,
                              call_logs_dir=logs_dir)
        report_path = save_report(report, reports_dir=reports_dir)
        print(f"  [{task.id}] 리포트: {report_path}")

        with save_lock:
            save_tasks(all_tasks, tasks_path)

    return succeeded, branch


# ── 자동 머지 헬퍼 ────────────────────────────────────────────────────────────


def _is_branch_merged(branch: str, base_branch: str, repo_path: Path) -> bool:
    """origin/{branch}가 base_branch의 조상인지 확인한다 (즉, 이미 머지됐는지)."""
    import subprocess
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", f"origin/{branch}", base_branch],
        capture_output=True,
        cwd=str(repo_path),
    )
    return result.returncode == 0


def _remote_branch_exists(branch: str, repo_path: Path) -> bool:
    """origin/{branch}가 원격에 존재하는지 확인한다."""
    import subprocess
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        capture_output=True,
        cwd=str(repo_path),
    )
    return result.returncode == 0


def _catchup_merge(
    all_tasks: list,
    pending_ids: set,
    base_branch: str,
    repo_path: Path,
    merge_agent: MergeAgent,
    all_task_ids: set,
    runner,
    notifier,
    emit,
) -> None:
    """
    이전 실행에서 DONE됐지만 base_branch에 아직 머지 안 된 브랜치를
    의존성 순서(위상 정렬)대로 머지한다.

    재개 시 auto_merge가 처음 켜진 경우를 위한 catch-up 단계.
    """
    done_tasks = [t for t in all_tasks if t.id not in pending_ids and t.status == TaskStatus.DONE]
    if not done_tasks:
        return

    # 원격 브랜치가 존재하고 아직 미머지인 태스크만 필터링
    unmerged = [
        t for t in done_tasks
        if _remote_branch_exists(t.branch_name, repo_path)
        and not _is_branch_merged(t.branch_name, base_branch, repo_path)
    ]
    if not unmerged:
        return

    # 의존성 순서(위상 정렬)로 정렬 — 선행 태스크를 먼저 머지
    try:
        ordered_groups = resolve_execution_groups(unmerged, all_valid_ids=all_task_ids)
    except ValueError:
        # 순환 의존성 등 예외 — 순서 무시하고 그냥 진행
        ordered_groups = [unmerged]

    branches_in_order = [t.branch_name for group in ordered_groups for t in group]

    logger.warning(
        "catch-up 머지: %d개 미머지 브랜치를 순서대로 머지합니다: %s",
        len(branches_in_order), branches_in_order,
    )
    _notify(notifier,
            f"🔁 catch-up 머지 — 이전 실행에서 완료됐지만 미머지된 브랜치 {len(branches_in_order)}개를 먼저 처리합니다.\n"
            f"순서: {' → '.join(branches_in_order)}")
    emit({"type": "catchup_merge_start", "branches": branches_in_order,
          "count": len(branches_in_order)})

    _auto_merge_group(
        branches=branches_in_order,
        base_branch=base_branch,
        repo_path=repo_path,
        merge_agent=merge_agent,
        test_runner=runner,
        notifier=notifier,
        emit=emit,
    )


def _auto_merge_group(
    branches: list[str],
    base_branch: str,
    repo_path: Path,
    merge_agent: MergeAgent,
    test_runner: "DockerTestRunner | None" = None,
    notifier=None,
    emit=None,
) -> None:
    """
    그룹 내 agent 브랜치들을 base_branch 에 순서대로 머지하고 push 한다.

    충돌 발생 시 MergeAgent(LLM)가 자동 해결한다.
    test_runner 가 주어지면 전체 머지 완료 후 테스트를 실행하고,
    실패 시 머지 커밋들을 모두 되돌린다.
    """
    import subprocess

    def git(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + args, capture_output=True, text=True, cwd=str(repo_path)
        )

    def _emit(event: dict) -> None:
        if emit:
            emit(event)

    def _log(msg: str) -> None:
        print(msg)
        logger.info(msg)

    # 현재 브랜치 저장 → base_branch 로 이동
    original = git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    pre_merge_sha = git(["rev-parse", "HEAD"]).stdout.strip()

    # 이전 머지가 중단된 채 MERGE_HEAD가 남아 있으면 먼저 정리
    if (repo_path / ".git" / "MERGE_HEAD").exists():
        logger.warning("진행 중인 머지 발견 — merge --abort로 정리 후 checkout 시도")
        git(["merge", "--abort"])

    checkout = git(["checkout", base_branch])
    if checkout.returncode != 0:
        msg = f"자동 머지 건너뜀 — {base_branch} checkout 실패: {checkout.stderr.strip()}"
        print(_warn(msg))
        _notify(notifier, f"⚠️ 자동 머지 건너뜀: {msg}")
        return

    _log(f"\n→ {base_branch} 자동 머지 시작 ({len(branches)}개 브랜치)")
    _notify(notifier, f"🔀 {base_branch} 자동 머지 시작 — {len(branches)}개 브랜치")
    _emit({"type": "merge_start", "base_branch": base_branch,
           "branches": branches, "count": len(branches)})

    merged_count = 0
    for branch in branches:
        _emit({"type": "merge_branch", "branch": branch, "base_branch": base_branch})
        merge_result = merge_agent.merge_branch(branch, base_branch=base_branch)
        if merge_result.success:
            merged_count += 1
            resolved = merge_result.conflicts_resolved
            if resolved:
                msg = f"머지: {branch}  (충돌 {resolved}개 자동 해결)"
                print(_ok(msg))
                _notify(notifier, f"✅ {msg}")
                _emit({"type": "merge_done", "branch": branch,
                       "conflicts_resolved": resolved})
            else:
                msg = f"머지: {branch}"
                print(_ok(msg))
                _emit({"type": "merge_done", "branch": branch,
                       "conflicts_resolved": 0})
        else:
            msg = f"머지 실패: {branch} — {merge_result.error}"
            print(_warn(msg))
            logger.warning(msg)
            _notify(notifier, f"⚠️ {msg}")
            _emit({"type": "merge_fail", "branch": branch,
                   "error": merge_result.error})

    # ── 머지 후 테스트 검증 ──────────────────────────────────────────────────
    if test_runner and merged_count > 0:
        tests_dir = repo_path / "tests"
        if tests_dir.exists():
            _log("→ 머지 후 테스트 실행 중…")
            _notify(notifier, f"🧪 {base_branch} 머지 후 테스트 실행 중…")
            _emit({"type": "merge_testing", "base_branch": base_branch})

            test_result = test_runner.run(repo_path)

            if test_result.passed:
                _log(f"✓ 머지 후 테스트 통과: {test_result.summary}")
                _notify(notifier, f"✅ 머지 후 테스트 통과: {test_result.summary}")
                _emit({"type": "merge_test_pass", "summary": test_result.summary})
            else:
                # 테스트 실패 → 머지 커밋 전부 되돌리기
                logger.error("머지 후 테스트 실패 — 머지 취소: %s", test_result.summary)
                _notify(notifier,
                        f"❌ 머지 후 테스트 실패 — 머지 취소\n"
                        f"원인: {test_result.summary[:300]}")
                _emit({"type": "merge_test_fail", "summary": test_result.summary})

                git(["reset", "--hard", pre_merge_sha])
                git(["push", "origin", base_branch, "--force"])
                print(_fail(f"머지 후 테스트 실패 — {base_branch} 원상복구 완료"))
                git(["checkout", original])
                return
        else:
            logger.debug("tests/ 디렉토리 없음 — 머지 후 테스트 건너뜀")

    # ── PROJECT_STRUCTURE.md 갱신 ────────────────────────────────────────────
    _update_project_structure(repo_path, git)

    # ── base_branch push ─────────────────────────────────────────────────────
    push = git(["push", "origin", base_branch])
    if push.returncode == 0:
        _log(f"✓ {base_branch} push 완료")
        _notify(notifier, f"🚀 {base_branch} push 완료 ({merged_count}개 브랜치 머지)")
        _emit({"type": "merge_pushed", "base_branch": base_branch,
               "merged_count": merged_count})
    else:
        msg = f"{base_branch} push 실패: {push.stderr.strip()}"
        print(_warn(msg))
        _notify(notifier, f"⚠️ {msg}")
        _emit({"type": "merge_push_fail", "error": push.stderr.strip()})

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


def _build_pipeline_metrics_summary(
    all_tasks: list[Task],
    reports_dir: Path,
) -> str:
    """완료된 태스크의 리포트에서 핵심 메트릭을 집계해 Discord 요약 문자열을 반환한다."""
    reports = load_reports(reports_dir=reports_dir)
    if not reports:
        return ""

    # 이번 파이프라인 태스크 ID만 필터
    task_ids = {t.id for t in all_tasks}
    reports = [r for r in reports if r.task_id in task_ids]
    if not reports:
        return ""

    total = len(reports)
    first_try_pass = sum(1 for r in reports if r.test_red_to_green_first_try)
    quality_gate_hits = sum(1 for r in reports if r.quality_gate_rejections > 0)
    dep_injected = sum(1 for r in reports if r.dep_files_injected > 0)
    avg_impl_retries = (
        sum(r.impl_retries for r in reports) / total if total else 0
    )

    # 실패 단계 분포
    failed_reports = [r for r in reports if r.status == "FAILED" and r.failed_stage]
    stage_counts: dict[str, int] = {}
    for r in failed_reports:
        stage_counts[r.failed_stage] = stage_counts.get(r.failed_stage, 0) + 1

    lines = [
        "\n**📊 파이프라인 메트릭**",
        f"- 첫 시도 통과율 (Red→Green): {first_try_pass}/{total} ({first_try_pass*100//total if total else 0}%)",
        f"- 평균 구현 재시도: {avg_impl_retries:.1f}회",
        f"- 품질 게이트 reject: {quality_gate_hits}건",
        f"- 의존성 주입 활용: {dep_injected}건",
    ]
    if stage_counts:
        stage_str = ", ".join(f"{k}: {v}" for k, v in sorted(stage_counts.items()))
        lines.append(f"- 실패 단계 분포: {stage_str}")

    return "\n".join(lines)


def _notify_failure(
    notifier: DiscordNotifier | None,
    task: Task,
    reason: str,
    elapsed: float,
) -> None:
    """태스크 실패를 Discord에 단방향으로 알린다. 힌트 대기 없음."""
    _notify(
        notifier,
        f"❌ [{task.id}] \"{task.title}\" 실패 (⏱ {elapsed:.0f}s)\n"
        f"원인: {reason[:300]}",
    )


def _ask_continue(
    notifier: DiscordNotifier | None,
    failed_ids_in_group: list[str],
    remaining_groups: int,
    pause_ctrl: "PauseController | None" = None,
) -> bool:
    """
    그룹 내 태스크가 전부 실패했을 때 사용자에게 계속 진행 여부를 묻는다.
    답이 올 때까지 무한 대기한다.

    Returns:
        True  → 계속 진행
        False → 파이프라인 중단
    """
    _CONTINUE_KEYWORDS = {"계속", "continue", "yes", "ㅇ", "응", "ㅇㅇ"}
    _STOP_KEYWORDS     = {"중단", "stop", "no", "ㄴ", "아니", "아니오"}

    def _matches(text: str, keywords: set) -> bool:
        """슬래시 접두사 제거 후 키워드 대조 (e.g. /중단 → 중단).
        NFC 정규화 후 정확 일치 또는 키워드 포함 여부 확인."""
        normalized = unicodedata.normalize("NFC", text.strip().lstrip("/").strip().lower())
        return normalized in keywords or any(kw in normalized for kw in keywords)

    ids_text = ", ".join(failed_ids_in_group)
    msg = (
        f"⚠️ **연속 실패 감지**\n\n"
        f"방금 실행한 태스크가 모두 실패했습니다: {ids_text}\n"
        f"남은 그룹: {remaining_groups}개\n\n"
        f"계속 진행하시겠습니까?\n"
        f"`계속` — 다음 그룹 실행  |  `중단` — 파이프라인 종료"
    )

    if notifier is None:
        # stdin 폴백 — Discord 경로와 동일한 _matches() 사용
        print(f"\n{'='*60}\n{msg}\n{'='*60}")
        while True:
            try:
                reply = input(">>> ").strip()
            except (EOFError, KeyboardInterrupt):
                return False
            if _matches(reply, _CONTINUE_KEYWORDS):
                return True
            if _matches(reply, _STOP_KEYWORDS):
                return False

    # listen_for_commands 스레드가 이미 "중단"을 처리했다면 즉시 종료
    if pause_ctrl and pause_ctrl.is_stopped:
        _notify(notifier, "🛑 파이프라인을 종료합니다.")
        return False

    message_id = _notify(notifier, msg)
    if not message_id:
        return True  # Discord 실패 시 기본값: 계속

    _POLL_CHUNK = 10
    _stop_check = (lambda: pause_ctrl.is_stopped) if pause_ctrl else None
    while True:
        # pause_ctrl이 이미 중단 상태면(listen 스레드가 먼저 처리) 즉시 종료
        if pause_ctrl and pause_ctrl.is_stopped:
            _notify(notifier, "🛑 파이프라인을 종료합니다.")
            return False

        reply, message_id = notifier.wait_for_reply(
            after_message_id=message_id,
            timeout=_POLL_CHUNK,
            stop_check=_stop_check,
        )
        if reply is None:
            continue
        if _matches(reply, _CONTINUE_KEYWORDS):
            _notify(notifier, "▶ 파이프라인을 계속 진행합니다.")
            return True
        if _matches(reply, _STOP_KEYWORDS):
            _notify(notifier, "🛑 파이프라인을 종료합니다.")
            return False
        # 키워드 아닌 메시지 — listen 스레드가 이미 처리했으면 즉시 종료
        logger.warning("_ask_continue: 미인식 메시지 %r (codepoints: %s)",
                       reply[:80], [hex(ord(c)) for c in reply[:20]])
        if pause_ctrl and pause_ctrl.is_stopped:
            _notify(notifier, "🛑 파이프라인을 종료합니다.")
            return False
        message_id = _notify(
            notifier,
            "`계속` 또는 `중단`을 입력해주세요.",
        ) or message_id


# ── CLI 진입점 ────────────────────────────────────────────────────────────────

def _apply_rate_limit_overrides(args: argparse.Namespace) -> None:
    """--tpm-limit / --rate-limit-safety-ratio 를 rate_limiter 가 읽는 env 로 주입.

    LLM 클라이언트가 생성되기 전에 호출해야 한다.
    """
    import os as _os
    import re as _re

    if getattr(args, "rate_limit_safety_ratio", None) is not None:
        _os.environ["LLM_RATE_LIMIT_SAFETY_RATIO"] = str(args.rate_limit_safety_ratio)

    for spec in getattr(args, "tpm_limit", []) or []:
        # 형식: provider:model=TPM
        try:
            left, tpm_str = spec.split("=", 1)
            provider, model = left.split(":", 1)
            tpm = int(tpm_str)
        except ValueError:
            print(_fail(f"--tpm-limit 형식 오류: '{spec}' (provider:model=TPM)"))
            continue
        sanitized = _re.sub(r"[^A-Z0-9]+", "_",
                            f"{provider}_{model}".upper()).strip("_")
        _os.environ[f"LLM_TPM_{sanitized}"] = str(tpm)
        print(f"rate_limiter: TPM override {provider}/{model} → {tpm}")


def main() -> int:
    args = _parse_args()

    _apply_rate_limit_overrides(args)

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

    # Docker 이미지 (언어별 자동 빌드 — DockerTestRunner.run() 내부에서 처리)
    runner = DockerTestRunner()

    # LLM 클라이언트
    try:
        fast_llm = create_client(args.provider, LLMConfig(model=args.model_fast, max_tokens=8192))
        capable_llm = create_client(args.provider, LLMConfig(model=args.model_capable, max_tokens=8192))
    except ValueError as e:
        print(_fail(f"LLM 클라이언트 초기화 실패: {e}"))
        return 1

    # 오케스트레이터 개입 + hotline LLM 주입
    analyze_llm, report_llm = create_intervention_llms(args.provider, args.model_capable)
    _set_intervention_llm(analyze_llm, report_llm)
    conv_llm, sum_llm = create_hotline_llms(args.provider, args.model_capable)
    _set_hotline_llm(conv_llm, sum_llm)
    _set_hotline_tasks_path(tasks_path)

    pipeline = TDDPipeline(agent_llm=fast_llm, implementer_llm=fast_llm, test_runner=runner)
    git = GitWorkflow(repo_path, base_branch=args.base_branch)
    merge_agent = MergeAgent(llm=fast_llm, repo_path=repo_path)

    # reports_dir: --reports-dir 명시 시 해당 경로, 기본값이면 repo 기준 자동 해석
    reports_dir = (
        resolve_reports_dir(args.reports_dir, base=repo_path)
        if args.reports_dir == "agent-data/reports"
        else Path(args.reports_dir)
    )

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
                    no_pr=args.no_pr,
                    no_push=None,
                    notifier=None,  # CLI에서는 Discord 미사용
                    save_lock=save_lock,
                    all_tasks=all_tasks,
                    tasks_path=tasks_path,
                    reports_dir=reports_dir,
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

    # ── 마일스톤 보고서 생성 ──────────────────────────────────────────────────
    if success_count > 0:
        try:
            _milestone_llm = create_client(
                args.provider, LLMConfig(model=args.model_capable, max_tokens=4096)
            )

            def _llm_fn(system: str, user: str) -> str:
                from llm import LLMConfig as _LLMConfig, Message as _Message, create_client as _cc
                llm = _cc(args.provider, _LLMConfig(model=args.model_capable, system_prompt=system, max_tokens=4096))
                resp = llm.chat([_Message(role="user", content=user)])
                for block in resp.content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block["text"].strip()
                    if hasattr(block, "type") and block.type == "text":
                        return block.text.strip()
                return ""

            run_label = tasks_path.stem  # 예: "tasks"
            task_reports = load_reports(reports_dir=reports_dir)
            # 이번 실행 태스크만 필터 (all_tasks ID 기준)
            run_ids = {t.id for t in all_tasks}
            run_reports = [r for r in task_reports if r.task_id in run_ids]

            if run_reports:
                milestones_dir = reports_dir / "milestones"
                _, milestone_path = generate_milestone_report(
                    reports=run_reports,
                    llm_fn=_llm_fn,
                    run_label=run_label,
                    milestones_dir=milestones_dir,
                )
                print(_ok(f"마일스톤 보고서: {milestone_path}"))
        except Exception as exc:
            print(_warn(f"마일스톤 보고서 생성 실패 (건너뜀): {exc}"))

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
    parser.add_argument("--tpm-limit", action="append", default=[],
                        metavar="PROVIDER:MODEL=TPM",
                        help="클라이언트 측 TPM 한도 override. 예: "
                             "--tpm-limit openai:gpt-4.1=20000. 여러 번 지정 가능.")
    parser.add_argument("--rate-limit-safety-ratio", type=float, default=None,
                        metavar="RATIO",
                        help="공식 한도의 몇 %%까지 쓸지 (0.1~1.0, 기본 0.85).")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="DEBUG 로그 출력")
    parser.add_argument("--reports-dir", default="agent-data/reports",
                        metavar="DIR",
                        help="Task Report 저장 디렉토리 (기본값: agent-data/reports)")
    parser.add_argument("--provider", default="claude",
                        choices=["claude", "openai", "glm", "ollama"],
                        help="LLM 프로바이더 (기본값: claude)")
    parser.add_argument("--model-fast", default="claude-haiku-4-5",
                        metavar="MODEL",
                        help="빠른 작업용 모델 (기본값: claude-haiku-4-5)")
    parser.add_argument("--model-capable", default="claude-sonnet-4-6",
                        metavar="MODEL",
                        help="복잡한 작업용 모델 (기본값: claude-sonnet-4-6)")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
