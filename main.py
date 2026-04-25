"""
main.py — AI Coding Agent 진입점

실행:
    python main.py                        # openai / gpt-4.1-mini (기본값)
    python main.py -p ollama -m qwen2.5-coder:14b
    python main.py -p openai  -m gpt-4o
    python main.py -p openai  -m gpt-4.1-mini
    python main.py -p claude  -s <세션-ID-앞자리>  # 세션 이어하기
    python main.py -p glm     -m glm-4.5-air
    python main.py -m glm-4.5-air                  # provider 자동 추론
    python main.py --list                           # 전체 provider 모델 목록
    python main.py -p glm --list                    # glm 모델 목록
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import pathlib
import re
import sys
import threading
from dataclasses import dataclass
from typing import Callable

from agents.roles import resolve_model_for_role
from cli import interface as ui
from cli.commands import Action, handle
from cli.config import (
    ROLE_MINI_MEETING,
    find_repo_root,
    load_config as load_cli_config,
    normalize_default_mode,
)
from cli.instant_runner import InstantRunner, RunMode
from cli.plan_runner import PlanRunner
from cli.interface import CLIMode, get_current_mode, set_mode
from cli.interrupt import EscInterruptHandler
from cli.pipeline_confirm import PipelineConfirmManager
from cli.retry_prompt import RetryPrompt
from cli.settings_wizard import run_settings_wizard
from cli.task_converter import PlanLoop
from core.config import load_config
from core.loop import ReactLoop
from core.undo import ChangeTracker
from llm import LLMConfig, create_client
from llm.base import Message
from memory import SessionManager
from tools.registry import TOOL_REGISTRY, get_tools_schema, make_tool_caller


# ── 인자 파싱 ─────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="로컬 AI 코딩 에이전트",
    )
    parser.add_argument(
        "-p",
        "--provider",
        default=None,
        choices=["ollama", "openai", "claude", "gemini", "glm"],
        help="LLM provider (기본값: config 파일 또는 openai)",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=None,
        help="모델 이름 (미지정 시 provider 기본값 사용)",
    )
    parser.add_argument(
        "-s",
        "--session",
        default=None,
        metavar="ID",
        help="이어할 세션 ID (앞자리만 입력 가능)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG 로그 출력",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="사용 가능한 모델 목록 출력 후 종료 (-p 로 provider 지정 가능)",
    )
    return parser.parse_args()


# ── provider별 기본 모델 ──────────────────────────────────────────────────────


_DEFAULT_MODELS: dict[str, str] = {
    "ollama": "kimi-k2.5:cloud",
    "openai": "gpt-4.1-mini",
    "claude": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-pro-preview-06-05",
    "glm": "glm-5.1",
}

# 모델 이름 prefix → provider 자동 추론
_MODEL_PREFIX_MAP: list[tuple[str, str]] = [
    ("glm-", "glm"),
    ("claude-", "claude"),
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("gemini-", "gemini"),
]


def _infer_provider(model: str) -> str | None:
    for prefix, provider in _MODEL_PREFIX_MAP:
        if model.startswith(prefix):
            return provider
    return None


_CONFIG_PATH = pathlib.Path.home() / ".config" / "ai_coding_agent" / "config.toml"

_OLLAMA_KWARGS: dict = {"native_tool_role": False}
_INSTANT_TOOL_EXCLUDES = {"git_add", "git_commit", "verified_commit"}
_COMMIT_WORKFLOW_TOOL_NAMES = (
    "git_status",
    "git_diff",
    "git_log",
    "analyze_uncommitted_changes",
    "propose_commit_groups",
    "stage_group",
    "commit_group",
)
_COMMIT_WORKFLOW_SYSTEM = """\
당신은 로컬 git 저장소의 미커밋 변경을 안전하게 정리하는 전용 에이전트입니다.
당신의 모델 이름은 {model}입니다. 자신이 누구냐는 질문에 이 이름을 답하세요.

## 핵심 원칙

