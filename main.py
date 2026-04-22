"""
main.py — AI Coding Agent 진입점

실행:
    python main.py                        # claude (기본값)
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
import pathlib
import re
import sys
from typing import Callable

from agents.roles import resolve_model_for_role
from cli import interface as ui
from cli.commands import Action, handle
from cli.config import ROLE_MINI_MEETING, find_repo_root, load_config as load_cli_config
from cli.instant_runner import InstantRunner, RunMode
from cli.interface import CLIMode, get_current_mode, set_mode
from cli.interrupt import EscInterruptHandler
from cli.pipeline_confirm import PipelineConfirmManager
from cli.retry_prompt import RetryPrompt
from cli.settings_wizard import run_settings_wizard
from cli.task_converter import TaskConverter
from core.config import load_config
from core.loop import ReactLoop
from core.undo import ChangeTracker
from llm import LLMConfig, create_client
from memory import SessionManager


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
        help="LLM provider (기본값: config 파일 또는 claude)",
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
    "ollama": "devstral:24b",
    "openai": "gpt-4o",
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
            asyncio.run(runner.run(raw))
        except KeyboardInterrupt:
            ui.print_info("TDD 파이프라인 중단됨.")
        except Exception as e:
            ui.print_error(f"TDD 실행 오류: {e}")
        return True
    return False


def _build_tdd_runner(repo_root: str, cli_cfg) -> InstantRunner:
    meeting_provider, meeting_model = resolve_model_for_role(
        role=ROLE_MINI_MEETING,
        role_models=None,
        default_role_models=cli_cfg.default_role_models,
    )
    converter = TaskConverter(
        repo_path=repo_root,
        llm_config=LLMConfig(model=meeting_model),
        provider=meeting_provider,
    )
    return InstantRunner(
        repo_path=repo_root,
        converter=converter,
        confirm=PipelineConfirmManager(),
        retry=RetryPrompt(),
        default_role_models=cli_cfg.default_role_models,
        complexity_role_models=cli_cfg.complexity_role_models,
        auto_select_by_complexity=cli_cfg.auto_select_by_complexity,
        mode=RunMode.FULL_TDD,
    )


def _make_tdd_runner_loader(repo_root: str, cli_cfg) -> Callable[[], InstantRunner]:
    runner: InstantRunner | None = None

    def _get_runner() -> InstantRunner:
        nonlocal runner
        if runner is None:
            runner = _build_tdd_runner(repo_root, cli_cfg)
        return runner

    return _get_runner


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
    ui.console.print(f"[dim]총 {len(models)}개  ·  기본값: {default or '(없음)'}[/dim]\n")


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
        model = _DEFAULT_MODELS.get(args.provider or agent_cfg.provider, _DEFAULT_MODELS["claude"])

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
    )

    # ── 세션 초기화 ───────────────────────────────────────────────────────────
    mgr = SessionManager()

    # ── TDD 모드 초기화 ───────────────────────────────────────────────────────
    repo_root = find_repo_root()
    cli_cfg = load_cli_config(repo_root)

    ui.configure_tdd_availability(repo_root is not None)

    get_tdd_runner: Callable[[], InstantRunner] | None = None
    if repo_root is None:
        ui.print_info(
            "경고: .git을 찾지 못했습니다. TDD 모드는 git 프로젝트 내에서 실행하세요."
        )
        set_mode(CLIMode.NORMAL)
    else:
        get_tdd_runner = _make_tdd_runner_loader(repo_root, cli_cfg)
        if cli_cfg.default_mode == "tdd":
            set_mode(CLIMode.TDD)
        else:
            set_mode(CLIMode.NORMAL)

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

    while True:
        try:
            raw = ui.get_input(session.session_id[:8])
        except KeyboardInterrupt:
            ui.print_info("\n종료합니다.")
            break

        if not raw.strip():
            continue

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

        # ── TDD 모드 분기 ────────────────────────────────────────────────────
        if _run_turn(raw, mode=get_current_mode(), get_runner=get_tdd_runner):
            continue

        # ── 일반 대화 → ReAct 루프 실행 ──────────────────────────────────────
        history = mgr.get_history(session.session_id)
        expanded = expand_at_mentions(raw)

        esc_handler.reset()
        loop.stop_check = esc_handler.is_interrupted
        try:
            with esc_handler:
                loop_result = loop.run(expanded, history=history)
        except Exception as e:
            ui.print_error(f"루프 실행 중 오류: {e}")
            loop.stop_check = None
            continue
        finally:
            loop.stop_check = None

        # ── ESC 인터럽트 처리 ────────────────────────────────────────────────
        if esc_handler.was_interrupted:
            ui.console.print(
                "\n[bold yellow]Interrupted[/bold yellow] · "
                "What should agent do instead? "
                "[dim](비우고 Enter 시 취소)[/dim]"
            )
            try:
                redirect = ui.get_input(session.session_id[:8])
            except KeyboardInterrupt:
                redirect = ""

            if not redirect.strip():
                ui.print_info("인터럽트 취소됨.")
                continue

            # redirect를 새 입력으로 루프 재실행 (기존 히스토리 유지)
            history = mgr.get_history(session.session_id)
            redirect_expanded = expand_at_mentions(redirect)
            esc_handler.reset()
            loop.stop_check = esc_handler.is_interrupted
            try:
                with esc_handler:
                    loop_result = loop.run(redirect_expanded, history=history)
            except Exception as e:
                ui.print_error(f"루프 실행 중 오류: {e}")
                loop.stop_check = None
                continue
            finally:
                loop.stop_check = None

        # 이번 턴에 새로 추가된 메시지만 저장
        # result.messages = [system, *history, user_input, *new_turns]
        new_messages = loop_result.messages[1 + len(history) :]
        mgr.append_many(session.session_id, new_messages)

        ui.print_answer(loop_result.answer)
        ui.print_token_usage(loop_result)

        if not loop_result.succeeded:
            ui.print_info(f"[중단 이유: {loop_result.stop_reason.value}]")


if __name__ == "__main__":
    main()
