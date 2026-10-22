"""Bounded SQLite spool for collector outages, with durable loss accounting."""

import base64
from contextlib import closing, contextmanager
import json
import os
import sqlite3
import time
from pathlib import Path

from contracts.content import ContentObject
from contracts.events import EventEnvelope


class DurableSpool:
    def __init__(self, path: str | Path, *, limit_bytes: int, allow_operations: bool = False):
        if type(allow_operations) is not bool:
            raise TypeError("allow_operations must be a boolean")
        self.allow_operations = allow_operations
        if limit_bytes <= 0:
            raise ValueError("positive spool bound required")
        self.path = Path(path)
        self.limit_bytes = limit_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS entries (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "event_json TEXT NOT NULL, content_json TEXT NOT NULL, size_bytes INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
            for key in ("next_producer_sequence", "known_gaps", "reported_gaps"):
                db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES(?,0)", (key,))

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def drain_lock(self):
        """Fence collector delivery of this spool across threads and processes."""
        lock_path = self.path.with_name(self.path.name + ".drain.lock")
        with lock_path.open("a+b") as lock_file:
            deadline = time.monotonic() + 10
            while True:
                try:
                    lock_file.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("spool drain lock unavailable")
                    time.sleep(0.025)
            try:
                yield
            finally:
                lock_file.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _meta(self, key: str) -> int:
        with self._connect() as db:
            return db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0]

    def allocate_sequence(self) -> int:
        with self._connect() as db:
            db.execute("UPDATE metadata SET value=value+1 WHERE key='next_producer_sequence'")
            return db.execute("SELECT value FROM metadata WHERE key='next_producer_sequence'").fetchone()[0]

    def append(self, event: EventEnvelope, content: ContentObject | None = None) -> bool:
        if self.allow_operations and not isinstance(event, EventEnvelope):
            from contracts.event_reader import read_event
            if content is not None:
                raise ValueError("operation content is not supported")
            event = read_event(event.model_dump(mode="json"), allow_candidate=True).event
        event_json = event.model_dump_json()
        if content:
            encoded = content.model_dump(mode="json", exclude={"raw_bytes"})
            encoded["raw_bytes_b64"] = base64.b64encode(content.raw_bytes).decode("ascii")
            content_json = json.dumps(encoded, separators=(",", ":"))
        else:
            content_json = "null"
        size = len(event_json.encode()) + len(content_json.encode())
        with self._connect() as db:
            used = db.execute("SELECT coalesce(sum(size_bytes),0) FROM entries").fetchone()[0]
            if used + size > self.limit_bytes:
                db.execute("UPDATE metadata SET value=value+1 WHERE key='known_gaps'")
                return False
            db.execute("INSERT INTO entries(event_json,content_json,size_bytes) VALUES(?,?,?)",
                       (event_json, content_json, size))
            return True

    def first(self):
        with self._connect() as db:
            row = db.execute("SELECT seq,event_json,content_json FROM entries ORDER BY seq LIMIT 1").fetchone()
        if not row:
            return None
        content = None
        if row[2] != "null":
            encoded = json.loads(row[2])
            if "raw_bytes_b64" in encoded:
                encoded["raw_bytes"] = base64.b64decode(encoded.pop("raw_bytes_b64"),
                                                         validate=True)
            content = ContentObject.model_validate(encoded)
        if self.allow_operations:
            from contracts.event_reader import read_event
            event = read_event(row[1], allow_candidate=True).event
        else:
            event = EventEnvelope.model_validate_json(row[1])
        return row[0], event, content

    def acknowledge(self, seq: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM entries WHERE seq=?", (seq,))

    def pending_count(self) -> int:
        with self._connect() as db:
            return db.execute("SELECT count(*) FROM entries").fetchone()[0]

    def quarantined_count(self) -> int:
        """Read explicit retained conflicts without provisioning recovery storage."""
        with closing(self._connect()) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spool_recoveries'").fetchone():
                return 0
            return db.execute("SELECT count(*) FROM spool_recoveries WHERE action='quarantined'").fetchone()[0]

    def pressure(self) -> float:
        with self._connect() as db:
            used = db.execute("SELECT coalesce(sum(size_bytes),0) FROM entries").fetchone()[0]
        return min(1.0, used / self.limit_bytes)

    def known_gaps(self) -> int:
        return self._meta("known_gaps")

    def unreported_gaps(self) -> int:
        return self.known_gaps() - self._meta("reported_gaps")

    def mark_gaps_reported(self) -> None:
        with self._connect() as db:
            db.execute("UPDATE metadata SET value=(SELECT value FROM metadata WHERE key='known_gaps') "
                       "WHERE key='reported_gaps'")