- 커밋 관련 요청은 반드시 현재 워킹 트리 상태를 먼저 확인한 뒤 진행하세요.
- 첫 단계로 `analyze_uncommitted_changes` 또는 `git_status`를 호출해 전체 변경 범위를 파악하세요.
- 커밋을 나누라는 요청이면 `propose_commit_groups`로 후보를 만든 뒤, 필요하면 `git_diff`로 각 그룹을 검증하세요.
- 스테이징은 반드시 `stage_group`만 사용하세요. `git add .` 같은 광범위 staging을 시도하지 마세요.
- 스테이징 구성을 다시 잡아야 하면 `stage_group`을 `replace=true`와 함께 호출해 기존 staged 상태를 비운 뒤 원하는 경로만 다시 stage하세요.
- 커밋은 반드시 `commit_group`만 사용하세요. 기대 경로를 `expected_paths`로 명시해 검증을 통과한 경우에만 커밋하세요.
- 변경이 하나의 커밋으로 묶기 부적절하면 여러 커밋으로 나누세요.
- 근거 없이 서로 무관한 파일을 한 커밋으로 합치지 마세요.
- 도구 결과를 충분히 확인하기 전에는 커밋하지 마세요.
- 하나의 커밋 그룹에 여러 파일이 속하면 `stage_group`을 파일별로 반복 호출하지 말고 한 번에 묶어서 호출하세요.

## 권장 절차

1. 변경 분석: `analyze_uncommitted_changes`
2. 그룹 제안: `propose_commit_groups`
3. 필요시 diff 검증: `git_diff`
4. 그룹별 stage: `stage_group`
   staging이 꼬였으면 `stage_group(..., replace=true)`
5. 그룹별 commit: `commit_group`

## 최종 답변

