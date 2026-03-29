"""
cli/commands.py — 슬래시 명령어 처리

지원 명령어:
  /help                  — 명령어 목록 출력
  /history               — 현재 세션 대화 히스토리 출력
  /sessions              — 전체 세션 목록 출력
  /new [제목]            — 새 세션 시작
  /load <id-prefix>      — 세션 ID(앞 8자 이상)로 세션 불러오기
  /rename <새 제목>      — 현재 세션 제목 변경
  /delete                — 현재 세션 삭제 후 새 세션 시작
  /exit | /quit          — 종료

handle(raw, mgr, session) → CommandResult | None
  None이면 일반 대화 입력으로 처리합니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

from cli import interface as ui

if TYPE_CHECKING:
    from core.undo import ChangeTracker
    from memory.session import Session, SessionManager


# ── 액션 타입 ─────────────────────────────────────────────────────────────────


class Action(Enum):
    NONE           = auto()  # 명령어 처리 완료, 루프 계속
    EXIT           = auto()  # 프로그램 종료
    NEW_SESSION    = auto()  # 새 세션으로 교체
    LOAD_SESSION   = auto()  # 불러온 세션으로 교체


@dataclass
class CommandResult:
    action: Action
    session: Session | None = None  # NEW_SESSION / LOAD_SESSION 시 새 세션


# ── 핸들러 ────────────────────────────────────────────────────────────────────


def handle(
    raw: str,
    mgr: SessionManager,
    session: Session,
    tracker: ChangeTracker | None = None,
) -> CommandResult | None:
    """
    슬래시 명령어를 처리합니다.

    Returns:
        CommandResult  — 슬래시 명령어였을 때
        None           — 일반 대화 입력일 때
    """
    stripped = raw.strip()
    if not stripped.startswith("/"):
        return None

    parts = stripped.split(maxsplit=1)
    cmd   = parts[0].lower()
    arg   = parts[1] if len(parts) > 1 else ""

    match cmd:
        case "/help":
            return _help()
        case "/history":
            return _history(mgr, session)
        case "/sessions":
            return _sessions(mgr)
        case "/new":
            return _new(mgr, session, title=arg, model=session.model)
        case "/load":
            return _load(mgr, arg)
        case "/rename":
            return _rename(mgr, session, title=arg)
        case "/delete":
            return _delete(mgr, session)
        case "/undo":
            return _undo(tracker, undo_all=arg.strip().lower() == "all")
        case "/exit" | "/quit":
            return CommandResult(action=Action.EXIT)
        case _:
            ui.print_error(f"알 수 없는 명령어: {cmd}  (/help 로 목록 확인)")
            return CommandResult(action=Action.NONE)


# ── 개별 명령 구현 ────────────────────────────────────────────────────────────


def _help() -> CommandResult:
    lines = [
        ("",               ""),
        ("/help",          "명령어 목록 출력"),
        ("/history",       "현재 세션의 대화 히스토리 출력"),
        ("/sessions",      "저장된 세션 목록 출력"),
        ("/new [제목]",    "새 세션 시작"),
        ("/load <id>",     "세션 ID 앞자리로 세션 불러오기"),
        ("/rename <제목>", "현재 세션 제목 변경"),
        ("/delete",        "현재 세션 삭제 후 새 세션 시작"),
        ("/undo",          "마지막 파일 변경 되돌리기"),
        ("/undo all",      "이번 세션의 모든 파일 변경 되돌리기"),
        ("/exit",          "종료"),
        ("",               ""),
    ]
    for cmd, desc in lines:
        if cmd:
            ui.print_info(f"  {cmd:<20} {desc}")
        else:
            ui.print_info("")
    return CommandResult(action=Action.NONE)


def _history(mgr: SessionManager, session: Session) -> CommandResult:
    history = mgr.get_history(session.session_id)
    ui.print_history(history)
    return CommandResult(action=Action.NONE)


def _sessions(mgr: SessionManager) -> CommandResult:
    summaries = mgr.list_all()
    ui.print_sessions(summaries)
    return CommandResult(action=Action.NONE)


def _new(mgr: SessionManager, old: Session, title: str, model: str) -> CommandResult:
    new_session = mgr.new(title=title, model=model)
    ui.print_info(f"새 세션 시작: [{new_session.session_id[:8]}] {title or '(제목 없음)'}")
    return CommandResult(action=Action.NEW_SESSION, session=new_session)


def _load(mgr: SessionManager, prefix: str) -> CommandResult:
    if not prefix:
        ui.print_error("/load <세션 ID> 형식으로 입력하세요.")
        return CommandResult(action=Action.NONE)

    summaries = mgr.list_all()
    matches = [s for s in summaries if s.session_id.startswith(prefix)]

    if not matches:
        ui.print_error(f"'{prefix}'로 시작하는 세션이 없습니다. /sessions 로 확인하세요.")
        return CommandResult(action=Action.NONE)

    if len(matches) > 1:
        ui.print_error(f"'{prefix}'에 해당하는 세션이 {len(matches)}개입니다. ID를 더 길게 입력하세요.")
        ui.print_sessions(matches)
        return CommandResult(action=Action.NONE)

    loaded = mgr.load(matches[0].session_id)
    if loaded is None:
        ui.print_error("세션을 불러오는 데 실패했습니다.")
        return CommandResult(action=Action.NONE)

    ui.print_info(
        f"세션 불러옴: [{loaded.session_id[:8]}] "
        f"{loaded.title or '(제목 없음)'} ({len(loaded.messages)}개 메시지)"
    )
    return CommandResult(action=Action.LOAD_SESSION, session=loaded)


def _rename(mgr: SessionManager, session: Session, title: str) -> CommandResult:
    if not title:
        ui.print_error("/rename <새 제목> 형식으로 입력하세요.")
        return CommandResult(action=Action.NONE)

    mgr.rename(session.session_id, title)
    session.title = title
    ui.print_info(f"세션 제목 변경: '{title}'")
    return CommandResult(action=Action.NONE)


def _undo(tracker: ChangeTracker | None, undo_all: bool = False) -> CommandResult:
    if tracker is None:
        ui.print_error("undo 기능을 사용할 수 없습니다.")
        return CommandResult(action=Action.NONE)

    if tracker.stack_size == 0:
        ui.print_info("되돌릴 변경사항이 없습니다.")
        return CommandResult(action=Action.NONE)

    if undo_all:
        results = tracker.undo_all()
        for path, ok in results:
            if ok:
                ui.print_info(f"  복구됨: {path}")
            else:
                ui.print_error(f"  복구 실패: {path}")
        ui.print_info(f"총 {len(results)}개 변경사항 되돌림 완료.")
    else:
        path, ok = tracker.undo_last()
        if ok:
            ui.print_info(f"복구됨: {path}")
        else:
            ui.print_error(f"복구 실패: {path}")

    return CommandResult(action=Action.NONE)


def _delete(mgr: SessionManager, session: Session) -> CommandResult:
    mgr.delete(session.session_id)
    ui.print_info(f"세션 삭제됨: [{session.session_id[:8]}]")
    new_session = mgr.new(model=session.model)
    ui.print_info(f"새 세션 시작: [{new_session.session_id[:8]}]")
    return CommandResult(action=Action.NEW_SESSION, session=new_session)
