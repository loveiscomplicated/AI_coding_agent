"""
tests/test_memory.py

memory/db.py & memory/session.py 단위 테스트.
외부 의존성 없음 — tmp_path fixture로 실제 SQLite I/O 검증.

실행:
    pytest tests/test_memory.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from llm.base import Message
from memory import Session, SessionManager, SessionSummary
from memory import db as mem_db


# ── 공통 픽스처 ────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def mgr(db_path):
    return SessionManager(db_path)


@pytest.fixture
def session(mgr):
    return mgr.new(title="테스트 세션", model="claude-sonnet-4-6")


# ── db.init_db ─────────────────────────────────────────────────────────────────


class TestInitDb:
    def test_creates_db_file(self, db_path):
        mem_db.init_db(db_path)
        assert db_path.exists()

    def test_creates_parent_directories(self, tmp_path):
        nested = tmp_path / "a" / "b" / "test.db"
        mem_db.init_db(nested)
        assert nested.exists()

    def test_idempotent(self, db_path):
        """두 번 호출해도 오류 없이 동작해야 합니다."""
        mem_db.init_db(db_path)
        mem_db.init_db(db_path)
        assert db_path.exists()


# ── db — 세션 CRUD ─────────────────────────────────────────────────────────────


class TestDbSession:
    def test_create_and_get(self, db_path):
        mem_db.init_db(db_path)
        mem_db.create_session("sid-1", title="제목", model="gpt-4", db_path=db_path)

        row = mem_db.get_session("sid-1", db_path=db_path)

        assert row is not None
        assert row["session_id"] == "sid-1"
        assert row["title"] == "제목"
        assert row["model"] == "gpt-4"

    def test_get_missing_returns_none(self, db_path):
        mem_db.init_db(db_path)
        assert mem_db.get_session("nonexistent", db_path=db_path) is None

    def test_list_sessions_order(self, db_path):
        mem_db.init_db(db_path)
        mem_db.create_session("a", title="A", db_path=db_path)
        mem_db.create_session("b", title="B", db_path=db_path)
        # B에 메시지를 추가해 updated_at을 갱신
        mem_db.add_message("b", "user", "hi", db_path=db_path)

        rows = mem_db.list_sessions(db_path=db_path)

        assert rows[0]["session_id"] == "b"  # 최신 updated_at이 먼저

    def test_list_includes_message_count(self, db_path):
        mem_db.init_db(db_path)
        mem_db.create_session("s1", db_path=db_path)
        mem_db.add_message("s1", "user", "msg1", db_path=db_path)
        mem_db.add_message("s1", "assistant", "msg2", db_path=db_path)

        rows = mem_db.list_sessions(db_path=db_path)
        assert rows[0]["message_count"] == 2

    def test_rename_session(self, db_path):
        mem_db.init_db(db_path)
        mem_db.create_session("s1", title="원래", db_path=db_path)
        mem_db.rename_session("s1", "변경됨", db_path=db_path)

        row = mem_db.get_session("s1", db_path=db_path)
        assert row["title"] == "변경됨"

    def test_delete_session(self, db_path):
        mem_db.init_db(db_path)
        mem_db.create_session("s1", db_path=db_path)
        mem_db.delete_session("s1", db_path=db_path)

        assert mem_db.get_session("s1", db_path=db_path) is None

    def test_delete_cascades_messages(self, db_path):
        mem_db.init_db(db_path)
        mem_db.create_session("s1", db_path=db_path)
        mem_db.add_message("s1", "user", "hello", db_path=db_path)
        mem_db.delete_session("s1", db_path=db_path)

        assert mem_db.get_messages("s1", db_path=db_path) == []


# ── db — 메시지 CRUD ───────────────────────────────────────────────────────────


class TestDbMessage:
    def test_add_and_get_string_content(self, db_path):
        mem_db.init_db(db_path)
        mem_db.create_session("s1", db_path=db_path)
        mem_db.add_message("s1", "user", "안녕", db_path=db_path)

        msgs = mem_db.get_messages("s1", db_path=db_path)

        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "안녕"

    def test_add_and_get_list_content(self, db_path):
        """tool_use / tool_result 구조체(list)가 그대로 복원돼야 합니다."""
        mem_db.init_db(db_path)
        mem_db.create_session("s1", db_path=db_path)
        payload = [{"type": "tool_result", "tool_use_id": "abc", "content": "ok"}]
        mem_db.add_message("s1", "user", payload, db_path=db_path)

        msgs = mem_db.get_messages("s1", db_path=db_path)

        assert msgs[0]["content"] == payload

    def test_messages_ordered_by_insertion(self, db_path):
        mem_db.init_db(db_path)
        mem_db.create_session("s1", db_path=db_path)
        for text in ["first", "second", "third"]:
            mem_db.add_message("s1", "user", text, db_path=db_path)

        msgs = mem_db.get_messages("s1", db_path=db_path)
        assert [m["content"] for m in msgs] == ["first", "second", "third"]

    def test_add_message_updates_session_updated_at(self, db_path):
        mem_db.init_db(db_path)
        mem_db.create_session("s1", db_path=db_path)
        before = mem_db.get_session("s1", db_path=db_path)["updated_at"]

        import time; time.sleep(0.01)
        mem_db.add_message("s1", "user", "hi", db_path=db_path)
        after = mem_db.get_session("s1", db_path=db_path)["updated_at"]

        assert after > before

    def test_add_message_returns_message_id(self, db_path):
        mem_db.init_db(db_path)
        mem_db.create_session("s1", db_path=db_path)
        mid = mem_db.add_message("s1", "user", "hi", db_path=db_path)

        assert isinstance(mid, int)
        assert mid >= 1


# ── SessionManager ─────────────────────────────────────────────────────────────


class TestSessionManagerNew:
    def test_returns_session(self, mgr):
        s = mgr.new(title="작업", model="ollama")

        assert isinstance(s, Session)
        assert s.title == "작업"
        assert s.model == "ollama"
        assert s.session_id != ""

    def test_session_id_is_uuid(self, mgr):
        import uuid
        s = mgr.new()
        uuid.UUID(s.session_id)  # 유효한 UUID이면 예외 없음

    def test_persisted_to_db(self, mgr):
        s = mgr.new(title="영속성 확인")
        assert mgr.load(s.session_id) is not None


class TestSessionManagerLoad:
    def test_load_existing(self, mgr, session):
        loaded = mgr.load(session.session_id)

        assert loaded is not None
        assert loaded.session_id == session.session_id
        assert loaded.title == session.title

    def test_load_missing_returns_none(self, mgr):
        assert mgr.load("does-not-exist") is None

    def test_load_restores_messages(self, mgr, session):
        mgr.append(session.session_id, Message(role="user", content="질문"))
        mgr.append(session.session_id, Message(role="assistant", content="답변"))

        loaded = mgr.load(session.session_id)

        assert len(loaded.messages) == 2
        assert loaded.messages[0].role == "user"
        assert loaded.messages[1].content == "답변"


class TestSessionManagerListAll:
    def test_empty(self, mgr):
        assert mgr.list_all() == []

    def test_returns_summaries(self, mgr):
        mgr.new(title="A")
        mgr.new(title="B")

        summaries = mgr.list_all()

        assert len(summaries) == 2
        assert all(isinstance(s, SessionSummary) for s in summaries)

    def test_message_count_in_summary(self, mgr, session):
        mgr.append(session.session_id, Message(role="user", content="hi"))
        mgr.append(session.session_id, Message(role="assistant", content="hello"))

        summary = mgr.list_all()[0]
        assert summary.message_count == 2


class TestSessionManagerRenameDelete:
    def test_rename(self, mgr, session):
        mgr.rename(session.session_id, "새 제목")

        loaded = mgr.load(session.session_id)
        assert loaded.title == "새 제목"

    def test_delete_removes_session(self, mgr, session):
        mgr.delete(session.session_id)

        assert mgr.load(session.session_id) is None

    def test_delete_removes_messages(self, mgr, session):
        mgr.append(session.session_id, Message(role="user", content="hi"))
        mgr.delete(session.session_id)

        assert mgr.list_all() == []


class TestSessionManagerAppend:
    def test_append_string_message(self, mgr, session):
        mgr.append(session.session_id, Message(role="user", content="안녕"))

        history = mgr.get_history(session.session_id)
        assert len(history) == 1
        assert history[0].content == "안녕"

    def test_append_list_message(self, mgr, session):
        """tool_result 구조체가 그대로 복원돼야 합니다."""
        payload = [{"type": "tool_result", "tool_use_id": "xyz", "content": "done"}]
        mgr.append(session.session_id, Message(role="user", content=payload))

        history = mgr.get_history(session.session_id)
        assert history[0].content == payload

    def test_append_many(self, mgr, session):
        messages = [
            Message(role="user", content="첫 번째"),
            Message(role="assistant", content="두 번째"),
            Message(role="user", content="세 번째"),
        ]
        mgr.append_many(session.session_id, messages)

        history = mgr.get_history(session.session_id)
        assert len(history) == 3
        assert history[2].content == "세 번째"


class TestSessionManagerGetHistory:
    def test_excludes_system_messages(self, mgr, session):
        mgr.append(session.session_id, Message(role="system", content="시스템 지시"))
        mgr.append(session.session_id, Message(role="user", content="사용자 입력"))

        history = mgr.get_history(session.session_id)

        roles = [m.role for m in history]
        assert "system" not in roles
        assert "user" in roles

    def test_empty_session_returns_empty_list(self, mgr, session):
        assert mgr.get_history(session.session_id) == []

    def test_history_usable_in_loop(self, mgr, session):
        """get_history 반환값이 list[Message] 타입이어야 합니다."""
        mgr.append(session.session_id, Message(role="user", content="질문"))
        history = mgr.get_history(session.session_id)

        assert isinstance(history, list)
        assert all(isinstance(m, Message) for m in history)
