"""Measured synthetic stream workload, without claiming a production SLA."""

import json
import os
import time
import uuid
from pathlib import Path

import psycopg

from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from contracts.events import EventType
from database.migrate import migrate
from service.ingest.store import ingest_batch
from service.queries.snapshot import read_publications


def test_synthetic_capture_and_query_measurements(test_dsn, tmp_path):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, "
                     "producer_sequences RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
    producer = f"load-{uuid.uuid4()}"
    class Collector:
        offline = True

        def ingest_batch(self, identity, events, content):
            if self.offline:
                raise ConnectionError("load outage")
            return ingest_batch(test_dsn, identity, events, content)

    collector = Collector()
    path = tmp_path / "load-spool.sqlite"
    spool = DurableSpool(path, limit_bytes=2_000_000)
    emitter = TelemetryEmitter(spool, collector, producer_id=producer, incarnation="load-1")
    body = ("Synthetic payload π🚀 " * 40).encode("utf-8")
    begin = time.perf_counter()
    for index in range(100):
        assert emitter.emit(EventType.MESSAGE_RECEIVED, call_id=f"synthetic-call-{index}",
                            body=body, role="assistant", direction="in")
    spool_bytes = path.stat().st_size
    pending = spool.pending_count()
    collector.offline = False
    emitter.replay()
    replay_ms = (time.perf_counter() - begin) * 1000
    query_begin = time.perf_counter()
    first_page = read_publications(test_dsn, 0, limit=100)
    query_ms = (time.perf_counter() - query_begin) * 1000
    assert pending == 100 and spool.pending_count() == 0
    assert len(first_page) == 100
    assert all(row["event_type"] == EventType.MESSAGE_RECEIVED.value for row in first_page)
    measurements = {"events": 100, "body_bytes_each": len(body),
                    "event_to_query_ms": round(replay_ms + query_ms, 2),
                    "replay_ms": round(replay_ms, 2),
                    "publication_page_ms": round(query_ms, 2),
                    "spool_file_bytes_at_peak": spool_bytes,
                    "spool_limit_bytes": spool.limit_bytes,
                    "pending_at_peak": pending}
    print("\noperational_load_metrics=" + json.dumps(measurements, sort_keys=True))
    target = os.environ.get("BRIDGE_MONITOR_LOAD_REPORT")
    if target:
        report = Path(target).resolve()
        project = Path(__file__).resolve().parents[2]
        if not report.is_relative_to(project / "var" / "reports"):
            raise ValueError("load report must stay under project var/reports")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(measurements, indent=2) + "\n", encoding="utf-8")