- 실제로 수행한 커밋 메시지와 포함 파일을 간결하게 요약하세요.
- 커밋하지 못한 변경이 남아 있으면 명확히 적으세요.
- 언어는 한국어로 답변하세요.
"""
_COMMIT_INTENT_PATTERNS = (
    r"\bcommit\b",
    r"\bcommits\b",
    r"\bstage\b",
    r"\bstaging\b",
    r"\buncommitted\b",
    r"\bunstaged\b",
    r"\bgit\s+add\b",
    r"\bgit\s+commit\b",
    r"\bgit\s+status\b",
    r"\bgit\s+diff\b",
    r"\bworking tree\b",
)
_COMMIT_INTENT_KEYWORDS = (
    "커밋",
    "미커밋",
    "스테이징",
    "스테이지",
    "언스테이징",
    "변경사항 정리",
    "변경 사항 정리",
    "변경사항을 정리",
    "변경 사항을 정리",
    "구현 의도별",
    "커밋 메시지",
    "커밋 경계",
    "나누어 커밋",
    "나눠 커밋",
    "분리 커밋",
)
_WORKFLOW_RESUME_KEYWORDS = (
    "다음",
    "계속",
    "계속 이어서",
    "이어서",
    "이어가",
    "이어 진행",
    "계속 진행",
    "마저",
    "resume",
    "continue",
)


def _tool_schema_provider(provider: str) -> str:
    if provider in {"openai", "glm", "gemini"}:
        return "openai"
    if provider == "claude":
        return "anthropic"
    if provider == "ollama":
        return "ollama"
    raise ValueError(f"지원하지 않는 provider: {provider}")


def _instant_allowed_tools() -> tuple[str, ...]:
    return tuple(
        name for name in TOOL_REGISTRY
        if name not in _INSTANT_TOOL_EXCLUDES
    )


def _commit_workflow_allowed_tools() -> tuple[str, ...]:
    return tuple(
        name for name in TOOL_REGISTRY
        if name in _COMMIT_WORKFLOW_TOOL_NAMES
    )


def _looks_like_commit_workflow_request(raw: str) -> bool:
    text = raw.casefold()
    if any(keyword in raw for keyword in _COMMIT_INTENT_KEYWORDS):
        return True
    return any(re.search(pattern, text) for pattern in _COMMIT_INTENT_PATTERNS)


def _looks_like_workflow_resume_request(raw: str) -> bool:
    text = raw.casefold().strip()
    if not text:
        return False
    return any(keyword in text for keyword in _WORKFLOW_RESUME_KEYWORDS)


def _select_instant_loop_kind(
    raw: str,
    *,
    pending_loop_kind: str | None = None,
) -> str:
    if pending_loop_kind and _looks_like_workflow_resume_request(raw):
        return pending_loop_kind
    if _looks_like_commit_workflow_request(raw):
        return "commit_workflow"
    return "instant"


# ── @멘션 전처리 ──────────────────────────────────────────────────────────────

_MAX_DIR_ENTRIES = 200  # 디렉토리 트리 최대 항목 수
_MAX_FILE_BYTES = 500_000  # 단일 파일 최대 크기 (500 KB)

_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}


def _dir_tree(path: pathlib.Path) -> str:
    """디렉토리 트리를 문자열로 반환합니다."""
    lines: list[str] = []
    for p in sorted(path.rglob("*")):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        indent = "  " * (len(p.relative_to(path).parts) - 1)
        marker = "/" if p.is_dir() else ""
        lines.append(f"{indent}{p.name}{marker}")
        if len(lines) >= _MAX_DIR_ENTRIES:
            lines.append(f"  ... (최대 {_MAX_DIR_ENTRIES}개 표시)")
            break
    return "\n".join(lines)


def expand_at_mentions(text: str) -> str:
    """
    입력 텍스트에서 @경로 토큰을 파일/디렉토리 내용으로 치환합니다.

    - @path/to/file.py  → 파일 내용 주입
    - @path/to/dir/     → 디렉토리 트리 주입
    """

    def replace(m: re.Match) -> str:
        raw = m.group(1)
        path = pathlib.Path(raw).expanduser()

        if path.is_file():
            size = path.stat().st_size
            if size > _MAX_FILE_BYTES:
                return f"\n\n[파일: {path}  ⚠ {size // 1024} KB — 너무 커서 생략됨]\n"
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return f"\n\n[파일 읽기 실패: {path} — {e}]\n"
            lang = path.suffix.lstrip(".") or "text"
            return f"\n\n[파일: {path}]\n```{lang}\n{content}\n```\n"

        if path.is_dir():
            tree = _dir_tree(path)
            return f"\n\n[디렉토리: {path}]\n{tree}\n"

        # 존재하지 않는 경로면 원본 유지 (LLM이 직접 처리하게 둠)
        return m.group(0)

    return re.sub(r"@(\S+)", replace, text)


# ── 모드 분기 (테스트용으로 분리) ────────────────────────────────────────────


def _run_turn(
    raw: str,
    *,
    mode: CLIMode,
    get_runner: Callable[[], InstantRunner] | None,
) -> bool:
    """현재 모드에 따라 입력을 라우팅.

    Returns:
        True  — TDD 모드로 처리됨 (호출자는 continue)
        False — 일반 모드로 처리 필요 (호출자가 ReactLoop로 진행)
    """
    if mode == CLIMode.TDD and get_runner is not None:
        try:
            runner = get_runner()
            _run_async_blocking(runner.run, raw)
        except KeyboardInterrupt:
            ui.print_info("TDD 파이프라인 중단됨.")
        except Exception as e:
            ui.print_error(f"TDD 실행 오류: {e}")
        return True
    return False


def _build_plan_loop(repo_root: str, cli_cfg) -> PlanLoop:
    meeting_provider, meeting_model = resolve_model_for_role(
        role=ROLE_MINI_MEETING,
        role_models=None,
        default_role_models=cli_cfg.default_role_models,
    )
    return PlanLoop(
        repo_path=repo_root,
        llm_config=LLMConfig(model=meeting_model),
        provider=meeting_provider,
    )


def _build_tdd_runner(repo_root: str, cli_cfg) -> InstantRunner:
    planner = _build_plan_loop(repo_root, cli_cfg)
    return InstantRunner(
        repo_path=repo_root,
        planner=planner,
        confirm=PipelineConfirmManager(),
        retry=RetryPrompt(),
        default_role_models=cli_cfg.default_role_models,
        complexity_role_models=cli_cfg.complexity_role_models,
        auto_select_by_complexity=cli_cfg.auto_select_by_complexity,
        mode=RunMode.FULL_TDD,
    )


def _build_plan_runner(repo_root: str, cli_cfg) -> PlanRunner:
    return PlanRunner(
        planner=_build_plan_loop(repo_root, cli_cfg),
        confirm=PipelineConfirmManager(),
    )


def _build_commit_workflow_loop(provider: str, model: str, agent_cfg, approval_handler) -> ReactLoop:
    commit_client = create_client(
        provider=provider,
        config=LLMConfig(model=model, system_prompt=_COMMIT_WORKFLOW_SYSTEM),
        **(_OLLAMA_KWARGS if provider == "ollama" else {}),
    )
    return ReactLoop(
        llm=commit_client,
        max_iterations=agent_cfg.max_iterations,
        on_tool_call=ui.print_tool_call,
        on_tool_result=ui.print_tool_result,
        on_tool_approval=approval_handler,
        tools_schema=get_tools_schema(
            _tool_schema_provider(provider),
            allowed_names=_commit_workflow_allowed_tools(),
        ),
        tool_caller=make_tool_caller(_commit_workflow_allowed_tools()),
    )


def _run_async_blocking(async_fn, *args, **kwargs):
    """동기 컨텍스트에서 async 함수를 실행한다.

    prompt_toolkit 등으로 이미 이벤트 루프가 실행 중인 경우에는 별도 스레드에서
    독립 루프를 띄워 TDD 코루틴을 실행한다.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(async_fn(*args, **kwargs))

    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result["value"] = asyncio.run(async_fn(*args, **kwargs))
        except BaseException as exc:  # KeyboardInterrupt 포함 전달
            error["exc"] = exc

    thread = threading.Thread(target=_worker, daemon=False)
    thread.start()
    thread.join()

    if "exc" in error:
        raise error["exc"]
    return result.get("value")


