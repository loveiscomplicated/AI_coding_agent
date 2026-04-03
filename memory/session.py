"""
memory/session.py — 세션 생성·불러오기 고수준 인터페이스

SessionSummary : 목록 표시용 경량 세션 정보
Session        : 세션 메타 + 전체 메시지 히스토리
SessionManager : CRUD 진입점 (db.py를 래핑)

사용 예:
    mgr = SessionManager()
    session = mgr.new(title="리팩토링 작업", model="claude-sonnet-4-6")

    # 대화 후 메시지 저장
    mgr.append(session.session_id, Message(role="user", content="안녕"))
    mgr.append(session.session_id, Message(role="assistant", content="안녕하세요!"))

    # 다음 대화에서 히스토리 복원
    history = mgr.get_history(session.session_id)   # list[Message]
    loop.run("계속 작업해줘", history=history)

    # 세션 목록
    for s in mgr.list_all():
        print(s.session_id, s.title, s.message_count)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from llm.base import Message
from memory import db
from memory.db import DEFAULT_DB_PATH


# ── 데이터 클래스 ─────────────────────────────────────────────────────────────


@dataclass
class SessionSummary:
    """세션 목록에서 한 행에 해당하는 경량 뷰."""

    session_id: str
    title: str
    model: str
    created_at: str
    updated_at: str
    message_count: int = 0


@dataclass
class Session:
    """세션 메타데이터 + 복원된 메시지 히스토리."""

    session_id: str
    title: str
    model: str
    created_at: str
    messages: list[Message] = field(default_factory=list)


# ── SessionManager ────────────────────────────────────────────────────────────


class SessionManager:
    """
    세션 생명주기 전체를 담당합니다.

    Args:
        db_path: SQLite 파일 경로. 기본값 agent-data/sessions.db.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        db.init_db(db_path)

    # ── 세션 관리 ──────────────────────────────────────────────────────────────

    def new(self, title: str = "", model: str = "") -> Session:
        """새 세션을 생성하고 반환합니다."""
        session_id = str(uuid.uuid4())
        db.create_session(session_id, title=title, model=model, db_path=self.db_path)
        row = db.get_session(session_id, db_path=self.db_path)
        return Session(
            session_id=session_id,
            title=title,
            model=model,
            created_at=row["created_at"],  # type: ignore[index]
        )

    def load(self, session_id: str) -> Session | None:
        """세션 ID로 세션과 전체 메시지 히스토리를 불러옵니다."""
        row = db.get_session(session_id, db_path=self.db_path)
        if row is None:
            return None

        raw_messages = db.get_messages(session_id, db_path=self.db_path)
        messages = [Message(role=m["role"], content=m["content"]) for m in raw_messages]

        return Session(
            session_id=session_id,
            title=row["title"],
            model=row["model"],
            created_at=row["created_at"],
            messages=messages,
        )

    def list_all(self) -> list[SessionSummary]:
        """전체 세션 목록을 최신순으로 반환합니다."""
        rows = db.list_sessions(db_path=self.db_path)
        return [
            SessionSummary(
                session_id=r["session_id"],
                title=r["title"],
                model=r["model"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                message_count=r["message_count"],
            )
            for r in rows
        ]

    def rename(self, session_id: str, title: str) -> None:
        """세션 제목을 변경합니다."""
        db.rename_session(session_id, title, db_path=self.db_path)

    def delete(self, session_id: str) -> None:
        """세션과 연결된 모든 메시지를 삭제합니다."""
        db.delete_session(session_id, db_path=self.db_path)

    # ── 메시지 관리 ────────────────────────────────────────────────────────────

    def append(self, session_id: str, message: Message) -> None:
        """대화 메시지 하나를 DB에 저장합니다."""
        db.add_message(
            session_id,
            role=message.role,
            content=message.content,
            db_path=self.db_path,
        )

    def append_many(self, session_id: str, messages: list[Message]) -> None:
        """여러 메시지를 순서대로 저장합니다."""
        for message in messages:
            self.append(session_id, message)

    def get_history(self, session_id: str) -> list[Message]:
        """
        세션의 대화 히스토리를 list[Message]로 반환합니다.
        loop.run(history=...) 에 바로 전달할 수 있습니다.

        system 메시지는 loop가 자체적으로 붙이므로 여기서는 제외합니다.
        """
        raw = db.get_messages(session_id, db_path=self.db_path)
        return [
            Message(role=m["role"], content=m["content"])
            for m in raw
            if m["role"] != "system"
        ]
