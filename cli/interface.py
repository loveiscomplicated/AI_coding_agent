"""
cli/interface.py — 입출력, 색상, 포맷팅 (rich 기반)

공개 함수:
  print_banner       : 시작 배너 출력
  print_answer       : LLM 최종 답변을 Markdown으로 렌더링
  print_tool_call    : 도구 호출 시작 표시
  print_tool_result  : 도구 실행 결과 표시
  print_sessions     : 세션 목록 테이블 출력
  print_history      : 현재 세션의 대화 히스토리 출력
  print_error        : 에러 메시지 (빨간색)
  print_info         : 안내 메시지 (회색)
  get_input          : 사용자 입력 프롬프트
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box

if TYPE_CHECKING:
    from core.loop import ToolCall, ToolResult
    from memory.session import SessionSummary
    from llm.base import Message

console = Console()

# ── 색상 팔레트 ───────────────────────────────────────────────────────────────
_C_TOOL   = "cyan"
_C_OK     = "green"
_C_ERR    = "red"
_C_INFO   = "dim white"
_C_PROMPT = "bold blue"


# ── 배너 ──────────────────────────────────────────────────────────────────────


def print_banner() -> None:
    console.print(Panel(
        "[bold cyan]AI Coding Agent[/bold cyan]\n"
        "[dim]/help 로 명령어 목록을 확인하세요[/dim]",
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
                # content가 list인 경우 텍스트 블록만 추출
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


def get_input(session_id_short: str) -> str:
    """사용자 입력을 받아 반환합니다. EOF(Ctrl-D)이면 빈 문자열을 반환합니다."""
    try:
        return console.input(f"[{_C_PROMPT}][{session_id_short}] ❯ [/{_C_PROMPT}]")
    except EOFError:
        return ""
