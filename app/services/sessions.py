"""Conversation session store for Co-Pilot (SQLite, stdlib only)."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "sessions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    content TEXT NOT NULL,
    citations TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, created_at);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)


def create_session(title: Optional[str] = None) -> str:
    sid = uuid.uuid4().hex
    t = _now_ms()
    with _conn() as c:
        c.execute(
            "INSERT INTO sessions (id,title,created_at,updated_at) VALUES (?,?,?,?)",
            (sid, title or "New chat", t, t),
        )
    return sid


def add_message(
    session_id: str,
    role: str,
    content: str,
    citations: Optional[list[str]] = None,
) -> str:
    mid = uuid.uuid4().hex
    t = _now_ms()
    with _conn() as c:
        c.execute(
            "INSERT INTO messages (id,session_id,role,content,citations,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (mid, session_id, role, content,
             json.dumps(citations) if citations else None, t),
        )
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (t, session_id))
        # auto-title from first user msg
        if role == "user":
            cur = c.execute(
                "SELECT title FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if cur and cur["title"] in (None, "", "New chat"):
                c.execute(
                    "UPDATE sessions SET title=? WHERE id=?",
                    (content[:60], session_id),
                )
    return mid


def list_sessions(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id,title,created_at,updated_at FROM sessions"
            " ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str) -> Optional[dict]:
    with _conn() as c:
        s = c.execute(
            "SELECT id,title,created_at,updated_at FROM sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if not s:
            return None
        msgs = c.execute(
            "SELECT id,role,content,citations,created_at FROM messages"
            " WHERE session_id=? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
    return {
        **dict(s),
        "messages": [
            {**dict(m), "citations": json.loads(m["citations"]) if m["citations"] else []}
            for m in msgs
        ],
    }


def delete_session(session_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    return cur.rowcount > 0


# init on import (idempotent)
init_db()
