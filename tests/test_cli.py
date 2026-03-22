"""
tests/test_cli.py

cli/interface.py & cli/commands.py 단위 테스트.
외부 의존성 없음.

실행:
    pytest tests/test_cli.py -v
"""

import io
import os
import sys
from dataclasses import dataclass
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from rich.console import Console

from cli.commands import Action, CommandResult, handle
from cli import interface as ui
from llm.base import Message
from memory import SessionManager


# ── 공통 픽스처 ────────────────────────────────────────────────────────────────


@pytest.fixture
def captured():
    """cli.interface.console 을 StringIO 기반 테스트 콘솔로 교체하고 버퍼를 반환."""
    buf = io.StringIO()
    test_console = Console(file=buf, highlight=False, markup=False, width=120)
    with patch("cli.interface.console", test_console):
        yield buf


def get_output(buf: io.StringIO) -> str:
    return buf.getvalue()


@pytest.fixture
def mgr(tmp_path):
    return SessionManager(tmp_path / "test.db")


@pytest.fixture
def session(mgr):
    return mgr.new(title="테스트 세션", model="claude-sonnet-4-6")


# ── interface: print_banner ────────────────────────────────────────────────────


class TestPrintBanner:
    def test_contains_product_name(self, captured):
        ui.print_banner()
        assert "AI Coding Agent" in get_output(captured)

    def test_contains_help_hint(self, captured):
        ui.print_banner()
        assert "/help" in get_output(captured)


# ── interface: print_answer ────────────────────────────────────────────────────


class TestPrintAnswer:
    def test_renders_plain_text(self, captured):
        ui.print_answer("안녕하세요")
        assert "안녕하세요" in get_output(captured)

    def test_renders_markdown_heading(self, captured):
        ui.print_answer("# 제목\n본문")
        out = get_output(captured)
        assert "제목" in out
        assert "본문" in out


# ── interface: print_tool_call ─────────────────────────────────────────────────


class TestPrintToolCall:
    def _make_tc(self, name, **kwargs):
        @dataclass
        class FakeTC:
            name: str
            input: dict
        return FakeTC(name=name, input=kwargs)

    def test_shows_tool_name(self, captured):
        tc = self._make_tc("get_outline", path="tools/code_tools.py")
        ui.print_tool_call(tc)
        assert "get_outline" in get_output(captured)

    def test_shows_argument_value(self, captured):
        tc = self._make_tc("read_file", path="main.py")
        ui.print_tool_call(tc)
        assert "main.py" in get_output(captured)

    def test_shows_tool_symbol(self, captured):
        tc = self._make_tc("list_directory", path=".")
        ui.print_tool_call(tc)
        assert "⚙" in get_output(captured)


# ── interface: print_tool_result ───────────────────────────────────────────────


class TestPrintToolResult:
    def _make_tr(self, content, is_error=False):
        @dataclass
        class FakeTR:
            content: str
            is_error: bool
        return FakeTR(content=content, is_error=is_error)

    def test_success_shows_checkmark(self, captured):
        tr = self._make_tr("결과 내용")
        ui.print_tool_result(tr)
        assert "✓" in get_output(captured)

    def test_success_shows_char_count(self, captured):
        tr = self._make_tr("hello")
        ui.print_tool_result(tr)
        assert "5 chars" in get_output(captured)

    def test_error_shows_cross(self, captured):
        tr = self._make_tr("파일 없음", is_error=True)
        ui.print_tool_result(tr)
        assert "✗" in get_output(captured)

    def test_error_shows_preview(self, captured):
        tr = self._make_tr("파일 없음: ghost.py", is_error=True)
        ui.print_tool_result(tr)
        assert "ghost.py" in get_output(captured)

    def test_error_truncates_long_content(self, captured):
        tr = self._make_tr("x" * 200, is_error=True)
        ui.print_tool_result(tr)
        out = get_output(captured)
        # 200자 전체가 출력되지 않아야 함 (80자 제한)
        assert "x" * 200 not in out


# ── interface: print_sessions ──────────────────────────────────────────────────