def _make_tdd_runner_loader(repo_root: str, cli_cfg) -> Callable[[], InstantRunner]:
    runner: InstantRunner | None = None

    def _get_runner() -> InstantRunner:
        nonlocal runner
        if runner is None:
            runner = _build_tdd_runner(repo_root, cli_cfg)
        return runner

    return _get_runner


def _make_plan_runner_loader(repo_root: str, cli_cfg) -> Callable[[], PlanRunner]:
    runner: PlanRunner | None = None

    def _get_runner() -> PlanRunner:
        nonlocal runner
        if runner is None:
            runner = _build_plan_runner(repo_root, cli_cfg)
        return runner

    return _get_runner


@dataclass
class _DispatchResult:
    handled: bool
    execute_input: str | None = None
    loop_kind: str = "instant"


def _dispatch_mode_turn(
    raw: str,
    *,
    mode: CLIMode,
    get_plan_runner: Callable[[], PlanRunner] | None,
    get_tdd_runner: Callable[[], InstantRunner] | None,
    pending_loop_kind: str | None = None,
) -> _DispatchResult:
    if mode == CLIMode.TDD:
        handled = _run_turn(raw, mode=mode, get_runner=get_tdd_runner)
        return _DispatchResult(handled=handled)

    if mode == CLIMode.PLAN and get_plan_runner is not None:
        try:
            runner = get_plan_runner()
            result = _run_async_blocking(runner.run, raw)
        except KeyboardInterrupt:
            ui.print_info("Plan 모드 중단됨.")
            return _DispatchResult(handled=True)
        except Exception as e:
            ui.print_error(f"Plan 실행 오류: {e}")
            return _DispatchResult(handled=True)

        if result.warnings and result.task is None:
            for warning in result.warnings:
                ui.print_info(warning)

        if result.user_aborted or not result.execution_prompt:
            return _DispatchResult(handled=True)
        return _DispatchResult(handled=False, execute_input=result.execution_prompt)

    if mode == CLIMode.INSTANT:
        return _DispatchResult(
            handled=False,
            loop_kind=_select_instant_loop_kind(
                raw,
                pending_loop_kind=pending_loop_kind,
            ),
        )

    return _DispatchResult(handled=False)


