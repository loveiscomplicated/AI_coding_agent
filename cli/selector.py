"""
cli/selector.py — 인라인 방향키 선택기

prompt_toolkit Application 기반 mini-app으로 터미널에 인라인 선택 위젯을 표시한다.
↑↓ 방향키로 커서 이동, 엔터로 확정, Esc로 취소.
"""

from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension


@dataclass
class SelectOption:
    label: str
    value: str
    description: str = ""


def _description_line_count(option: SelectOption) -> int:
    if not option.description:
        return 0
    return len(option.description.splitlines())


def _widget_height(options: list[SelectOption]) -> int:
    max_description_lines = max((_description_line_count(option) for option in options), default=0)
    return len(options) + max_description_lines


def inline_select(
    options: list[SelectOption],
    message: str | None = None,
    detail: str | None = None,
    default_index: int = 0,
    *,
    allow_back: bool = False,
) -> str | None:
    """
    터미널에 인라인 선택기를 표시하고 사용자 선택을 반환한다.

    Args:
        options: 선택지 목록 (최소 2개)
        message: 선택기 위에 표시할 메시지 (rich 마크업 지원)
        detail: 메시지 아래 추가 정보
        default_index: 초기 커서 위치

    Returns:
        선택된 option의 value.
        Esc 취소 시 None, allow_back=True 에서 ← 입력 시 "__back__".
    """
    from cli.interface import console

    if len(options) < 2:
        raise ValueError("inline_select requires at least 2 options")

    if message:
        console.print(message)
    if detail:
        console.print(detail)

    selected = [max(0, min(default_index, len(options) - 1))]
    result: list[str | None] = [None]

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        selected[0] = max(0, selected[0] - 1)
        event.app.invalidate()

    @kb.add("down")
    def _down(event):
        selected[0] = min(len(options) - 1, selected[0] + 1)
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event):
        result[0] = options[selected[0]].value
        event.app.exit()

    @kb.add("right")
    def _right(event):
        result[0] = options[selected[0]].value
        event.app.exit()

    @kb.add("escape")
    def _escape(event):
        result[0] = None
        event.app.exit()

    @kb.add("q")
    @kb.add("Q")
    def _quit(event):
        result[0] = None
        event.app.exit()

    if allow_back:
        @kb.add("left")
        def _left(event):
            result[0] = "__back__"
            event.app.exit()

    def _render():
        lines: list[tuple[str, str]] = []
        for i, opt in enumerate(options):
            if i == selected[0]:
                lines.append(("bold ansicyan", f"  ▸ {opt.label}\n"))
                if opt.description:
                    for line in opt.description.splitlines():
                        lines.append(("ansigray", f"    {line}\n"))
            else:
                lines.append(("ansigray", f"    {opt.label}\n"))
        return FormattedText(lines)

    control = FormattedTextControl(text=_render, focusable=True, show_cursor=False)
    window = Window(
        content=control,
        height=Dimension.exact(_widget_height(options)),
        dont_extend_height=True,
        always_hide_cursor=True,
    )
    layout = Layout(HSplit([window]))

    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=False,
        erase_when_done=True,
    )
    from cli.interrupt import pause_stdin_readers, resume_stdin_readers
    pause_stdin_readers()
    try:
        app.run()
    finally:
        resume_stdin_readers()

    if result[0] is None:
        console.print("  [dim]✗ 취소됨[/dim]")
    elif result[0] == "__back__":
        console.print("  [dim]← 이전 단계[/dim]")
    else:
        chosen_label = next(
            (o.label for o in options if o.value == result[0]),
            result[0],
        )
        console.print(f"  [dim]✓ {chosen_label}[/dim]")

    return result[0]