class TestPrintSessions:
    def test_empty_shows_info(self, captured):
        ui.print_sessions([])
        assert "없습니다" in get_output(captured)

    def test_shows_session_id_prefix(self, captured, mgr):
        s = mgr.new(title="작업A", model="claude")
        summaries = mgr.list_all()
        ui.print_sessions(summaries)
        assert s.session_id[:8] in get_output(captured)

    def test_shows_title(self, captured, mgr):
        mgr.new(title="중요한 작업", model="claude")
        ui.print_sessions(mgr.list_all())
        assert "중요한 작업" in get_output(captured)

    def test_shows_model(self, captured, mgr):
        mgr.new(title="", model="gpt-4o")
        ui.print_sessions(mgr.list_all())
        assert "gpt-4o" in get_output(captured)

    def test_multiple_sessions_all_shown(self, captured, mgr):
        mgr.new(title="첫 번째")
        mgr.new(title="두 번째")
        ui.print_sessions(mgr.list_all())
        out = get_output(captured)
        assert "첫 번째" in out
        assert "두 번째" in out


# ── interface: print_history ───────────────────────────────────────────────────


class TestPrintHistory:
    def test_empty_shows_info(self, captured):
        ui.print_history([])
        assert "없습니다" in get_output(captured)

    def test_user_string_message(self, captured):
        ui.print_history([Message(role="user", content="안녕하세요")])
        out = get_output(captured)
        assert "You" in out
        assert "안녕하세요" in out

    def test_user_list_message_shows_tool_result_label(self, captured):
        payload = [{"type": "tool_result", "tool_use_id": "abc", "content": "ok"}]
        ui.print_history([Message(role="user", content=payload)])
        assert "tool_result" in get_output(captured)

    def test_assistant_string_message(self, captured):
        ui.print_history([Message(role="assistant", content="반갑습니다")])
        out = get_output(captured)
        assert "Agent" in out
        assert "반갑습니다" in out

    def test_assistant_list_message_extracts_text(self, captured):
        content = [{"type": "text", "text": "추출된 텍스트"}]
        ui.print_history([Message(role="assistant", content=content)])
        assert "추출된 텍스트" in get_output(captured)


# ── interface: print_error / print_info ───────────────────────────────────────


class TestPrintErrorInfo:
    def test_print_error_shows_message(self, captured):
        ui.print_error("뭔가 잘못됨")
        out = get_output(captured)
        assert "✗" in out
        assert "뭔가 잘못됨" in out

    def test_print_info_shows_message(self, captured):
        ui.print_info("안내 메시지")
        assert "안내 메시지" in get_output(captured)


# ── commands: handle — 일반 입력 ───────────────────────────────────────────────


class TestHandleNonCommand:
    def test_plain_text_returns_none(self, mgr, session, captured):
        assert handle("안녕하세요", mgr, session) is None

    def test_empty_string_returns_none(self, mgr, session, captured):
        assert handle("", mgr, session) is None

    def test_text_starting_with_slash_in_middle_returns_none(self, mgr, session, captured):
        assert handle("path/to/file", mgr, session) is None


# ── commands: /help ────────────────────────────────────────────────────────────


class TestCommandHelp:
    def test_returns_none_action(self, mgr, session, captured):
        result = handle("/help", mgr, session)
        assert result.action == Action.NONE

    def test_output_contains_all_commands(self, mgr, session, captured):
        handle("/help", mgr, session)
        out = get_output(captured)
        for cmd in ["/help", "/history", "/sessions", "/new", "/load", "/rename", "/delete", "/exit"]:
            assert cmd in out


# ── commands: /history ────────────────────────────────────────────────────────


class TestCommandHistory:
    def test_empty_session_shows_info(self, mgr, session, captured):
        result = handle("/history", mgr, session)
        assert result.action == Action.NONE
        assert "없습니다" in get_output(captured)

    def test_shows_saved_messages(self, mgr, session, captured):
        mgr.append(session.session_id, Message(role="user", content="기억해줘"))
        result = handle("/history", mgr, session)
        assert result.action == Action.NONE
        assert "기억해줘" in get_output(captured)


# ── commands: /sessions ───────────────────────────────────────────────────────


class TestCommandSessions:
    def test_returns_none_action(self, mgr, session, captured):
        result = handle("/sessions", mgr, session)
        assert result.action == Action.NONE

    def test_empty_shows_info(self, mgr, captured):
        # 세션 없는 mgr
        empty_mgr = SessionManager(mgr.db_path.parent / "empty.db")
        empty_session = empty_mgr.new()
        empty_mgr.delete(empty_session.session_id)

        result = handle("/sessions", empty_mgr, empty_session)
        assert result.action == Action.NONE
        assert "없습니다" in get_output(captured)