def _extract_new_messages(loop_result, history: list[Message]) -> list[Message]:
    """loop.run 결과에서 이번 턴에 새로 추가된 메시지만 추출한다."""
    return loop_result.messages[1 + len(history) :]


def _prompt_interrupt_redirect(session_id_short: str) -> str:
    """Interrupted 안내를 띄우고 사용자의 새 지시를 받는다."""
    ui.console.print(
        "\n[bold yellow]Interrupted[/bold yellow] · "
        "What should agent do instead? "
        "[dim](비우고 Enter 시 취소)[/dim]"
    )
    try:
        return ui.get_input(session_id_short)
    except KeyboardInterrupt:
        return ""


# ── 모델 목록 출력 ───────────────────────────────────────────────────────────


def _print_model_list(provider: str, client) -> None:
    from rich.table import Table
    from rich import box

    ui.console.print(f"\n[bold cyan]{provider}[/bold cyan] 사용 가능한 모델\n")

    try:
        models = client.list_models()
    except Exception as e:
        ui.print_error(f"모델 목록 조회 실패: {e}")
        return

    default = _DEFAULT_MODELS.get(provider, "")
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("model", style="white")
    table.add_column("tag", style="dim")

    for m in sorted(models):
        tag = "[bold green]default[/bold green]" if m == default else ""
        table.add_row(m, tag)

    ui.console.print(table)
    ui.console.print(
        f"[dim]총 {len(models)}개  ·  기본값: {default or '(없음)'}[/dim]\n"
    )


# ── 메인 REPL ────────────────────────────────────────────────────────────────


