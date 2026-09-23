from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


class Store:
    def __init__(self, path: str | Path | None = None):
        default = os.environ.get("ACLA_STATE", str(Path.home() / ".astra-critic-luna-actor" / "state.sqlite3"))
        self.path = Path(path or default).expanduser()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.init()

    def init(self) -> None:
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, goal TEXT NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('astra','luna')),
            name TEXT NOT NULL, workspace TEXT, tmux_session TEXT,
            command TEXT, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS pairs (
            luna_id TEXT PRIMARY KEY REFERENCES agents(id),
            astra_id TEXT NOT NULL REFERENCES agents(id),
            run_id TEXT NOT NULL REFERENCES runs(id),
            UNIQUE(run_id, astra_id, luna_id)
        );
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
            astra_id TEXT NOT NULL REFERENCES agents(id),
            luna_id TEXT NOT NULL REFERENCES agents(id), created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL REFERENCES threads(id),
            sender_id TEXT NOT NULL REFERENCES agents(id), recipient_id TEXT NOT NULL REFERENCES agents(id),
            body TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT, read_at TEXT
        );
        CREATE TABLE IF NOT EXISTS idempotency (
            sender_id TEXT NOT NULL, key TEXT NOT NULL, message_id INTEGER NOT NULL REFERENCES messages(id),
            PRIMARY KEY(sender_id, key)
        );
        CREATE INDEX IF NOT EXISTS messages_pending ON messages(recipient_id, delivered_at, id);
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def create_run(self, goal: str, run_id: str | None = None) -> str:
        run_id = run_id or new_id()
        existing = self.db.execute("SELECT goal FROM runs WHERE id=?", (run_id,)).fetchone()
        if existing:
            if existing["goal"] != goal:
                raise ValueError("run ID already exists with a different goal")
            return run_id
        stamp = now()
        self.db.execute("INSERT INTO runs VALUES (?, ?, 'working', ?, ?)", (run_id, goal, stamp, stamp))
        self.db.commit()
        return run_id

    def agent(self, agent_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not row:
            raise ValueError(f"unknown agent: {agent_id}")
        return row

    def register_agent(self, kind: str, name: str, *, workspace: str | None = None,
                       tmux_session: str | None = None, command: str | None = None,
                       agent_id: str | None = None) -> str:
        agent_id = agent_id or new_id()
        self.db.execute("INSERT INTO agents VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                        (agent_id, kind, name, workspace, tmux_session, command, now()))
        self.db.commit()
        return agent_id

    def ensure_agent(self, agent_id: str, kind: str, name: str, **kwargs) -> str:
        if agent_id:
            row = self.agent(agent_id)
            if row["kind"] != kind:
                raise ValueError("agent kind does not match existing identity")
            return agent_id
        return self.register_agent(kind, name, **kwargs)

    def pair(self, run_id: str, astra_id: str, luna_id: str) -> None:
        self.agent(astra_id); self.agent(luna_id)
        self.db.execute("INSERT OR REPLACE INTO pairs(luna_id, astra_id, run_id) VALUES (?, ?, ?)",
                        (luna_id, astra_id, run_id))
        self.db.commit()

    def existing_luna(self, run_id: str, name: str) -> sqlite3.Row | None:
        return self.db.execute("""SELECT l.* FROM pairs p JOIN agents l ON l.id=p.luna_id
            WHERE p.run_id=? AND l.name=?""", (run_id, name)).fetchone()

    def thread(self, run_id: str, astra_id: str, luna_id: str, thread_id: str | None = None) -> str:
        self.pair(run_id, astra_id, luna_id)
        existing = self.db.execute("SELECT id FROM threads WHERE run_id=? AND luna_id=?", (run_id, luna_id)).fetchone()
        if existing:
            return existing["id"]
        thread_id = thread_id or new_id()
        self.db.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?)", (thread_id, run_id, astra_id, luna_id, now()))
        self.db.commit()
        return thread_id

    def send(self, thread_id: str, sender_id: str, body: str, key: str | None = None) -> dict:
        thread = self.db.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
        if not thread:
            raise ValueError(f"unknown thread: {thread_id}")
        if sender_id not in {thread["astra_id"], thread["luna_id"]}:
            raise ValueError("sender is not a participant in this thread")
        recipient = thread["luna_id"] if sender_id == thread["astra_id"] else thread["astra_id"]
        if key:
            old = self.db.execute("SELECT message_id FROM idempotency WHERE sender_id=? AND key=?", (sender_id, key)).fetchone()
            if old:
                return dict(self.db.execute("SELECT * FROM messages WHERE id=?", (old["message_id"],)).fetchone())
        cur = self.db.execute("INSERT INTO messages(thread_id,sender_id,recipient_id,body,created_at) VALUES(?,?,?,?,?)",
                              (thread_id, sender_id, recipient, body, now()))
        message_id = cur.lastrowid
        if key:
            self.db.execute("INSERT INTO idempotency VALUES (?, ?, ?)", (sender_id, key, message_id))
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())

    def pending(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM messages WHERE delivered_at IS NULL ORDER BY id LIMIT ?", (limit,)).fetchall()

    def mark_delivered(self, message_id: int) -> None:
        self.db.execute("UPDATE messages SET delivered_at=? WHERE id=? AND delivered_at IS NULL", (now(), message_id))
        self.db.commit()

    def messages(self, thread_id: str) -> list[dict]:
        rows = self.db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY id", (thread_id,)).fetchall()
        return [dict(row) for row in rows]

    def status(self, run_id: str) -> dict:
        run = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise ValueError(f"unknown run: {run_id}")
        streams = self.db.execute("""SELECT p.*, a.name AS astra_name, l.name AS luna_name,
            t.id AS thread_id FROM pairs p JOIN agents a ON a.id=p.astra_id
            JOIN agents l ON l.id=p.luna_id LEFT JOIN threads t ON t.luna_id=p.luna_id
            WHERE p.run_id=?""", (run_id,)).fetchall()
        return {"run": dict(run), "streams": [dict(row) for row in streams]}

    def set_run_state(self, run_id: str, state: str) -> None:
        self.db.execute("UPDATE runs SET state=?, updated_at=? WHERE id=?", (state, now(), run_id))
        self.db.commit()