# ── commands: /new ────────────────────────────────────────────────────────────


class TestCommandNew:
    def test_returns_new_session_action(self, mgr, session, captured):
        result = handle("/new", mgr, session)
        assert result.action == Action.NEW_SESSION

    def test_new_session_has_given_title(self, mgr, session, captured):
        result = handle("/new 새 프로젝트", mgr, session)
        assert result.session.title == "새 프로젝트"

    def test_new_session_inherits_model(self, mgr, session, captured):
        result = handle("/new", mgr, session)
        assert result.session.model == session.model

    def test_new_session_persisted(self, mgr, session, captured):
        result = handle("/new 영속성 확인", mgr, session)
        assert mgr.load(result.session.session_id) is not None


# ── commands: /load ───────────────────────────────────────────────────────────


class TestCommandLoad:
    def test_load_by_prefix(self, mgr, session, captured):
        prefix = session.session_id[:6]
        result = handle(f"/load {prefix}", mgr, session)
        assert result.action == Action.LOAD_SESSION
        assert result.session.session_id == session.session_id

    def test_load_restores_messages(self, mgr, session, captured):
        mgr.append(session.session_id, Message(role="user", content="복원 확인"))
        prefix = session.session_id[:6]
        result = handle(f"/load {prefix}", mgr, session)
        assert len(result.session.messages) == 1

    def test_no_arg_returns_none_action(self, mgr, session, captured):
        result = handle("/load", mgr, session)
        assert result.action == Action.NONE
        assert "✗" in get_output(captured)

    def test_no_match_returns_none_action(self, mgr, session, captured):
        result = handle("/load zzz_no_match", mgr, session)
        assert result.action == Action.NONE

    def test_ambiguous_prefix_returns_none_action(self, mgr, captured):
        # 두 세션이 같은 prefix를 공유하도록 UUID를 고정
        from unittest.mock import patch
        import uuid
        ids = ["aaaa0001-0000-0000-0000-000000000000",
               "aaaa0002-0000-0000-0000-000000000000"]
        id_iter = iter(ids)
        with patch.object(uuid, "uuid4", side_effect=lambda: uuid.UUID(next(id_iter))):
            s1 = mgr.new(title="A")
            s2 = mgr.new(title="B")

        result = handle("/load aaaa", mgr, s1)
        assert result.action == Action.NONE


# ── commands: /rename ─────────────────────────────────────────────────────────


class TestCommandRename:
    def test_renames_session(self, mgr, session, captured):
        handle("/rename 새 제목", mgr, session)
        loaded = mgr.load(session.session_id)
        assert loaded.title == "새 제목"

    def test_returns_none_action(self, mgr, session, captured):
        result = handle("/rename 새 제목", mgr, session)
        assert result.action == Action.NONE

    def test_no_arg_returns_error(self, mgr, session, captured):
        result = handle("/rename", mgr, session)
        assert result.action == Action.NONE
        assert "✗" in get_output(captured)


# ── commands: /delete ─────────────────────────────────────────────────────────


class TestCommandDelete:
    def test_deletes_old_session(self, mgr, session, captured):
        old_id = session.session_id
        handle("/delete", mgr, session)
        assert mgr.load(old_id) is None

    def test_returns_new_session(self, mgr, session, captured):
        old_id = session.session_id
        result = handle("/delete", mgr, session)
        assert result.action == Action.NEW_SESSION
        assert result.session.session_id != old_id

    def test_new_session_inherits_model(self, mgr, session, captured):
        result = handle("/delete", mgr, session)
        assert result.session.model == session.model


# ── commands: /exit & /quit ───────────────────────────────────────────────────


class TestCommandExit:
    def test_exit_returns_exit_action(self, mgr, session, captured):
        result = handle("/exit", mgr, session)
        assert result.action == Action.EXIT

    def test_quit_returns_exit_action(self, mgr, session, captured):
        result = handle("/quit", mgr, session)
        assert result.action == Action.EXIT


# ── commands: 알 수 없는 명령어 ───────────────────────────────────────────────


class TestCommandUnknown:
    def test_unknown_command_returns_none_action(self, mgr, session, captured):
        result = handle("/unknown_cmd", mgr, session)
        assert result.action == Action.NONE

    def test_unknown_command_shows_error(self, mgr, session, captured):
        handle("/unknown_cmd", mgr, session)
        assert "✗" in get_output(captured)