def main() -> None:
    # ── 터미널 상태 보존 (atexit로 복원 보장) ────────────────────────────────
    # EscInterruptHandler / _StopController가 tty.setcbreak()로 터미널을
    # 수정하는 동안 프로세스가 비정상 종료되면 ECHO/ICANON이 꺼진 채 남는다.
    # atexit에 원본 상태를 등록해두면 Ctrl-C·예외·정상 종료 등 대부분의
    # 경로에서 터미널이 자동 복원된다 (SIGKILL 제외).
    import atexit
    import termios as _termios

    if sys.stdin.isatty():
        try:
            _saved_tty = _termios.tcgetattr(sys.stdin.fileno())

            def _restore_tty() -> None:
                try:
                    _termios.tcsetattr(sys.stdin.fileno(), _termios.TCSANOW, _saved_tty)
                except Exception:
                    pass

            atexit.register(_restore_tty)
        except Exception:
            pass

    if len(sys.argv) > 1 and sys.argv[1] == "set":
        run_settings_wizard(find_repo_root())
        return

    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # ── 설정 파일 로드 (CLI 인자가 우선) ────────────────────────────────────
    agent_cfg = load_config(str(_CONFIG_PATH))

    # 모델 결정: CLI -m > (provider 변경 없으면) config > provider 기본값
    if args.model:
        model = args.model
    elif args.provider is None or args.provider == agent_cfg.provider:
        model = agent_cfg.model
    else:
        model = _DEFAULT_MODELS.get(
            args.provider or agent_cfg.provider, _DEFAULT_MODELS["claude"]
        )

    # provider 결정: CLI -p > 모델명 prefix 자동 추론 > config > 기본값
    provider = args.provider or _infer_provider(model) or agent_cfg.provider

    # ── --list: 모델 목록 출력 후 종료 ──────────────────────────────────────
    if args.list:
        if args.provider is None:
            for p, m in _DEFAULT_MODELS.items():
                try:
                    extra = _OLLAMA_KWARGS if p == "ollama" else {}
                    c = create_client(provider=p, config=LLMConfig(model=m), **extra)
                    _print_model_list(p, c)
                except Exception as e:
                    ui.console.print(f"[dim]{p}: 목록 조회 실패 — {e}[/dim]\n")
        else:
            config = LLMConfig(model=model)
            extra = _OLLAMA_KWARGS if provider == "ollama" else {}
            try:
                client = create_client(provider=provider, config=config, **extra)
            except Exception as e:
                ui.print_error(f"LLM 클라이언트 초기화 실패: {e}")
                sys.exit(1)
            _print_model_list(provider, client)
        sys.exit(0)

    # ── LLM 클라이언트 초기화 ────────────────────────────────────────────────
    config = LLMConfig(model=model)
    extra = _OLLAMA_KWARGS if provider == "ollama" else {}

    try:
        client = create_client(provider=provider, config=config, **extra)
    except Exception as e:
        ui.print_error(f"LLM 클라이언트 초기화 실패: {e}")
        sys.exit(1)

    if not client.is_available():
        ui.print_error(f"모델 '{model}'에 연결할 수 없습니다. 설정을 확인하세요.")
        sys.exit(1)

    # ── ChangeTracker (undo 지원) ────────────────────────────────────────────
    tracker = ChangeTracker()

    # ── ReAct 루프 설정 ───────────────────────────────────────────────────────
    approval_handler = (
        None if agent_cfg.auto_approve else ui.ApprovalHandler(tracker=tracker)
    )
    loop = ReactLoop(
        llm=client,
        max_iterations=agent_cfg.max_iterations,
        on_tool_call=ui.print_tool_call,
        on_tool_result=ui.print_tool_result,
        on_tool_approval=approval_handler,
        tools_schema=get_tools_schema(
            _tool_schema_provider(provider),
            allowed_names=_instant_allowed_tools(),
        ),
        tool_caller=make_tool_caller(_instant_allowed_tools()),
    )
    commit_loop: ReactLoop | None = None

    def _get_commit_loop() -> ReactLoop:
        nonlocal commit_loop
        if commit_loop is None:
            commit_loop = _build_commit_workflow_loop(
                provider,
                model,
                agent_cfg,
                approval_handler,
            )
        return commit_loop

    # ── 세션 초기화 ───────────────────────────────────────────────────────────
    mgr = SessionManager()

    # ── 모드 초기화 ──────────────────────────────────────────────────────────
    repo_root = find_repo_root()
    cli_cfg = load_cli_config(repo_root)
    planning_root = repo_root or os.getcwd()

    ui.configure_tdd_availability(repo_root is not None)

    get_plan_runner: Callable[[], PlanRunner] | None = _make_plan_runner_loader(
        planning_root, cli_cfg
    )
    get_tdd_runner: Callable[[], InstantRunner] | None = None
    if repo_root is None:
        ui.print_info(
            "경고: .git을 찾지 못했습니다. TDD 모드는 git 프로젝트 내에서 실행하세요."
        )
    else:
        get_tdd_runner = _make_tdd_runner_loader(repo_root, cli_cfg)

    default_mode = normalize_default_mode(cli_cfg.default_mode)
    if default_mode == "tdd" and get_tdd_runner is None:
        set_mode(CLIMode.INSTANT)
    elif default_mode == "tdd":
        set_mode(CLIMode.TDD)
    elif default_mode == "plan":
        set_mode(CLIMode.PLAN)
    else:
        set_mode(CLIMode.INSTANT)

    if args.session:
        summaries = mgr.list_all()
        matches = [s for s in summaries if s.session_id.startswith(args.session)]
        if not matches:
            ui.print_error(f"세션 '{args.session}'을 찾을 수 없습니다.")
            sys.exit(1)
        session = mgr.load(matches[0].session_id)
        assert session is not None
        ui.print_info(
            f"세션 불러옴: [{session.session_id[:8]}] "
            f"{session.title or '(제목 없음)'} ({len(session.messages)}개 메시지)"
        )
    else:
        session = mgr.new(model=model)

    # ── 배너 & REPL ───────────────────────────────────────────────────────────
    ui.print_banner()
    ui.print_info(
        f"provider={provider}  model={model}  session=[{session.session_id[:8]}]"
    )

    esc_handler = EscInterruptHandler()
    pending_loop_kind: str | None = None

    while True:
        try:
            raw = ui.get_input(session.session_id[:8])
        except KeyboardInterrupt:
            ui.print_info("\n종료합니다.")
            break

        if not raw.strip():
            continue

        ui.print_user_input(raw, session.session_id[:8])

        # ── 슬래시 명령어 처리 ────────────────────────────────────────────────
        result = handle(raw, mgr, session, tracker=tracker)
        if result is not None:
            if result.action == Action.EXIT:
                ui.print_info("종료합니다.")
                break
            if result.action in (Action.NEW_SESSION, Action.LOAD_SESSION):
                assert result.session is not None
                session = result.session
            continue

        # ── 모드 분기 ────────────────────────────────────────────────────────
        dispatch = _dispatch_mode_turn(
            raw,
            mode=get_current_mode(),
            get_plan_runner=get_plan_runner,
            get_tdd_runner=get_tdd_runner,
            pending_loop_kind=pending_loop_kind,
        )
        if dispatch.handled:
            continue

        # ── instant/plan executor → ReAct 루프 실행 ────────────────────────
        history = mgr.get_history(session.session_id)
        expanded = (
            dispatch.execute_input
            if dispatch.execute_input is not None
            else expand_at_mentions(raw)
        )
        active_loop = (
            _get_commit_loop()
            if dispatch.loop_kind == "commit_workflow"
            else loop
        )

        esc_handler.reset()
        active_loop.stop_check = esc_handler.is_interrupted
        try:
            with esc_handler:
                loop_result = active_loop.run(expanded, history=history)
        except Exception as e:
            ui.print_error(f"루프 실행 중 오류: {e}")
            active_loop.stop_check = None
            continue
        finally:
            active_loop.stop_check = None

        # ── ESC 인터럽트 처리 ────────────────────────────────────────────────
        if esc_handler.was_interrupted:
            pending_loop_kind = dispatch.loop_kind
            redirect = _prompt_interrupt_redirect(session.session_id[:8])

            if not redirect.strip():
                ui.print_info("인터럽트 취소됨.")
                continue

            # redirect를 새 입력으로 루프 재실행 (기존 히스토리 유지)
            history = mgr.get_history(session.session_id)
            redirect_expanded = expand_at_mentions(redirect)
            redirect_loop_kind = _select_instant_loop_kind(
                redirect,
                pending_loop_kind=pending_loop_kind,
            )
            redirect_loop = (
                _get_commit_loop()
                if redirect_loop_kind == "commit_workflow"
                else loop
            )
            esc_handler.reset()
            redirect_loop.stop_check = esc_handler.is_interrupted
            try:
                with esc_handler:
                    loop_result = redirect_loop.run(redirect_expanded, history=history)
            except Exception as e:
                ui.print_error(f"루프 실행 중 오류: {e}")
                redirect_loop.stop_check = None
                continue
            finally:
                redirect_loop.stop_check = None

        # 이번 턴에 새로 추가된 메시지만 저장
        # result.messages = [system, *history, user_input, *new_turns]
        new_messages = _extract_new_messages(loop_result, history)
        mgr.append_many(session.session_id, new_messages)
        pending_loop_kind = None

        ui.print_answer(loop_result.answer)
        ui.print_token_usage(loop_result)

        if not loop_result.succeeded:
            ui.print_info(f"[중단 이유: {loop_result.stop_reason.value}]")


if __name__ == "__main__":
    main()
