from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Memory:
    id: int
    text: str
    created_at: str


class MemoryStore:
    """Deliberately separate tables for chats, user-approved memories, and future knowledge."""
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, source TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        self.connection.commit()

    def add_memory(self, text: str) -> Memory:
        cursor = self.connection.execute("INSERT INTO memories(text) VALUES (?)", (text.strip(),))
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM memories WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return Memory(**dict(row))

    def search_memories(self, query: str, limit: int = 5) -> list[Memory]:
        words = [word for word in query.lower().split() if len(word) > 2]
        if not words:
            return []
        where = " OR ".join("lower(text) LIKE ?" for _ in words)
        rows = self.connection.execute(f"SELECT * FROM memories WHERE {where} ORDER BY id DESC LIMIT ?", tuple(f"%{word}%" for word in words) + (limit,)).fetchall()
        return [Memory(**dict(row)) for row in rows]

    def add_conversation(self, session_id: str, role: str, content: str) -> None:
        self.connection.execute("INSERT INTO conversations(session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
        self.connection.commit()

    def recent_conversation(self, session_id: str, limit: int = 12) -> list[dict[str, str]]:
        rows = self.connection.execute("SELECT role, content FROM conversations WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]
