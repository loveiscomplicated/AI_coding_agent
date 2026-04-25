"""
cli/selector.py — 인라인 방향키 선택기

prompt_toolkit Application 기반 mini-app으로 터미널에 인라인 선택 위젯을 표시한다.
↑↓ 방향키로 커서 이동, 엔터로 확정, Esc로 취소.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from prompt_toolkit import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, ScrollOffsets, Window
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


def _preferred_widget_height(options: list[SelectOption]) -> int:
    """현재 터미널 높이에 맞춰 선택기 선호 높이를 제한한다."""
    _columns, lines = shutil.get_terminal_size(fallback=(80, 24))
    # message/detail와 프롬프트 푸터를 고려해 약간의 여유를 남긴다.
    max_visible = max(3, lines - 6)
    return max(1, min(_widget_height(options), max_visible))


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

    def _cursor_position() -> Point:
        # 선택된 항목 줄을 cursor 기준점으로 삼아, 작은 화면에서도 해당 위치로 스크롤된다.
        return Point(x=0, y=selected[0])

    control = FormattedTextControl(
        text=_render,
        focusable=True,
        show_cursor=False,
        get_cursor_position=_cursor_position,
    )
    window = Window(
        content=control,
        height=Dimension(min=1, preferred=_preferred_widget_height(options)),
        dont_extend_height=True,
        always_hide_cursor=True,
        scroll_offsets=ScrollOffsets(top=1, bottom=1),
    )
    layout = Layout(HSplit([window]))

    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=False,
        erase_when_done=True,
    )
    from cli.interrupt import stdin_readers_paused
    with stdin_readers_paused():
        app.run()

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


async def inline_select_async(
    options: list[SelectOption],
    message: str | None = None,
    detail: str | None = None,
    default_index: int = 0,
    *,
    allow_back: bool = False,
) -> str | None:
    """
    asyncio 이벤트 루프 내부에서 사용할 비동기 인라인 선택기.
    """
    from cli.interface import console

    if len(options) < 2:
        raise ValueError("inline_select_async requires at least 2 options")

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

    def _cursor_position() -> Point:
        return Point(x=0, y=selected[0])

    control = FormattedTextControl(
        text=_render,
        focusable=True,
        show_cursor=False,
        get_cursor_position=_cursor_position,
    )
    window = Window(
        content=control,
        height=Dimension(min=1, preferred=_preferred_widget_height(options)),
        dont_extend_height=True,
        always_hide_cursor=True,
        scroll_offsets=ScrollOffsets(top=1, bottom=1),
    )
    layout = Layout(HSplit([window]))

    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=False,
        erase_when_done=True,
    )
    from cli.interrupt import stdin_readers_paused
    with stdin_readers_paused():
        await app.run_async()

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
