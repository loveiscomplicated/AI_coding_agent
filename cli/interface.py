"""
cli/interface.py — 입출력, 색상, 포맷팅 (rich + prompt_toolkit)

공개 함수:
  print_banner       : 시작 배너 출력
  print_answer       : LLM 최종 답변을 Markdown으로 렌더링
  print_task_summary : TaskConverter가 생성한 Task 객체를 사람이 읽기 쉬운 형태로 출력
  print_tool_call    : 도구 호출 시작 표시
  print_tool_result  : 도구 실행 결과 표시
  print_sessions     : 세션 목록 테이블 출력
  print_history      : 현재 세션의 대화 히스토리 출력
  print_error        : 에러 메시지 (빨간색)
  print_info         : 안내 메시지 (회색)
  get_input          : 사용자 입력 프롬프트 (탭 자동완성 지원)
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.filters import has_completions
from prompt_toolkit.key_binding import KeyBindings
from cli.selector import SelectOption, inline_select

if TYPE_CHECKING:
    from core.loop import ToolCall, ToolResult
    from core.undo import ChangeTracker
    from memory.session import SessionSummary
    from llm.base import Message
    from orchestrator.pipeline import PipelineResult
    from orchestrator.task import Task

console = Console()


# ── 모드 관리 ─────────────────────────────────────────────────────────────────


class CLIMode(Enum):
    INSTANT = "Instant"
    NORMAL = "Instant"
    PLAN = "Plan"
    READ_ONLY = "ReadOnly"
    TDD = "TDD"


class ModeChangeStatus(Enum):
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ModeChangeResult:
    status: ModeChangeStatus
    mode: CLIMode


_current_mode: CLIMode = CLIMode.INSTANT
_tdd_available: bool = True
_tdd_unavailable_message: str = "git 프로젝트 내에서 실행하세요."


def get_current_mode() -> CLIMode:
    return _current_mode


def set_mode(mode: CLIMode) -> None:
    global _current_mode
    _current_mode = mode


def configure_tdd_availability(
    available: bool,
    message: str = "git 프로젝트 내에서 실행하세요.",
) -> None:
    global _tdd_available, _tdd_unavailable_message, _current_mode
    _tdd_available = available
    _tdd_unavailable_message = message
    if not available and _current_mode == CLIMode.TDD:
        _current_mode = CLIMode.INSTANT


def get_tdd_unavailable_message() -> str:
    return _tdd_unavailable_message


def request_mode_change(target: CLIMode) -> ModeChangeResult:
    global _current_mode

    if target == CLIMode.TDD and not _tdd_available:
        _current_mode = CLIMode.INSTANT
        return ModeChangeResult(status=ModeChangeStatus.BLOCKED, mode=_current_mode)

    if _current_mode == target:
        return ModeChangeResult(status=ModeChangeStatus.UNCHANGED, mode=_current_mode)

    _current_mode = target
    return ModeChangeResult(status=ModeChangeStatus.CHANGED, mode=_current_mode)


def toggle_mode() -> ModeChangeResult:
    cycle = [CLIMode.INSTANT, CLIMode.READ_ONLY, CLIMode.PLAN]
    if _tdd_available:
        cycle.append(CLIMode.TDD)

    try:
        idx = cycle.index(_current_mode)
    except ValueError:
        idx = 0
    target = cycle[(idx + 1) % len(cycle)]
    return request_mode_change(target)


# ── 색상 팔레트 ───────────────────────────────────────────────────────────────
_C_TOOL   = "cyan"
_C_OK     = "green"
_C_ERR    = "red"
_C_INFO   = "dim white"
_C_PROMPT = "bold blue"

# ── 슬래시 명령어 목록 (자동완성용) ──────────────────────────────────────────
_COMMANDS = [
    "/help",
    "/history",
    "/sessions",
    "/new",
    "/load",
    "/rename",
    "/delete",
    "/undo",
    "/mode",
    "/instant",
    "/readonly",
    "/plan",
    "/tdd",
    "/normal",
    "/exit",
    "/quit",
]


# ── 탭 자동완성 ───────────────────────────────────────────────────────────────

class AgentCompleter(Completer):
    """
    두 가지 자동완성을 제공합니다.

    - `@` 뒤: 파일/디렉토리 경로 완성
    - `/` 로 시작: 슬래시 명령어 완성
    """

    _path_completer = PathCompleter(expanduser=True)
    _SEARCH_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}
    _MAX_MENTION_RESULTS = 40
    _CACHE_TTL_SEC = 2.0

    def __init__(self) -> None:
        self._mention_cache_root: Path | None = None
        self._mention_cache_built_at: float = 0.0
        self._mention_cache_entries: list[tuple[str, str, bool]] = []

    def _should_use_path_completion(self, query: str) -> bool:
        return (
            not query
            or "/" in query
            or query.startswith(".")
            or query.startswith("~")
            or query.startswith("/")
        )

    def _mention_entries(self) -> list[tuple[str, str, bool]]:
        root = Path.cwd()
        now = time.monotonic()
        if (
            self._mention_cache_root == root
            and now - self._mention_cache_built_at < self._CACHE_TTL_SEC
        ):
            return self._mention_cache_entries

        entries: list[tuple[str, str, bool]] = []
        for current_root, dirs, files in os.walk(root):
            dirs[:] = [
                d for d in dirs
                if d not in self._SEARCH_SKIP_DIRS
            ]
            current_path = Path(current_root)

            for dirname in dirs:
                path = current_path / dirname
                rel = path.relative_to(root).as_posix()
                entries.append((dirname.lower(), f"{rel}/", True))

            for filename in files:
                path = current_path / filename
                rel = path.relative_to(root).as_posix()
                entries.append((filename.lower(), rel, False))

        self._mention_cache_root = root
        self._mention_cache_built_at = now
        self._mention_cache_entries = entries
        return entries

    def _basename_completions(self, query: str) -> list[Completion]:
        q = query.lower()
        ranked: list[tuple[tuple[int, int, int, str], Completion]] = []
        seen: set[str] = set()

        for basename, rel_path, is_dir in self._mention_entries():
            if q not in basename:
                continue
            if rel_path in seen:
                continue
            seen.add(rel_path)
            rank = (
                0 if basename == q else 1 if basename.startswith(q) else 2,
                0 if is_dir else 1,
                len(rel_path),
                rel_path,
            )
            ranked.append((
                rank,
                Completion(
                    text=rel_path,
                    start_position=-len(query),
                    display=rel_path,
                ),
            ))
            if len(ranked) >= self._MAX_MENTION_RESULTS * 3:
                break

        ranked.sort(key=lambda item: item[0])
        return [completion for _, completion in ranked[:self._MAX_MENTION_RESULTS]]

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor

        # @ 경로 자동완성
        at_match = re.search(r"@(\S*)$", text)
        if at_match:
            path_prefix = at_match.group(1)
            if self._should_use_path_completion(path_prefix):
                path_doc = Document(path_prefix, len(path_prefix))
                yield from self._path_completer.get_completions(path_doc, complete_event)
            else:
                yield from self._basename_completions(path_prefix)
            return

        # / 슬래시 명령어 자동완성 (입력이 /로만 이루어진 경우)
        if re.match(r"^/\S*$", text):
            for cmd in _COMMANDS:
                if cmd.startswith(text):
                    yield Completion(
                        cmd[len(text):],
                        start_position=0,
                        display=cmd,
                    )


# ── 키 바인딩 (Shift+Tab 모드 전환) ──────────────────────────────────────────

_kb = KeyBindings()


@_kb.add("s-tab", filter=~has_completions)
def _toggle_mode_handler(event) -> None:
    result = toggle_mode()
    if result.status == ModeChangeStatus.CHANGED:
        print_mode_changed(result.mode)
    elif result.status == ModeChangeStatus.UNCHANGED:
        print_info(f"이미 {result.mode.value} 모드입니다.")
    else:
        print_info(get_tdd_unavailable_message())
    event.app.invalidate()


# PromptSession은 모듈 수준에서 한 번만 생성 (히스토리 유지)
_prompt_session: PromptSession = PromptSession(
    history=InMemoryHistory(),
    completer=AgentCompleter(),
    complete_while_typing=True,   # @ / / 입력 시 즉시 자동완성 목록 표시
    key_bindings=_kb,
)


# ── 배너 ──────────────────────────────────────────────────────────────────────


def print_banner() -> None:
    mode = get_current_mode()
    mode_label = f"[bold magenta][{mode.value} 모드][/bold magenta]"
    console.print(Panel(
        f"[bold cyan]AI Coding Agent[/bold cyan]  {mode_label}\n"
        "[dim]/help · Tab 자동완성 · @경로 파일 첨부 · Shift+Tab 모드 전환[/dim]",
        box=box.ROUNDED,
        expand=False,
    ))


def print_mode_changed(mode: CLIMode) -> None:
    """모드 전환 시 알림을 한 줄로 출력."""
    console.print(f"  [dim magenta]✓ {mode.value} 모드로 전환[/dim magenta]")


def _mode_prefix(mode: CLIMode) -> str:
    if mode == CLIMode.INSTANT:
        return ""
    return f"[{mode.value}] "


# ── 답변 ──────────────────────────────────────────────────────────────────────


def print_answer(text: str) -> None:
    """LLM 최종 답변을 Markdown으로 렌더링합니다."""
    console.print()
    console.print(Markdown(text))
    console.print()


def print_task_summary(task: "Task", warnings: list[str] | None = None) -> None:
    """태스크를 사람이 읽기 쉬운 형태로 출력합니다.

    TaskConverter가 생성한 Task 객체를 사용자에게 보여줄 때 사용합니다.
    (S2에서 더 풍부한 레이아웃으로 확장 예정)
    """
    lines: list[str] = []
    lines.append(f"[bold]ID:[/bold]       {task.id}")
    lines.append(f"[bold]제목:[/bold]     {task.title}")
    if task.description:
        desc_lines = task.description.strip().splitlines()
        lines.append(f"[bold]설명:[/bold]     {desc_lines[0]}")
        for dl in desc_lines[1:]:
            lines.append(f"          {dl}")
    lines.append(f"[bold]언어:[/bold]     {task.language} ({task.test_framework})")
    lines.append(f"[bold]타입:[/bold]     {task.task_type}")
    lines.append(f"[bold]복잡도:[/bold]   {task.complexity or '(미지정)'}")

    if task.target_files:
        lines.append(f"[bold]대상 파일:[/bold] {task.target_files[0]}")
        for path in task.target_files[1:]:
            lines.append(f"          {path}")
    else:
        lines.append("[bold]대상 파일:[/bold] (없음)")

    if task.acceptance_criteria:
        lines.append("[bold]수락 기준:[/bold]")
        for i, c in enumerate(task.acceptance_criteria, 1):
            lines.append(f"  {i}. {c}")

    if getattr(task, "depends_on", None):
        lines.append(f"[bold]선행 태스크:[/bold] {', '.join(task.depends_on)}")

    if warnings:
        lines.append("")
        lines.append("[yellow]⚠ 경고:[/yellow]")
        for w in warnings:
            lines.append(f"  • {w}")

    console.print()
    console.print(Panel(
        "\n".join(lines),
        title="📋 태스크 요약",
        border_style=_C_TOOL,
        box=box.ROUNDED,
        expand=False,
    ))
    console.print()


# ── 도구 상태 ─────────────────────────────────────────────────────────────────


def print_tool_call(tc: ToolCall) -> None:
    """도구 호출 시작을 한 줄로 표시합니다."""
    args = "  ".join(f"[dim]{k}[/dim]={v!r}" for k, v in tc.input.items())
    console.print(f"  [{_C_TOOL}]⚙ {tc.name}[/{_C_TOOL}]  {args}")


def print_tool_result(tr: ToolResult) -> None:
    """도구 실행 결과(성공/실패)를 한 줄로 표시합니다."""
    if tr.is_error:
        preview = tr.content[:80].replace("\n", " ")
        console.print(f"  [{_C_ERR}]✗ error:[/{_C_ERR}] [dim]{preview}[/dim]")
    else:
        size = len(tr.content)
        console.print(f"  [{_C_OK}]✓[/{_C_OK}] [dim]({size} chars)[/dim]")


# ── 세션 목록 ─────────────────────────────────────────────────────────────────


def print_sessions(summaries: list[SessionSummary]) -> None:
    if not summaries:
        print_info("저장된 세션이 없습니다.")
        return

    table = Table(box=box.SIMPLE, header_style="bold", show_edge=False)
    table.add_column("#",          style="dim",        width=3,  justify="right")
    table.add_column("ID",         style="cyan",       width=10)
    table.add_column("제목",       style="white",      width=24)
    table.add_column("모델",       style="dim",        width=20)
    table.add_column("메시지",     style="dim",        width=6,  justify="right")
    table.add_column("마지막 수정", style="dim",       width=20)

    for i, s in enumerate(summaries, 1):
        table.add_row(
            str(i),
            s.session_id[:8] + "…",
            s.title or "[dim](제목 없음)[/dim]",
            s.model or "-",
            str(s.message_count),
            s.updated_at[:16].replace("T", " "),
        )

    console.print(table)


# ── 히스토리 ──────────────────────────────────────────────────────────────────


def print_history(messages: list[Message]) -> None:
    if not messages:
        print_info("대화 기록이 없습니다.")
        return

    for msg in messages:
        if msg.role == "user":
            if isinstance(msg.content, str):
                console.print(f"[bold blue]You ▶[/bold blue] {msg.content}")
            else:
                console.print(f"[bold blue]You ▶[/bold blue] [dim](tool_result)[/dim]")
        elif msg.role == "assistant":
            if isinstance(msg.content, str):
                preview = msg.content[:120].replace("\n", " ")
            else:
                texts = [
                    b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
                    for b in msg.content
                    if (isinstance(b, dict) and b.get("type") == "text")
                    or getattr(b, "type", None) == "text"
                ]
                preview = " ".join(texts)[:120].replace("\n", " ")
            console.print(f"[bold green]Agent ▶[/bold green] {preview}")
        console.print()


# ── 유틸 ──────────────────────────────────────────────────────────────────────


def print_error(msg: str) -> None:
    console.print(f"[{_C_ERR}]✗ {msg}[/{_C_ERR}]")


def print_info(msg: str) -> None:
    console.print(f"[{_C_INFO}]{msg}[/{_C_INFO}]")


def print_token_usage(result) -> None:
    """토큰 사용량을 출력합니다."""
    total = result.total_input_tokens + result.total_output_tokens
    console.print(
        f"[{_C_INFO}]tokens: input={result.total_input_tokens}  "
        f"output={result.total_output_tokens}  total={total}[/{_C_INFO}]"
    )


def _pipeline_token_totals(result: "PipelineResult") -> tuple[int, int, int, int]:
    total_in = total_out = total_cached_read = total_cached_write = 0
    for usage in result.metrics.token_usage.values():
        if not isinstance(usage, tuple):
            continue
        total_in += int(usage[0] or 0) if len(usage) > 0 else 0
        total_out += int(usage[1] or 0) if len(usage) > 1 else 0
        total_cached_read += int(usage[2] or 0) if len(usage) > 2 else 0
        total_cached_write += int(usage[3] or 0) if len(usage) > 3 else 0
    return total_in, total_out, total_cached_read, total_cached_write


def _format_pipeline_cost(result: "PipelineResult") -> str | None:
    if not result.metrics.token_usage or not result.models_used:
        return None

    from orchestrator.report import _calculate_cost

    cost_usd = _calculate_cost(result.metrics.token_usage, result.models_used)
    if cost_usd is None:
        return None
    if cost_usd >= 0.001:
        return f"${cost_usd:.3f}"
    return f"${cost_usd:.6f}"


def _pipeline_retry_summary(result: "PipelineResult") -> str | None:
    metrics = result.metrics
    automatic_attempts = len(metrics.call_logs.get("implementer", []))
    orchestrator_attempts = len(metrics.call_logs.get("intervention", []))

    if automatic_attempts == 0 and metrics.failed_stage in {"implementing", "testing", "reviewing"}:
        automatic_attempts = max(metrics.impl_retries + 1, 1)

    total_attempts = automatic_attempts + orchestrator_attempts
    if total_attempts == 0:
        return None
    if orchestrator_attempts:
        return (
            f"{total_attempts}회 "
            f"(자동 {automatic_attempts} + 오케스트레이터 {orchestrator_attempts})"
        )
    return f"{automatic_attempts}회"


def print_pipeline_result(result: "PipelineResult") -> None:
    """TDD 파이프라인 완료 후 결과 카드를 출력한다."""
    title = result.task.title
    metrics = result.metrics
    total_in, total_out, total_cached_read, _total_cached_write = _pipeline_token_totals(result)
    total_tokens = total_in + total_out + total_cached_read
    cost_label = _format_pipeline_cost(result)

    if result.succeeded:
        lines: list[str] = [f"[bold green]✅ {title}[/bold green]", ""]
        if result.test_result is not None:
            summary = (result.test_result.summary or "통과").strip()
            lines.append(f"[bold]Tests:[/bold]    {summary}")
        if result.review is not None:
            lines.append(f"[bold]Reviewer:[/bold] {result.review.verdict}")
        files = list(result.impl_files) + list(result.test_files)
        if files:
            lines.append(f"[bold]Files:[/bold]    {files[0]}")
            for f in files[1:]:
                lines.append(f"           {f}")
        token_line = f"[bold]Tokens:[/bold]   {total_tokens:,}"
        if cost_label:
            token_line += f" (cost: {cost_label})"
        lines.append(token_line)
        title_str = "TDD 완료"
        border = _C_OK
    else:
        lines = [f"[bold red]❌ {title}[/bold red]", ""]
        reason = (result.failure_reason or "알 수 없음")[:200]
        lines.append(f"[bold]실패 원인:[/bold] {reason}")
        retry_summary = _pipeline_retry_summary(result)
        if retry_summary:
            lines.append(f"[bold]재시도:[/bold]   {retry_summary}")
        token_line = f"[bold]Tokens:[/bold]   {total_tokens:,}"
        if cost_label:
            token_line += f" (cost: {cost_label})"
        lines.append(token_line)
        title_str = "TDD 실패"
        border = _C_ERR

    console.print()
    console.print(Panel(
        "\n".join(lines),
        title=title_str,
        border_style=border,
        box=box.ROUNDED,
        expand=False,
    ))
    console.print()


# ── Diff & 승인 ───────────────────────────────────────────────────────────────


def _print_diff(before: str, after: str, path: str) -> None:
    """unified diff를 색상으로 출력합니다."""
    diff_lines = list(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    ))
    if not diff_lines:
        console.print("[dim](변경 없음)[/dim]")
        return
    for line in diff_lines:
        if line.startswith("+++") or line.startswith("---"):
            console.print(f"[bold]{line}[/bold]")
        elif line.startswith("+"):
            console.print(f"[green]{line}[/green]")
        elif line.startswith("-"):
            console.print(f"[red]{line}[/red]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/cyan]")
        else:
            console.print(f"[dim]{line}[/dim]")


def _show_tool_preview(tc: ToolCall) -> None:
    """도구 실행 전 변경 내용을 미리 보여줍니다."""
    path = tc.input.get("path", "")

    if tc.name == "edit_file":
        old_str: str = tc.input.get("old_str", "")
        new_str: str = tc.input.get("new_str", "")
        try:
            original = Path(path).read_text(encoding="utf-8")
            before = original
            after  = original.replace(old_str, new_str, 1)
        except Exception:
            before, after = old_str, new_str
        console.print(f"\n[bold yellow]⚠  파일 수정 요청:[/bold yellow] [cyan]{path}[/cyan]")
        _print_diff(before, after, path)

    elif tc.name == "write_file":
        new_content: str = tc.input.get("content", "")
        p = Path(path)
        if p.exists():
            try:
                before = p.read_text(encoding="utf-8")
            except Exception:
                before = ""
            console.print(f"\n[bold yellow]⚠  파일 덮어쓰기 요청:[/bold yellow] [cyan]{path}[/cyan]")
            _print_diff(before, new_content, path)
        else:
            console.print(f"\n[bold yellow]⚠  새 파일 생성 요청:[/bold yellow] [cyan]{path}[/cyan]")
            for line in new_content.splitlines():
                console.print(f"[green]+{line}[/green]")

    elif tc.name == "append_to_file":
        content: str = tc.input.get("content", "")
        console.print(f"\n[bold yellow]⚠  파일 추가 요청:[/bold yellow] [cyan]{path}[/cyan]")
        for line in content.splitlines():
            console.print(f"[green]+{line}[/green]")

    elif tc.name == "execute_command":
        cmd = tc.input.get("command", tc.input.get("cmd", str(tc.input)))
        console.print(f"\n[bold yellow]⚠  셸 명령 실행 요청:[/bold yellow]")
        console.print(f"  [bold red]$ {cmd}[/bold red]")

    else:
        console.print(f"\n[bold yellow]⚠  도구 실행 요청:[/bold yellow] [cyan]{tc.name}[/cyan]")
        for k, v in tc.input.items():
            console.print(f"  [dim]{k}[/dim] = {v!r}")


_FILE_MODIFYING_TOOLS = {"write_file", "edit_file", "append_to_file"}
_COMMAND_ALWAYS_OPTIONS = (
    SelectOption(label="이번만 승인", value="proceed"),
    SelectOption(label="이 세션에서 자동 승인", value="always"),
    SelectOption(label="거부", value="cancel"),
)


class ApprovalHandler:
    """
    도구 실행 승인을 관리합니다.

    세션 동안 자동 승인 상태를 기억하며, 같은 요청이 다시 오면 묻지 않고
    실행한다. 파일 수정 도구는 도구 단위, execute_command는 command prefix
    단위로 자동 승인 범위를 구분한다.
    """

    def __init__(self, tracker: ChangeTracker | None = None) -> None:
        self._always_tools: set[str] = set()
        self._always_command_prefixes: set[tuple[str, ...]] = set()
        self._tracker = tracker

    def _command_tokens(self, tc: ToolCall) -> tuple[str, ...]:
        import shlex

        raw = tc.input.get("command", tc.input.get("cmd", ()))
        if isinstance(raw, (list, tuple)):
            return tuple(str(part) for part in raw if str(part))
        if isinstance(raw, str):
            try:
                parts = tuple(shlex.split(raw))
            except ValueError:
                parts = (raw,)
            return tuple(part for part in parts if part)
        return ()

    def _command_prefix(self, tc: ToolCall) -> tuple[str, ...]:
        tokens = self._command_tokens(tc)
        if len(tokens) >= 3 and tokens[:2] == ("uv", "run"):
            return tokens[:3]
        if tokens:
            return tokens[:1]
        return ("<empty-command>",)

    def _scope_label(self, tc: ToolCall) -> str:
        if tc.name == "execute_command":
            return " ".join(self._command_prefix(tc))
        return tc.name

    def _scope_detail(self, tc: ToolCall) -> str:
        label = self._scope_label(tc)
        if tc.name == "execute_command":
            return (
                f"`이 세션에서 자동 승인`은 `{label}` prefix에만 적용됩니다."
            )
        return f"`이 세션에서 자동 승인`은 `{label}` 도구에만 적용됩니다."

    def _is_auto_approved(self, tc: ToolCall) -> bool:
        if tc.name == "execute_command":
            return self._command_prefix(tc) in self._always_command_prefixes
        return tc.name in self._always_tools

    def _remember_auto_approval(self, tc: ToolCall) -> None:
        if tc.name == "execute_command":
            self._always_command_prefixes.add(self._command_prefix(tc))
            return
        self._always_tools.add(tc.name)

    def _record(self, tc: ToolCall) -> None:
        """파일 수정 도구 실행 전 원본을 tracker에 기록합니다."""
        if not self._tracker:
            return
        if tc.name in _FILE_MODIFYING_TOOLS:
            path = tc.input.get("path", "")
            if path:
                self._tracker.record(path)
            return
        if tc.name == "execute_command":
            self._tracker.record_tree(os.getcwd())

    def __call__(self, tc: ToolCall) -> bool:
        # 항상 승인으로 등록된 도구는 묻지 않고 바로 실행
        if self._is_auto_approved(tc):
            scope = self._scope_label(tc)
            console.print(
                f"  [{_C_INFO}]✓ 자동 승인 ({scope})[/{_C_INFO}]"
            )
            self._record(tc)
            return True

        _show_tool_preview(tc)
        selected = inline_select(
            list(_COMMAND_ALWAYS_OPTIONS),
            message="[bold yellow]승인할까요?[/bold yellow]",
            detail=self._scope_detail(tc),
        )
        if selected in {None, "cancel"}:
            return False

        if selected == "proceed":
            self._record(tc)
            return True

        if selected == "always":
            self._remember_auto_approval(tc)
            scope = self._scope_label(tc)
            console.print(
                f"  [{_C_INFO}]{scope} 은(는) 이후 자동 승인됩니다.[/{_C_INFO}]"
            )
            self._record(tc)
            return True

        return False


def prompt_with_stdin_pause(message, **kwargs):
    """prompt_toolkit 입력 중에는 백그라운드 stdin 리더를 멈춘다."""
    from cli.interrupt import stdin_readers_paused

    with stdin_readers_paused():
        return _prompt_session.prompt(message, **kwargs)


async def prompt_with_stdin_pause_async(message, **kwargs):
    """비동기 prompt에서도 stdin 리더와 입력 경합이 없도록 한다."""
    from cli.interrupt import stdin_readers_paused

    with stdin_readers_paused():
        return await _prompt_session.prompt_async(message, **kwargs)


def print_user_input(text: str, session_id_short: str) -> None:
    """
    prompt_toolkit이 남긴 프롬프트 줄을 회색 배경 메시지로 교체한다.

    입력이 터미널 너비를 넘어 줄바꿈된 경우를 계산해 필요한 만큼 위로 올라간다.
    """
    width = shutil.get_terminal_size().columns
    # "[session_id] ❯ " 에 해당하는 표시 길이 (모드 접두어 포함 가능)
    prefix_len = len(f"[{session_id_short}] ❯ ")
    prefix_len += len(_mode_prefix(_current_mode))
    # 프롬프트 줄이 몇 행을 차지했는지 계산
    wrapped_lines = max(1, -(-( prefix_len + len(text)) // width))  # ceiling div
    sys.stdout.write(f"\033[{wrapped_lines}A\r\033[J")
    sys.stdout.flush()
    console.print(f"[bold blue]You[/bold blue] [white on grey23] {text} [/white on grey23]")


def get_input(session_id_short: str) -> str:
    """
    사용자 입력을 받아 반환합니다.

    - Tab: 자동완성 (@경로, /명령어)
    - Shift+Tab: Instant/Plan/TDD 모드 순환
    - ↑↓ 방향키: 이전 입력 히스토리
    - Ctrl-C: KeyboardInterrupt (종료)
    - Ctrl-D / EOF: 빈 문자열 반환
    """
    def _prompt_message():
        mode = get_current_mode()
        prefix = _mode_prefix(mode)
        prefix_html = ""
        if prefix:
            prefix_html = f"<ansimagenta><b>{prefix}</b></ansimagenta>"
        return HTML(
            f"{prefix_html}"
            f"<ansiblue><b>[{session_id_short}] ❯ </b></ansiblue>"
        )

    try:
        return prompt_with_stdin_pause(_prompt_message)
    except EOFError:
        return ""


async def get_input_async(session_id_short: str) -> str:
    """
    사용자 입력을 비동기 방식으로 받아 반환합니다.

    TDD 파이프라인처럼 이미 asyncio 이벤트 루프 안에서 prompt_toolkit 입력이
    필요한 경로에서 사용한다.
    """
    def _prompt_message():
        mode = get_current_mode()
        prefix = _mode_prefix(mode)
        prefix_html = ""
        if prefix:
            prefix_html = f"<ansimagenta><b>{prefix}</b></ansimagenta>"
        return HTML(
            f"{prefix_html}"
            f"<ansiblue><b>[{session_id_short}] ❯ </b></ansiblue>"
        )

    try:
        return await prompt_with_stdin_pause_async(_prompt_message)
    except EOFError:
        return ""
