"""Durable job identity and bounded in-memory output."""

import sqlite3
import threading
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


TERMINAL_STATES = {"completed", "failed", "unknown"}


class JobJournal:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL,
                model TEXT NOT NULL, state TEXT NOT NULL,
                accepted_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                agent_incarnation TEXT NOT NULL DEFAULT '',
                terminal_json TEXT, last_sequence INTEGER NOT NULL DEFAULT 0)""")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            if "last_sequence" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN last_sequence INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE jobs SET state='unknown', updated_at=? "
                         "WHERE state NOT IN ('completed','failed','unknown')",
                         (self._now(),))

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def accept(self, job_id: str, request_sha256: str, model: str,
               agent_incarnation: str = "") -> tuple[dict, bool]:
        if not job_id or len(request_sha256) != 64 or not model:
            raise ValueError("job identity, request hash and model are required")
        now = self._now()
        with self._connect() as conn:
            inserted = conn.execute(
                "INSERT OR IGNORE INTO jobs(job_id,request_sha256,model,state,accepted_at,"
                "updated_at,agent_incarnation) VALUES(?,?,?,'accepted',?,?,?)",
                (job_id, request_sha256, model, now, now, agent_incarnation))
            row = conn.execute("SELECT request_sha256,model FROM jobs WHERE job_id=?",
                               (job_id,)).fetchone()
            if row != (request_sha256, model):
                raise ValueError("conflicting request for job ID")
            created = inserted.rowcount == 1
        return self.get(job_id), created

    def get(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT job_id,request_sha256,model,state,accepted_at,"
                               "updated_at,agent_incarnation,terminal_json,last_sequence FROM jobs "
                               "WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        names = ("job_id", "request_sha256", "model", "state", "accepted_at",
                 "updated_at", "agent_incarnation", "terminal_json", "last_sequence")
        return dict(zip(names, row))

    def note_output(self, job_id: str, sequence: int) -> None:
        with self._connect() as conn:
            row = conn.execute("UPDATE jobs SET last_sequence=? WHERE job_id=? "
                               "AND last_sequence<? RETURNING job_id",
                               (sequence, job_id, sequence)).fetchone()
            if row is None:
                raise ValueError("output sequence did not advance")

    def set_state(self, job_id: str, state: str, terminal_json: str | None = None) -> None:
        if state not in {"accepted", "ollama_connecting", "streaming", "completed",
                         "failed", "cancel_requested", "unknown"}:
            raise ValueError("invalid job state")
        with self._connect() as conn:
            row = conn.execute("UPDATE jobs SET state=?,updated_at=?,terminal_json=? "
                               "WHERE job_id=? AND state NOT IN ('completed','failed','unknown') "
                               "RETURNING job_id",
                               (state, self._now(), terminal_json, job_id)).fetchone()
            if row is None:
                if conn.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone() is None:
                    raise KeyError(job_id)

    def prune_terminal(self, retention_days: int) -> list[str]:
        if retention_days <= 0:
            raise ValueError("positive receipt retention required")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute("DELETE FROM jobs WHERE state IN ('completed','failed') "
                                "AND updated_at<? RETURNING job_id", (cutoff,)).fetchall()
        return [row[0] for row in rows]


class OutputBuffer:
    def __init__(self, *, max_bytes: int):
        if max_bytes <= 0:
            raise ValueError("positive output buffer required")
        self.max_bytes = max_bytes
        self._items: deque[tuple[int, str, int]] = deque()
        self._bytes = 0
        self._sequence = 0
        self._lock = threading.Lock()

    def append(self, text: str) -> int:
        size = len(text.encode("utf-8"))
        with self._lock:
            self._sequence += 1
            self._items.append((self._sequence, text, size))
            self._bytes += size
            while self._bytes > self.max_bytes:
                _, _, removed = self._items.popleft()
                self._bytes -= removed
            return self._sequence

    def page(self, after_sequence: int) -> dict:
        if after_sequence < 0:
            raise ValueError("nonnegative sequence required")
        with self._lock:
            first = self._items[0][0] if self._items else self._sequence + 1
            return {"sequence": self._sequence,
                    "gap": after_sequence < first - 1,
                    "deltas": [{"sequence": seq, "text": value}
                               for seq, value, _ in self._items if seq > after_sequence]}
