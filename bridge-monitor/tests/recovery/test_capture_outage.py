"""Collector reconnect only replays captured events, never model work."""

import uuid

import psycopg

from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from contracts.events import EventType
from database.migrate import migrate
from service.ingest.store import ingest_batch


def test_outage_overflow_reconnect_and_duplicate_replay(runtime_dsn, tmp_path):
    dsn = runtime_dsn
    migrate(dsn)
    producer = f"outage-{uuid.uuid4()}"
    class Collector:
        offline = True

        def ingest_batch(self, identity, events, content):
            if self.offline:
                raise ConnectionError("collector disconnected")
            return ingest_batch(dsn, identity, events, content)

    collector = Collector()
    path = tmp_path / "outage.sqlite"
    spool = DurableSpool(path, limit_bytes=3000)
    emitter = TelemetryEmitter(spool, collector, producer_id=producer, incarnation="before")
    body = "Unicode response π🚀".encode("utf-8")
    assert emitter.emit(EventType.MESSAGE_RECEIVED, call_id="missing-call", body=body,
                        role="assistant", direction="in")
    first_id = spool.first()[1].event_id
    assert not emitter.may_admit_new_work("reject_new_work")
    assert not emitter.emit(EventType.MESSAGE_RECEIVED, call_id="missing-call",
                            body=b"x" * 5000, role="assistant", direction="in")
    assert spool.known_gaps() == 1
    collector.offline = False
    restarted = TelemetryEmitter(DurableSpool(path, limit_bytes=3000), collector,
                                 producer_id=producer, incarnation="after")
    restarted.replay()
    restarted.replay()
    assert spool.pending_count() == 0
    with psycopg.connect(dsn) as conn:
        stored = conn.execute("SELECT event_id FROM events WHERE event_id=%s",
                              (first_id,)).fetchall()
        raw = conn.execute("SELECT raw_bytes FROM content_objects WHERE raw_bytes=%s",
                           (body,)).fetchone()[0]
        gap = conn.execute("SELECT count(*) FROM events WHERE producer_id=%s "
                           "AND event_type='capture.gap'", (producer,)).fetchone()[0]
        jobs = conn.execute("SELECT count(*) FROM runtime_jobs").fetchone()[0]
    assert len(stored) == 1 and bytes(raw) == body
    assert gap == 1
    assert jobs == 0
