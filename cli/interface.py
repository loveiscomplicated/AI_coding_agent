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
import re
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
    NORMAL = "일반"
    TDD = "TDD"


class ModeChangeStatus(Enum):
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ModeChangeResult:
    status: ModeChangeStatus
    mode: CLIMode


_current_mode: CLIMode = CLIMode.NORMAL
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
        _current_mode = CLIMode.NORMAL


def get_tdd_unavailable_message() -> str:
    return _tdd_unavailable_message


def request_mode_change(target: CLIMode) -> ModeChangeResult:
    global _current_mode

    if target == CLIMode.TDD and not _tdd_available:
        _current_mode = CLIMode.NORMAL
        return ModeChangeResult(status=ModeChangeStatus.BLOCKED, mode=_current_mode)

    if _current_mode == target:
        return ModeChangeResult(status=ModeChangeStatus.UNCHANGED, mode=_current_mode)

    _current_mode = target
    return ModeChangeResult(status=ModeChangeStatus.CHANGED, mode=_current_mode)


def toggle_mode() -> ModeChangeResult:
    target = CLIMode.TDD if _current_mode == CLIMode.NORMAL else CLIMode.NORMAL
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

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor

        # @ 경로 자동완성
        at_match = re.search(r"@(\S*)$", text)
        if at_match:
            path_prefix = at_match.group(1)
            path_doc = Document(path_prefix, len(path_prefix))
            yield from self._path_completer.get_completions(path_doc, complete_event)
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


class ApprovalHandler:
    """
    도구 실행 승인을 관리합니다.

    세션 동안 "항상 승인" 상태를 기억하며, 같은 도구가 다시 요청되면
    묻지 않고 자동 승인합니다.

    선택지:
        Y / Enter — 이번 한 번 승인
        n         — 거부
        a         — 항상 승인 (이후 같은 도구는 자동 실행)
    """

    def __init__(self, tracker: ChangeTracker | None = None) -> None:
        self._always: set[str] = set()
        self._tracker = tracker

    def _record(self, tc: ToolCall) -> None:
        """파일 수정 도구 실행 전 원본을 tracker에 기록합니다."""
        if self._tracker and tc.name in _FILE_MODIFYING_TOOLS:
            path = tc.input.get("path", "")
            if path:
                self._tracker.record(path)

    def __call__(self, tc: ToolCall) -> bool:
        # 항상 승인으로 등록된 도구는 묻지 않고 바로 실행
        if tc.name in self._always:
            console.print(
                f"  [{_C_INFO}]✓ 자동 승인 ({tc.name})[/{_C_INFO}]"
            )
            self._record(tc)
            return True

        _show_tool_preview(tc)

        from cli.selector import SelectOption, inline_select

        options = [
            SelectOption(label="승인", value="yes"),
            SelectOption(label="항상 승인", value="always"),
            SelectOption(label="거부", value="no"),
        ]
        try:
            selected = inline_select(options)
        except (KeyboardInterrupt, EOFError):
            console.print()
            console.print(f"[{_C_INFO}]취소되었습니다.[/{_C_INFO}]")
            return False

        if selected == "always":
            self._always.add(tc.name)
            console.print(
                f"  [{_C_INFO}]{tc.name} 은(는) 이후 자동 승인됩니다.[/{_C_INFO}]"
            )
            self._record(tc)
            return True

        if selected == "yes":
            self._record(tc)
            return True

        return False


def get_input(session_id_short: str) -> str:
    """
    사용자 입력을 받아 반환합니다.

    - Tab: 자동완성 (@경로, /명령어)
    - Shift+Tab: 일반/TDD 모드 토글
    - ↑↓ 방향키: 이전 입력 히스토리
    - Ctrl-C: KeyboardInterrupt (종료)
    - Ctrl-D / EOF: 빈 문자열 반환
    """
    def _prompt_message():
        prefix_html = ""
        if get_current_mode() == CLIMode.TDD:
            prefix_html = "<ansimagenta><b>[TDD] </b></ansimagenta>"
        return HTML(
            f"{prefix_html}"
            f"<ansiblue><b>[{session_id_short}] ❯ </b></ansiblue>"
        )

    try:
        return _prompt_session.prompt(_prompt_message)
    except EOFError:
        return ""
