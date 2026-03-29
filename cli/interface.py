"""
cli/interface.py — 입출력, 색상, 포맷팅 (rich + prompt_toolkit)

공개 함수:
  print_banner       : 시작 배너 출력
  print_answer       : LLM 최종 답변을 Markdown으로 렌더링
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

if TYPE_CHECKING:
    from core.loop import ToolCall, ToolResult
    from core.undo import ChangeTracker
    from memory.session import SessionSummary
    from llm.base import Message

console = Console()

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


# PromptSession은 모듈 수준에서 한 번만 생성 (히스토리 유지)
_prompt_session: PromptSession = PromptSession(
    history=InMemoryHistory(),
    completer=AgentCompleter(),
    complete_while_typing=True,   # @ / / 입력 시 즉시 자동완성 목록 표시
)


# ── 배너 ──────────────────────────────────────────────────────────────────────


def print_banner() -> None:
    console.print(Panel(
        "[bold cyan]AI Coding Agent[/bold cyan]\n"
        "[dim]/help 로 명령어 목록 · Tab 으로 자동완성 · @경로 로 파일 첨부[/dim]",
        box=box.ROUNDED,
        expand=False,
    ))


# ── 답변 ──────────────────────────────────────────────────────────────────────


def print_answer(text: str) -> None:
    """LLM 최종 답변을 Markdown으로 렌더링합니다."""
    console.print()
    console.print(Markdown(text))
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

        try:
            answer = _prompt_session.prompt(
                HTML(
                    "<ansiyellow><b>"
                    "  승인하시겠습니까? [Y/n/a(항상)]: "
                    "</b></ansiyellow>"
                )
            )
            console.print()
            answer = answer.strip().lower()

            if answer in ("a", "always", "항상"):
                self._always.add(tc.name)
                console.print(
                    f"  [{_C_INFO}]{tc.name} 은(는) 이후 자동 승인됩니다.[/{_C_INFO}]"
                )
                self._record(tc)
                return True

            approved = answer in ("y", "yes", "")
            if approved:
                self._record(tc)
            return approved

        except (KeyboardInterrupt, EOFError):
            console.print()
            console.print(f"[{_C_INFO}]취소되었습니다.[/{_C_INFO}]")
            return False


def get_input(session_id_short: str) -> str:
    """
    사용자 입력을 받아 반환합니다.

    - Tab: 자동완성 (@경로, /명령어)
    - ↑↓ 방향키: 이전 입력 히스토리
    - Ctrl-C: KeyboardInterrupt (종료)
    - Ctrl-D / EOF: 빈 문자열 반환
    """
    try:
        return _prompt_session.prompt(
            HTML(f"<ansiblue><b>[{session_id_short}] ❯ </b></ansiblue>"),
        )
    except EOFError:
        return ""
