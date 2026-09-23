from __future__ import annotations

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
    """Private SQLite state for runs, mappings, threads, and full messages."""

    def __init__(self, path: str | Path | None = None):
        default = os.environ.get("ACLA_STATE", str(Path.home() / ".astra-critic-luna-actor" / "state.sqlite3"))
        self.path = Path(path or default).expanduser()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.init()

    def init(self) -> None:
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, goal TEXT NOT NULL, state TEXT NOT NULL,
            astra_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('astra','luna')),
            name TEXT NOT NULL, workspace TEXT, tmux_session TEXT, tmux_socket TEXT,
            command TEXT, bootstrap_sent INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
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
            body TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT, read_at TEXT,
            delivery_claim TEXT, delivery_claimed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS idempotency (
            sender_id TEXT NOT NULL, key TEXT NOT NULL, message_id INTEGER NOT NULL REFERENCES messages(id),
            PRIMARY KEY(sender_id, key)
        );
        CREATE INDEX IF NOT EXISTS messages_pending ON messages(recipient_id, delivered_at, id);
        """)
        self._ensure_column("runs", "astra_id", "TEXT")
        self._ensure_column("agents", "tmux_socket", "TEXT")
        self._ensure_column("agents", "bootstrap_sent", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("messages", "delivery_claim", "TEXT")
        self._ensure_column("messages", "delivery_claimed_at", "TEXT")
        self.db.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self.db.close()

    def agent(self, agent_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not row:
            raise ValueError(f"unknown agent: {agent_id}")
        return row

    def find_agent_destination(self, kind: str, tmux_session: str | None, tmux_socket: str | None) -> sqlite3.Row | None:
        if not tmux_session:
            return None
        return self.db.execute("""SELECT * FROM agents WHERE kind=? AND tmux_session=?
            AND COALESCE(tmux_socket, '')=COALESCE(?, '') ORDER BY created_at LIMIT 1""",
                              (kind, tmux_session, tmux_socket)).fetchone()

    def _insert_agent(self, kind: str, name: str, *, workspace: str | None,
                      tmux_session: str | None, tmux_socket: str | None,
                      command: str | None, agent_id: str | None = None) -> str:
        agent_id = agent_id or new_id()
        self.db.execute("""INSERT INTO agents
            (id,kind,name,workspace,tmux_session,tmux_socket,command,bootstrap_sent,created_at,active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 1)""",
                        (agent_id, kind, name, workspace, tmux_session, tmux_socket, command, now()))
        return agent_id

    def register_agent(self, kind: str, name: str, *, workspace: str | None = None,
                       tmux_session: str | None = None, tmux_socket: str | None = None,
                       command: str | None = None, agent_id: str | None = None) -> str:
        identity = self._insert_agent(kind, name, workspace=workspace, tmux_session=tmux_session,
                                      tmux_socket=tmux_socket, command=command, agent_id=agent_id)
        self.db.commit()
        return identity

    def _destination(self, agent_id: str, *, workspace: str | None, tmux_session: str | None,
                     tmux_socket: str | None, command: str | None) -> None:
        row = self.agent(agent_id)
        values = {"workspace": workspace, "tmux_session": tmux_session,
                  "tmux_socket": tmux_socket, "command": command}
        for field, value in values.items():
            if value and row[field] and value != row[field]:
                raise ValueError(f"run identity already has a different destination {field}")
        updates = {field: value for field, value in values.items() if value and not row[field]}
        if updates:
            self.db.execute("UPDATE agents SET " + ", ".join(f"{field}=?" for field in updates)
                            + " WHERE id=?", (*updates.values(), agent_id))

    def run_astra(self, run_id: str) -> sqlite3.Row | None:
        return self.db.execute("""SELECT a.* FROM runs r JOIN agents a ON a.id=r.astra_id
            WHERE r.id=?""", (run_id,)).fetchone()

    def existing_luna(self, run_id: str, name: str) -> sqlite3.Row | None:
        return self.db.execute("""SELECT l.* FROM pairs p JOIN agents l ON l.id=p.luna_id
            WHERE p.run_id=? AND l.name=?""", (run_id, name)).fetchone()

    def bootstrap_sent(self, agent_id: str) -> bool:
        return bool(self.agent(agent_id)["bootstrap_sent"])

    def mark_bootstrap_sent(self, agent_id: str) -> None:
        self.db.execute("UPDATE agents SET bootstrap_sent=1 WHERE id=?", (agent_id,))
        self.db.commit()

    def _thread(self, run_id: str, astra_id: str, luna_id: str) -> str:
        existing = self.db.execute("SELECT id FROM threads WHERE run_id=? AND luna_id=?", (run_id, luna_id)).fetchone()
        if existing:
            return existing["id"]
        thread_id = new_id()
        self.db.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?)", (thread_id, run_id, astra_id, luna_id, now()))
        return thread_id

    def start_run(self, *, goal: str, run_id: str | None, astra_id: str | None,
                  astra_name: str, astra_workspace: str | None, astra_session: str | None,
                  astra_socket: str | None, astra_command: str | None,
                  luna_name: str, luna_id: str | None, luna_workspace: str,
                  luna_session: str, luna_socket: str, luna_command: str | None) -> dict:
        """Atomically create or reuse the complete run identity."""
        run_id = run_id or new_id()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            run = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run and run["goal"] != goal:
                raise ValueError("run ID already exists with a different goal")
            if not run:
                stamp = now()
                self.db.execute("INSERT INTO runs(id,goal,state,astra_id,created_at,updated_at) VALUES(?,?, 'working', NULL, ?, ?)",
                                (run_id, goal, stamp, stamp))
                run = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

            persisted_astra = run["astra_id"]
            if persisted_astra:
                if astra_id and astra_id != persisted_astra:
                    raise ValueError("run ID is already bound to a different Astra")
                actual_astra = persisted_astra
            else:
                existing_destination = self.find_agent_destination("astra", astra_session, astra_socket)
                actual_astra = astra_id or (existing_destination["id"] if existing_destination else None)
                if not actual_astra:
                    actual_astra = self._insert_agent("astra", astra_name,
                        workspace=astra_workspace, tmux_session=astra_session,
                        tmux_socket=astra_socket, command=astra_command)
                self.db.execute("UPDATE runs SET astra_id=?, updated_at=? WHERE id=?", (actual_astra, now(), run_id))
            self.agent(actual_astra)
            self._destination(actual_astra, workspace=astra_workspace, tmux_session=astra_session,
                              tmux_socket=astra_socket, command=astra_command)

            existing = self.db.execute("""SELECT l.* FROM pairs p JOIN agents l ON l.id=p.luna_id
                WHERE p.run_id=? AND (l.name=? OR l.id=?)""", (run_id, luna_name, luna_id or "")).fetchone()
            if existing:
                actual_luna = existing["id"]
                if luna_id and luna_id != actual_luna:
                    raise ValueError("run stream name and Luna ID refer to different actors")
                self._destination(actual_luna, workspace=luna_workspace, tmux_session=luna_session,
                                  tmux_socket=luna_socket, command=luna_command)
            else:
                actual_luna = luna_id or new_id()
                if luna_id:
                    existing_agent = self.db.execute("SELECT id FROM agents WHERE id=?", (luna_id,)).fetchone()
                    if existing_agent:
                        raise ValueError("Luna ID exists but is not part of this run")
                self._insert_agent("luna", luna_name, workspace=luna_workspace, tmux_session=luna_session,
                                   tmux_socket=luna_socket, command=luna_command, agent_id=actual_luna)
            pair = self.db.execute("SELECT * FROM pairs WHERE luna_id=?", (actual_luna,)).fetchone()
            if pair and (pair["run_id"] != run_id or pair["astra_id"] != actual_astra):
                raise ValueError("Luna is already bound to another Astra/run")
            self.db.execute("INSERT OR IGNORE INTO pairs(luna_id,astra_id,run_id) VALUES(?,?,?)",
                            (actual_luna, actual_astra, run_id))
            thread_id = self._thread(run_id, actual_astra, actual_luna)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {"run_id": run_id, "astra_id": actual_astra, "luna_id": actual_luna, "thread_id": thread_id}

    def thread_for_luna(self, luna_id: str, thread_id: str | None = None) -> sqlite3.Row:
        if thread_id:
            row = self.db.execute("SELECT * FROM threads WHERE id=? AND luna_id=?", (thread_id, luna_id)).fetchone()
        else:
            row = self.db.execute("SELECT * FROM threads WHERE luna_id=? ORDER BY created_at DESC LIMIT 1", (luna_id,)).fetchone()
        if not row:
            raise ValueError("Luna has no matching Astra thread")
        return row

    def send(self, thread_id: str, sender_id: str, body: str, key: str | None = None) -> dict:
        if not body.strip():
            raise ValueError("message body cannot be empty")
        thread = self.db.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
        if not thread:
            raise ValueError(f"unknown thread: {thread_id}")
        if sender_id not in {thread["astra_id"], thread["luna_id"]}:
            raise ValueError("sender is not a participant in this thread")
        recipient = thread["luna_id"] if sender_id == thread["astra_id"] else thread["astra_id"]
        if key:
            old = self.db.execute("""SELECT m.* FROM idempotency i JOIN messages m ON m.id=i.message_id
                WHERE i.sender_id=? AND i.key=?""", (sender_id, key)).fetchone()
            if old:
                if old["thread_id"] != thread_id or old["body"] != body:
                    raise ValueError("idempotency conflict: key already names different message text or thread")
                return dict(old)
        cur = self.db.execute("""INSERT INTO messages
            (thread_id,sender_id,recipient_id,body,created_at) VALUES(?,?,?,?,?)""",
                              (thread_id, sender_id, recipient, body, now()))
        message_id = cur.lastrowid
        if key:
            self.db.execute("INSERT INTO idempotency VALUES (?, ?, ?)", (sender_id, key, message_id))
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())

    def claim_pending(self, worker: str, limit: int = 100, stale_after: int = 900) -> list[sqlite3.Row]:
        cutoff = datetime.fromtimestamp(datetime.now().timestamp() - stale_after, timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            rows = self.db.execute("""SELECT * FROM messages WHERE delivered_at IS NULL
                AND (delivery_claim IS NULL OR delivery_claimed_at<?) ORDER BY id LIMIT ?""", (cutoff, limit)).fetchall()
            stamp = now()
            for row in rows:
                self.db.execute("UPDATE messages SET delivery_claim=?, delivery_claimed_at=? WHERE id=?",
                                (worker, stamp, row["id"]))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return rows

    def release_claim(self, message_id: int, worker: str) -> None:
        self.db.execute("UPDATE messages SET delivery_claim=NULL, delivery_claimed_at=NULL WHERE id=? AND delivery_claim=?",
                        (message_id, worker))
        self.db.commit()

    def mark_delivered(self, message_id: int, worker: str) -> None:
        self.db.execute("""UPDATE messages SET delivered_at=?, delivery_claim=NULL, delivery_claimed_at=NULL
            WHERE id=? AND delivered_at IS NULL AND delivery_claim=?""", (now(), message_id, worker))
        self.db.commit()

    def messages(self, thread_id: str) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY id", (thread_id,))]

    def status(self, run_id: str) -> dict:
        run = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise ValueError(f"unknown run: {run_id}")
        streams = self.db.execute("""SELECT p.*, a.name AS astra_name, a.tmux_session AS astra_session,
            l.name AS luna_name, l.tmux_session AS luna_session, t.id AS thread_id
            FROM pairs p JOIN agents a ON a.id=p.astra_id JOIN agents l ON l.id=p.luna_id
            LEFT JOIN threads t ON t.luna_id=p.luna_id WHERE p.run_id=?""", (run_id,)).fetchall()
        return {"run": dict(run), "streams": [dict(row) for row in streams]}

    def set_run_state(self, run_id: str, state: str) -> None:
        self.db.execute("UPDATE runs SET state=?, updated_at=? WHERE id=?", (state, now(), run_id))
        self.db.commit()
