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
        self.path = Path(path or default).expanduser().resolve()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.init()

    def init(self) -> None:
        had_legacy_marker = any(row["name"] == "legacy" for row in self.db.execute("PRAGMA table_info(messages)"))
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, goal TEXT NOT NULL, state TEXT NOT NULL,
            astra_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('astra','luna')),
            name TEXT NOT NULL, workspace TEXT, tmux_session TEXT, tmux_socket TEXT,
            command TEXT, bootstrap_sent INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            codex_thread_id TEXT, codex_home TEXT, model TEXT
        );
        CREATE TABLE IF NOT EXISTS pairs (
            luna_id TEXT PRIMARY KEY REFERENCES agents(id),
            astra_id TEXT NOT NULL REFERENCES agents(id),
            run_id TEXT NOT NULL REFERENCES runs(id),
            handoff TEXT, approved INTEGER NOT NULL DEFAULT 0,
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
            delivery_claim TEXT, delivery_claimed_at TEXT,
            delivery_state TEXT NOT NULL DEFAULT 'pending', delivery_worker TEXT,
            delivery_error TEXT
        );
        CREATE TABLE IF NOT EXISTS idempotency (
            sender_id TEXT NOT NULL, key TEXT NOT NULL, message_id INTEGER NOT NULL REFERENCES messages(id), reply_to INTEGER,
            PRIMARY KEY(sender_id, key)
        );
        CREATE TABLE IF NOT EXISTS inbox_notifications (
            recipient_id TEXT PRIMARY KEY REFERENCES agents(id), notification_id TEXT NOT NULL,
            state TEXT NOT NULL, token TEXT, claimed_at TEXT, created_at TEXT NOT NULL, error TEXT
        );
        CREATE INDEX IF NOT EXISTS messages_pending ON messages(recipient_id, delivered_at, id);
        """)
        self._ensure_column("runs", "astra_id", "TEXT")
        self._ensure_column("agents", "tmux_socket", "TEXT")
        self._ensure_column("agents", "bootstrap_sent", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("agents", "codex_thread_id", "TEXT")
        self._ensure_column("agents", "codex_home", "TEXT")
        self._ensure_column("agents", "model", "TEXT")
        self._ensure_column("agents", "reasoning_effort", "TEXT")
        self._ensure_column("pairs", "handoff", "TEXT")
        self._ensure_column("pairs", "approved", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("messages", "delivery_claim", "TEXT")
        self._ensure_column("messages", "delivery_claimed_at", "TEXT")
        self._ensure_column("messages", "delivery_state", "TEXT NOT NULL DEFAULT 'pending'")
        self._ensure_column("messages", "delivery_worker", "TEXT")
        self._ensure_column("messages", "delivery_error", "TEXT")
        self._ensure_column("messages", "handled_at", "TEXT")
        self._ensure_column("messages", "inbox_token", "TEXT")
        self._ensure_column("messages", "inbox_claimed_at", "TEXT")
        self._ensure_column("messages", "legacy", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("idempotency", "reply_to", "INTEGER")
        self.db.execute("UPDATE messages SET delivery_state='delivered' WHERE delivered_at IS NOT NULL AND delivery_state='pending'")
        if not had_legacy_marker:
            # Old never-dispatched work stays eligible; accepted history is not replayed.
            self.db.execute("UPDATE messages SET delivery_state='uncertain',delivery_error=COALESCE(delivery_error,'dispatch interrupted during inbox migration'),delivery_claim=NULL,delivery_claimed_at=NULL,delivery_worker=NULL WHERE delivered_at IS NULL AND delivery_state='dispatching'")
            self.db.execute("UPDATE messages SET delivery_state='pending',delivery_claim=NULL,delivery_claimed_at=NULL,delivery_worker=NULL WHERE delivered_at IS NULL AND delivery_state='claimed'")
            self.db.execute("UPDATE messages SET legacy=0 WHERE delivered_at IS NULL AND delivery_state='pending'")
            recipients = self.db.execute("SELECT DISTINCT m.recipient_id FROM messages m JOIN agents a ON a.id=m.recipient_id WHERE m.legacy=0 AND m.handled_at IS NULL AND m.delivered_at IS NULL").fetchall()
            for recipient in recipients:
                self.db.execute("INSERT OR IGNORE INTO inbox_notifications(recipient_id,notification_id,state,created_at) VALUES(?,?,'pending',?)", (recipient[0],new_id(),now()))
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

    def find_codex_agent(self, kind: str, thread_id: str, codex_home: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM agents WHERE kind=? AND codex_thread_id=? AND codex_home=? ORDER BY created_at LIMIT 1",
                               (kind, thread_id, codex_home)).fetchone()

    def _insert_agent(self, kind: str, name: str, *, workspace: str | None,
                      tmux_session: str | None, tmux_socket: str | None,
                      command: str | None, agent_id: str | None = None,
                      codex_thread_id: str | None = None, codex_home: str | None = None,
                      model: str | None = None) -> str:
        agent_id = agent_id or new_id()
        self.db.execute("""INSERT INTO agents
            (id,kind,name,workspace,tmux_session,tmux_socket,command,bootstrap_sent,created_at,active,codex_thread_id,codex_home,model)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 1, ?, ?, ?)""",
                        (agent_id, kind, name, workspace, tmux_session, tmux_socket, command, now(), codex_thread_id, codex_home, model))
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
                  luna_session: str, luna_socket: str, luna_command: str | None,
                  astra_thread_id: str | None = None, codex_home: str | None = None,
                  luna_model: str | None = None, handoff: str | None = None) -> dict:
        """Atomically create or reuse the complete run identity."""
        if astra_thread_id:
            try:
                astra_thread_id = str(uuid.UUID(astra_thread_id))
            except (ValueError, AttributeError):
                raise ValueError("Astra Codex thread ID must be a UUID")
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
                existing_destination = (self.find_codex_agent("astra", astra_thread_id, codex_home)
                                         if astra_thread_id and codex_home else
                                         self.find_agent_destination("astra", astra_session, astra_socket))
                actual_astra = astra_id or (existing_destination["id"] if existing_destination else None)
                if not actual_astra:
                    actual_astra = self._insert_agent("astra", astra_name,
                        workspace=astra_workspace, tmux_session=astra_session,
                        tmux_socket=astra_socket, command=astra_command,
                        codex_thread_id=astra_thread_id, codex_home=codex_home)
                self.db.execute("UPDATE runs SET astra_id=?, updated_at=? WHERE id=?", (actual_astra, now(), run_id))
            self.agent(actual_astra)
            self._destination(actual_astra, workspace=astra_workspace, tmux_session=astra_session,
                              tmux_socket=astra_socket, command=astra_command)
            self._codex_identity(actual_astra, astra_thread_id, codex_home, None)

            existing = self.db.execute("""SELECT l.* FROM pairs p JOIN agents l ON l.id=p.luna_id
                WHERE p.run_id=? AND (l.name=? OR l.id=?)""", (run_id, luna_name, luna_id or "")).fetchone()
            if run["state"] == "approved" and not existing:
                raise ValueError("approved run cannot accept additional actors")
            if existing:
                actual_luna = existing["id"]
                if luna_id and luna_id != actual_luna:
                    raise ValueError("run stream name and Luna ID refer to different actors")
                self._destination(actual_luna, workspace=luna_workspace, tmux_session=luna_session,
                                  tmux_socket=luna_socket, command=luna_command)
                self._codex_identity(actual_luna, None, codex_home, luna_model)
                old_handoff = self.db.execute("SELECT handoff FROM pairs WHERE luna_id=?", (actual_luna,)).fetchone()
                if old_handoff and handoff is not None and old_handoff["handoff"] not in (None, handoff):
                    raise ValueError("run stream already has a different handoff")
            else:
                actual_luna = luna_id or new_id()
                if luna_id:
                    existing_agent = self.db.execute("SELECT id FROM agents WHERE id=?", (luna_id,)).fetchone()
                    if existing_agent:
                        raise ValueError("Luna ID exists but is not part of this run")
                self._insert_agent("luna", luna_name, workspace=luna_workspace, tmux_session=luna_session,
                                   tmux_socket=luna_socket, command=luna_command, agent_id=actual_luna,
                                   codex_home=codex_home, model=luna_model)
            pair = self.db.execute("SELECT * FROM pairs WHERE luna_id=?", (actual_luna,)).fetchone()
            if pair and (pair["run_id"] != run_id or pair["astra_id"] != actual_astra):
                raise ValueError("Luna is already bound to another Astra/run")
            self.db.execute("INSERT OR IGNORE INTO pairs(luna_id,astra_id,run_id) VALUES(?,?,?)",
                            (actual_luna, actual_astra, run_id))
            self.db.execute("UPDATE pairs SET handoff=COALESCE(handoff, ?) WHERE luna_id=?", (handoff, actual_luna))
            thread_id = self._thread(run_id, actual_astra, actual_luna)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {"run_id": run_id, "astra_id": actual_astra, "luna_id": actual_luna, "thread_id": thread_id}

    def _codex_identity(self, agent_id: str, thread_id: str | None, codex_home: str | None, model: str | None) -> None:
        row = self.agent(agent_id)
        for field, value in (("codex_thread_id", thread_id), ("codex_home", codex_home), ("model", model)):
            if value is not None and row[field] not in (None, value):
                raise ValueError(f"run identity already has a different {field}")
            if value is not None and row[field] is None:
                self.db.execute(f"UPDATE agents SET {field}=? WHERE id=?", (value, agent_id))

    def bind_codex_thread(self, luna_id: str, thread_id: str, codex_home: str) -> None:
        try:
            canonical = str(uuid.UUID(thread_id))
        except (ValueError, AttributeError):
            raise ValueError("Codex thread ID must be a UUID")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            agent = self.agent(luna_id)
            if agent["kind"] != "luna":
                raise ValueError("only Luna agents can bind a Codex thread")
            if agent["codex_home"] not in (None, codex_home):
                raise ValueError("Codex home does not match Luna identity")
            if agent["codex_thread_id"] not in (None, canonical):
                raise ValueError("Luna is already bound to a different Codex thread")
            other = self.db.execute("SELECT id FROM agents WHERE kind='luna' AND codex_thread_id=? AND codex_home=? AND id<>?",
                                    (canonical, codex_home, luna_id)).fetchone()
            if other:
                raise ValueError("Codex thread is already bound to another Luna")
            self.db.execute("UPDATE agents SET codex_thread_id=?,codex_home=? WHERE id=?", (canonical, codex_home, luna_id))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def thread_for_luna(self, luna_id: str, thread_id: str | None = None) -> sqlite3.Row:
        if thread_id:
            row = self.db.execute("SELECT * FROM threads WHERE id=? AND luna_id=?", (thread_id, luna_id)).fetchone()
        else:
            row = self.db.execute("SELECT * FROM threads WHERE luna_id=? ORDER BY created_at DESC LIMIT 1", (luna_id,)).fetchone()
        if not row:
            raise ValueError("Luna has no matching Astra thread")
        return row

    def send(self, thread_id: str, sender_id: str, body: str, key: str | None = None, reply_to: int | None = None) -> dict:
        if not body.strip():
            raise ValueError("message body cannot be empty")
        self.db.execute("BEGIN IMMEDIATE")
        thread = self.db.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
        if not thread:
            self.db.rollback()
            raise ValueError(f"unknown thread: {thread_id}")
        if sender_id not in {thread["astra_id"], thread["luna_id"]}:
            self.db.rollback()
            raise ValueError("sender is not a participant in this thread")
        recipient = thread["luna_id"] if sender_id == thread["astra_id"] else thread["astra_id"]
        if key:
            old = self.db.execute("""SELECT m.*,i.reply_to FROM idempotency i JOIN messages m ON m.id=i.message_id
                WHERE i.sender_id=? AND i.key=?""", (sender_id, key)).fetchone()
            if old:
                if old["thread_id"] != thread_id or old["body"] != body or old["reply_to"] != reply_to:
                    self.db.rollback()
                    raise ValueError("idempotency conflict: key already names different message text or thread")
                self.db.commit()
                return dict(old)
        pair = self.db.execute("SELECT approved FROM pairs WHERE luna_id=?", (thread["luna_id"],)).fetchone()
        if pair and pair["approved"]:
            self.db.rollback()
            raise ValueError("run stream is approved; ordinary messages are closed")
        if reply_to is not None:
            target = self.db.execute("SELECT * FROM messages WHERE id=?", (reply_to,)).fetchone()
            if not target or target["thread_id"] != thread_id or target["recipient_id"] != sender_id or target["handled_at"] is not None:
                self.db.rollback()
                raise ValueError("reply target must be an incoming message in this thread")
        cur = self.db.execute("""INSERT INTO messages
            (thread_id,sender_id,recipient_id,body,created_at,legacy) VALUES(?,?,?,?,?,0)""",
                              (thread_id, sender_id, recipient, body, now()))
        message_id = cur.lastrowid
        if key:
            self.db.execute("INSERT INTO idempotency(sender_id,key,message_id,reply_to) VALUES (?, ?, ?, ?)", (sender_id, key, message_id, reply_to))
        if reply_to is not None:
            self.db.execute("UPDATE messages SET handled_at=?,inbox_token=NULL,inbox_claimed_at=NULL WHERE id=?", (now(), reply_to))
            self._refresh_notification(sender_id)
        self._refresh_notification(recipient)
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())

    def _refresh_notification(self, recipient_id: str) -> None:
        pending = self.db.execute("SELECT 1 FROM messages WHERE recipient_id=? AND legacy=0 AND handled_at IS NULL AND inbox_token IS NULL LIMIT 1", (recipient_id,)).fetchone()
        notification = self.db.execute("SELECT * FROM inbox_notifications WHERE recipient_id=?", (recipient_id,)).fetchone()
        if not pending:
            self.db.execute("DELETE FROM inbox_notifications WHERE recipient_id=?", (recipient_id,))
        elif not notification:
            self.db.execute("INSERT INTO inbox_notifications(recipient_id,notification_id,state,created_at) VALUES(?,?,'pending',?) ON CONFLICT(recipient_id) DO UPDATE SET notification_id=excluded.notification_id,state='pending',token=NULL,claimed_at=NULL,created_at=excluded.created_at,error=NULL",
                            (recipient_id, new_id(), now()))

    def _rearm_sent_notification(self, recipient_id: str) -> None:
        pending = self.db.execute("SELECT 1 FROM messages WHERE recipient_id=? AND legacy=0 AND handled_at IS NULL AND inbox_token IS NULL LIMIT 1", (recipient_id,)).fetchone()
        if pending:
            self.db.execute("UPDATE inbox_notifications SET notification_id=?,state='pending',created_at=?,claimed_at=NULL,error=NULL WHERE recipient_id=? AND state='sent'", (new_id(),now(),recipient_id))

    def claim_inbox(self, recipient_id: str, limit: int = 20, message_id: int | None = None, lease_seconds: int = 900) -> dict:
        if limit < 1 or limit > 100:
            raise ValueError("inbox limit must be between 1 and 100")
        stamp = now(); token = new_id()
        cutoff = datetime.fromtimestamp(datetime.now().timestamp() - lease_seconds, timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            # Expired claims become available and refresh the coalesced wakeup.
            expired = self.db.execute("UPDATE messages SET inbox_token=NULL,inbox_claimed_at=NULL WHERE recipient_id=? AND handled_at IS NULL AND inbox_claimed_at<?", (recipient_id, cutoff)).rowcount
            self._refresh_notification(recipient_id)
            if expired:
                self._rearm_sent_notification(recipient_id)
            if message_id is not None:
                rows = self.db.execute("SELECT * FROM messages WHERE id=? AND recipient_id=? AND handled_at IS NULL AND inbox_token IS NULL", (message_id,recipient_id)).fetchall()
            else:
                rows = self.db.execute("SELECT * FROM messages WHERE recipient_id=? AND legacy=0 AND handled_at IS NULL AND inbox_token IS NULL ORDER BY id LIMIT ?", (recipient_id,limit)).fetchall()
            for row in rows:
                self.db.execute("UPDATE messages SET inbox_token=?,inbox_claimed_at=? WHERE id=?", (token,stamp,row['id']))
            self._refresh_notification(recipient_id)
            self.db.commit()
            return {"token": token if rows else None, "messages": [dict(r) for r in rows]}
        except Exception:
            self.db.rollback(); raise

    def acknowledge(self, recipient_id: str, token: str, ids: list[int]) -> int:
        if not ids:
            raise ValueError("provide at least one message ID")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for mid in ids:
                cur = self.db.execute("UPDATE messages SET handled_at=?,inbox_token=NULL,inbox_claimed_at=NULL WHERE id=? AND recipient_id=? AND inbox_token=? AND handled_at IS NULL", (now(),mid,recipient_id,token))
                if cur.rowcount != 1:
                    raise ValueError(f"message {mid} is not claimed by this inbox token")
            self._refresh_notification(recipient_id)
            pending = self.db.execute("SELECT 1 FROM messages WHERE recipient_id=? AND legacy=0 AND handled_at IS NULL AND inbox_token IS NULL LIMIT 1", (recipient_id,)).fetchone()
            if pending:
                self.db.execute("UPDATE inbox_notifications SET state='pending',notification_id=?,created_at=?,error=NULL WHERE recipient_id=? AND state='sent'", (new_id(), now(), recipient_id))
            self.db.commit(); return len(ids)
        except Exception:
            self.db.rollback(); raise

    def notification_batch(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.db.execute("SELECT n.*,a.codex_thread_id,a.codex_home,a.workspace,a.kind,a.tmux_session,a.tmux_socket FROM inbox_notifications n JOIN agents a ON a.id=n.recipient_id WHERE n.state='pending' ORDER BY n.created_at LIMIT ?", (limit,)).fetchall()

    def notification_dispatching(self, recipient_id: str, notification_id: str) -> bool:
        cur = self.db.execute("UPDATE inbox_notifications SET state='dispatching',claimed_at=? WHERE recipient_id=? AND notification_id=? AND state='pending'", (now(),recipient_id,notification_id))
        self.db.commit()
        return cur.rowcount == 1

    def notification_result(self, recipient_id: str, notification_id: str, state: str, error: str | None = None) -> bool:
        cur = self.db.execute("UPDATE inbox_notifications SET state=?,error=?,claimed_at=NULL WHERE recipient_id=? AND notification_id=? AND state='dispatching'", (state,error,recipient_id,notification_id)); self.db.commit()
        return cur.rowcount == 1

    def notification_recover(self, stale_after: int = 900) -> None:
        cutoff = datetime.fromtimestamp(datetime.now().timestamp() - stale_after, timezone.utc).isoformat()
        self.db.execute("UPDATE inbox_notifications SET state='uncertain',error=COALESCE(error,'dispatch lease expired'),claimed_at=NULL WHERE state='dispatching' AND claimed_at<?", (cutoff,)); self.db.commit()

    def resolve_notification(self, recipient_id: str, retry: bool) -> None:
        state = 'pending' if retry else 'sent'
        cur = self.db.execute("UPDATE inbox_notifications SET state=?,error=NULL,created_at=? WHERE recipient_id=? AND state='uncertain'", (state,now(),recipient_id))
        if cur.rowcount != 1:
            raise ValueError('notification is not awaiting uncertain-delivery resolution')
        self.db.commit()

    def recover_inbox_claims(self, lease_seconds: int = 900) -> int:
        cutoff = datetime.fromtimestamp(datetime.now().timestamp() - lease_seconds, timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            recipients = [r[0] for r in self.db.execute("SELECT DISTINCT recipient_id FROM messages WHERE inbox_token IS NOT NULL AND inbox_claimed_at<?", (cutoff,))]
            cur = self.db.execute("UPDATE messages SET inbox_token=NULL,inbox_claimed_at=NULL WHERE inbox_token IS NOT NULL AND inbox_claimed_at<?", (cutoff,))
            for recipient in recipients:
                self._refresh_notification(recipient)
                self._rearm_sent_notification(recipient)
            self.db.commit(); return cur.rowcount
        except Exception:
            self.db.rollback(); raise

    def approve(self, thread_id: str, astra_id: str, body: str, key: str, reply_to: int | None = None) -> dict:
        if not body.strip():
            raise ValueError("message body cannot be empty")
        body = "ASTRA_APPROVED\n" + body
        self.db.execute("BEGIN IMMEDIATE")
        try:
            thread = self.db.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
            if not thread:
                raise ValueError(f"unknown thread: {thread_id}")
            if astra_id != thread["astra_id"]:
                raise ValueError("only the thread's Astra can approve")
            old = self.db.execute("SELECT m.*,i.reply_to FROM idempotency i JOIN messages m ON m.id=i.message_id WHERE i.sender_id=? AND i.key=?",
                                  (astra_id, key)).fetchone()
            if old:
                if old["thread_id"] != thread_id or old["body"] != body or old["reply_to"] != reply_to:
                    raise ValueError("idempotency conflict: key already names different message text or thread")
                self.db.commit()
                return dict(old)
            pair = self.db.execute("SELECT approved FROM pairs WHERE luna_id=?", (thread["luna_id"],)).fetchone()
            if pair and pair["approved"]:
                raise ValueError("stream is already approved; approval retry must use its original idempotency key")
            if reply_to is not None:
                target = self.db.execute("SELECT * FROM messages WHERE id=?", (reply_to,)).fetchone()
                if not target or target["thread_id"] != thread_id or target["recipient_id"] != astra_id or target["handled_at"] is not None:
                    raise ValueError("reply target must be an unhandled incoming message in this thread")
            cur = self.db.execute("INSERT INTO messages(thread_id,sender_id,recipient_id,body,created_at,legacy) VALUES(?,?,?,?,?,0)",
                                  (thread_id, astra_id, thread["luna_id"], body, now()))
            message_id = cur.lastrowid
            self.db.execute("INSERT INTO idempotency(sender_id,key,message_id,reply_to) VALUES(?,?,?,?)", (astra_id, key, message_id, reply_to))
            if reply_to is not None:
                self.db.execute("UPDATE messages SET handled_at=?,inbox_token=NULL,inbox_claimed_at=NULL WHERE id=?", (now(), reply_to))
                self._refresh_notification(astra_id)
            self._refresh_notification(thread["luna_id"])
            self.db.execute("UPDATE pairs SET approved=1 WHERE luna_id=?", (thread["luna_id"],))
            remaining = self.db.execute("SELECT COUNT(*) FROM pairs WHERE run_id=? AND approved=0", (thread["run_id"],)).fetchone()[0]
            if not remaining:
                self.db.execute("UPDATE runs SET state='approved',updated_at=? WHERE id=?", (now(), thread["run_id"]))
            result = dict(self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    def claim_pending(self, worker: str, limit: int = 100, stale_after: int = 900) -> list[sqlite3.Row]:
        cutoff = datetime.fromtimestamp(datetime.now().timestamp() - stale_after, timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            # A timed out dispatch is ambiguous: quarantine it for reconciliation. Only
            # a claim that never entered dispatch can safely return to the queue.
            self.db.execute("""UPDATE messages SET delivery_state='uncertain',delivery_error=COALESCE(delivery_error,'dispatch lease expired')
                WHERE delivery_state='dispatching' AND delivery_claimed_at<? AND delivered_at IS NULL""", (cutoff,))
            self.db.execute("""UPDATE messages SET delivery_state='pending',delivery_claim=NULL,delivery_claimed_at=NULL,delivery_worker=NULL
                WHERE delivery_state='claimed' AND delivery_claimed_at<? AND delivered_at IS NULL""", (cutoff,))
            rows = self.db.execute("""SELECT * FROM messages WHERE delivered_at IS NULL AND delivery_state='pending'
                ORDER BY id LIMIT ?""", (limit,)).fetchall()
            stamp = now()
            for row in rows:
                self.db.execute("UPDATE messages SET delivery_claim=?, delivery_claimed_at=?,delivery_worker=?,delivery_state='claimed' WHERE id=?",
                                (worker, stamp, worker, row["id"]))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return rows

    def mark_dispatching(self, message_id: int, worker: str) -> None:
        cur = self.db.execute("UPDATE messages SET delivery_state='dispatching',delivery_worker=? WHERE id=? AND delivery_claim=? AND delivery_state='claimed' AND delivered_at IS NULL",
                              (worker, message_id, worker))
        if cur.rowcount != 1:
            raise ValueError("message is not claimed by this worker")
        self.db.commit()

    def mark_uncertain(self, message_id: int, worker: str, error: str | None = None) -> None:
        cur = self.db.execute("UPDATE messages SET delivery_state='uncertain',delivery_error=?,delivery_worker=? WHERE id=? AND delivery_claim=? AND delivered_at IS NULL",
                              (error, worker, message_id, worker))
        if cur.rowcount != 1:
            raise ValueError("message is not claimed by this worker")
        self.db.commit()

    def release_claim(self, message_id: int, worker: str, error: str | None = None) -> None:
        cur = self.db.execute("UPDATE messages SET delivery_claim=NULL,delivery_claimed_at=NULL,delivery_worker=NULL,delivery_state='pending',delivery_error=? WHERE id=? AND delivery_claim=? AND delivery_state='claimed'",
                              (error, message_id, worker))
        if cur.rowcount != 1:
            raise ValueError("only a pre-dispatch claim can be released")
        self.db.commit()

    def reset_undispatched(self, message_id: int, worker: str, error: str | None = None) -> None:
        """Release a dispatching claim only when the transport guarantees no child started.

        Call only for a preflight/exec-start failure (for example DeliveryUnavailable).
        Once the queue child starts, use mark_uncertain when delivery cannot be confirmed.
        """
        cur = self.db.execute("""UPDATE messages SET delivery_claim=NULL,delivery_claimed_at=NULL,
            delivery_worker=NULL,delivery_state='pending',delivery_error=?
            WHERE id=? AND delivery_claim=? AND delivery_state='dispatching' AND delivered_at IS NULL""",
                              (error, message_id, worker))
        if cur.rowcount != 1:
            raise ValueError("message is not dispatching for this worker")
        self.db.commit()

    def mark_delivered(self, message_id: int, worker: str) -> None:
        self.db.execute("""UPDATE messages SET delivered_at=?, delivery_claim=NULL, delivery_claimed_at=NULL,
            delivery_worker=NULL,delivery_state='delivered',delivery_error=NULL
            WHERE id=? AND delivered_at IS NULL AND delivery_claim=?""", (now(), message_id, worker))
        self.db.commit()

    def resolve_delivery(self, message_id: int, retry: bool) -> None:
        state = 'pending' if retry else 'delivered'
        delivered = None if retry else now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            message = self.db.execute("SELECT recipient_id FROM messages WHERE id=? AND delivery_state='uncertain' AND delivered_at IS NULL", (message_id,)).fetchone()
            cur = self.db.execute("UPDATE messages SET delivery_state=?,delivery_claim=NULL,delivery_claimed_at=NULL,delivery_worker=NULL,delivery_error=NULL,delivered_at=?,legacy=CASE WHEN ? THEN 0 ELSE legacy END WHERE id=? AND delivery_state='uncertain' AND delivered_at IS NULL",
                                  (state, delivered, int(retry), message_id))
            if cur.rowcount != 1:
                raise ValueError("message is not awaiting uncertain-delivery resolution")
            if retry and message:
                self._refresh_notification(message['recipient_id'])
            self.db.commit()
        except Exception:
            self.db.rollback(); raise

    def messages(self, thread_id: str) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY id", (thread_id,))]

    def status(self, run_id: str) -> dict:
        run = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise ValueError(f"unknown run: {run_id}")
        astra_id = run["astra_id"] or self.db.execute("SELECT astra_id FROM pairs WHERE run_id=? LIMIT 1", (run_id,)).fetchone()[0]
        participant_ids = [astra_id] + [r[0] for r in self.db.execute("SELECT luna_id FROM pairs WHERE run_id=? ORDER BY luna_id", (run_id,))]
        recipients = []
        for recipient_id in participant_ids:
            agent = self.agent(recipient_id)
            counts = self.db.execute("""SELECT
                SUM(CASE WHEN m.handled_at IS NULL AND (m.legacy=0 OR m.delivered_at IS NULL) THEN 1 ELSE 0 END) AS unhandled,
                SUM(CASE WHEN m.handled_at IS NULL AND m.legacy=0 AND m.inbox_token IS NULL THEN 1 ELSE 0 END) AS available,
                SUM(CASE WHEN m.handled_at IS NULL AND m.inbox_token IS NOT NULL THEN 1 ELSE 0 END) AS claimed,
                SUM(CASE WHEN m.handled_at IS NULL AND m.delivery_state='uncertain' AND m.delivered_at IS NULL THEN 1 ELSE 0 END) AS uncertain_messages
                FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.run_id=? AND m.recipient_id=?""", (run_id,recipient_id)).fetchone()
            notification = self.db.execute("SELECT state,error FROM inbox_notifications WHERE recipient_id=?", (recipient_id,)).fetchone()
            recipients.append({"recipient_id": recipient_id, "kind": agent["kind"], "name": agent["name"],
                "unhandled_messages": counts["unhandled"] or 0, "available_messages": counts["available"] or 0,
                "claimed_messages": counts["claimed"] or 0, "uncertain_messages": counts["uncertain_messages"] or 0,
                "notification_state": notification["state"] if notification else None,
                "notification_error": notification["error"] if notification else None})
        streams = self.db.execute("""SELECT p.*, a.name AS astra_name, a.tmux_session AS astra_session,
            a.codex_thread_id AS astra_codex_thread_id, a.codex_home AS astra_codex_home,
            l.name AS luna_name, l.tmux_session AS luna_session, l.codex_thread_id AS luna_codex_thread_id,
            l.codex_home AS luna_codex_home, l.model AS luna_model,
            l.reasoning_effort AS luna_launch_effort, t.id AS thread_id,
            (SELECT COUNT(*) FROM messages m WHERE m.thread_id=t.id AND m.handled_at IS NULL AND (m.legacy=0 OR m.delivered_at IS NULL)) AS pending_messages,
            (SELECT COUNT(*) FROM messages m WHERE m.thread_id=t.id AND m.handled_at IS NULL AND m.legacy=0 AND m.inbox_token IS NULL) AS pending_available_messages,
            (SELECT COUNT(*) FROM messages m WHERE m.thread_id=t.id AND m.handled_at IS NULL AND m.inbox_token IS NOT NULL) AS pending_claimed_messages,
            (SELECT m.delivery_error FROM messages m WHERE m.thread_id=t.id AND m.delivery_error IS NOT NULL ORDER BY m.id DESC LIMIT 1) AS delivery_error,
            (SELECT n.state FROM inbox_notifications n WHERE n.recipient_id=l.id) AS inbox_notification_state,
            (SELECT n.error FROM inbox_notifications n WHERE n.recipient_id=l.id) AS inbox_notification_error
            FROM pairs p JOIN agents a ON a.id=p.astra_id JOIN agents l ON l.id=p.luna_id
            LEFT JOIN threads t ON t.luna_id=p.luna_id WHERE p.run_id=?""", (run_id,)).fetchall()
        return {"run": dict(run), "recipients": recipients, "streams": [dict(row) for row in streams]}

    def set_run_state(self, run_id: str, state: str) -> None:
        self.db.execute("UPDATE runs SET state=?, updated_at=? WHERE id=?", (state, now(), run_id))
        self.db.commit()
