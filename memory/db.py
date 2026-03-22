"""
memory/db.py — SQLite CRUD 레이어

테이블:
  sessions  : 세션 메타데이터 (id, title, model, created_at, updated_at)
  messages  : 대화 메시지 (session_id FK, role, content JSON, created_at)

공개 함수:
  init_db            : 테이블 초기화 (앱 시작 시 1회 호출)
  create_session     : 새 세션 행 삽입
  get_session        : 세션 단건 조회
  list_sessions      : 전체 세션 목록 조회
  rename_session     : 세션 제목 변경
  delete_session     : 세션 + 연결된 메시지 삭제
  add_message        : 메시지 추가
  get_messages       : 세션의 전체 메시지 조회
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# 기본 DB 경로 — SessionManager가 재정의 가능
DEFAULT_DB_PATH = Path("data/sessions.db")


# ── 연결 헬퍼 ─────────────────────────────────────────────────────────────────


@contextmanager
def _connect(db_path: Path):
    """SQLite 연결 컨텍스트 매니저 (WAL 모드 + foreign key 활성화)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 초기화 ────────────────────────────────────────────────────────────────────


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """테이블이 없으면 생성합니다. 앱 시작 시 1회 호출하세요."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id  TEXT PRIMARY KEY,
                title       TEXT NOT NULL DEFAULT '',
                model       TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                message_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,   -- JSON
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, message_id);
        """)


# ── 세션 CRUD ─────────────────────────────────────────────────────────────────


def create_session(
    session_id: str,
    title: str = "",
    model: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, title, model, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, title, model, now, now),
        )


def get_session(
    session_id: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def list_sessions(db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """최신 updated_at 순으로 전체 세션 목록 + 메시지 수 반환."""
    with _connect(db_path) as conn:
        rows = conn.execute("""
            SELECT s.*, COUNT(m.message_id) AS message_count
            FROM sessions s
            LEFT JOIN messages m ON s.session_id = m.session_id
            GROUP BY s.session_id
            ORDER BY s.updated_at DESC
        """).fetchall()
    return [dict(row) for row in rows]


def rename_session(
    session_id: str,
    title: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
            (title, _now(), session_id),
        )


def delete_session(
    session_id: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """세션과 연결된 모든 메시지를 삭제합니다 (CASCADE)."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


# ── 메시지 CRUD ───────────────────────────────────────────────────────────────


def add_message(
    session_id: str,
    role: str,
    content: str | list,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """
    메시지를 추가하고 message_id를 반환합니다.
    content가 list(tool_use / tool_result 구조체)이면 JSON 직렬화합니다.
    """
    content_json = json.dumps(content, ensure_ascii=False)
    now = _now()
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, role, content_json, now),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
    return cur.lastrowid  # type: ignore[return-value]


def get_messages(
    session_id: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """
    세션의 메시지를 삽입 순으로 반환합니다.
    content는 원래 타입(str 또는 list)으로 역직렬화됩니다.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE session_id = ? ORDER BY message_id",
            (session_id,),
        ).fetchall()

    result = []
    for row in rows:
        result.append(
            {
                "role": row["role"],
                "content": json.loads(row["content"]),
                "created_at": row["created_at"],
            }
        )
    return result
