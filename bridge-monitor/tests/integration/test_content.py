from datetime import datetime, timezone

import psycopg
import pytest

from contracts.content import ContentObject
from contracts.events import EventEnvelope, EventType
from database.migrate import migrate
from service.ingest.store import ingest_batch, read_content


@pytest.fixture(autouse=True)
def schema(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences RESTART IDENTITY CASCADE")


def test_exact_bytes_are_retained_and_read_requires_operator(test_dsn):
    raw = b" {\"answer\": 42}\r\n"
    body = ContentObject.from_bytes("body-a", raw, role="assistant", direction="out",
                                    content_type="application/json", origin="synthetic",
                                    capture_layer="runtime")
    event = EventEnvelope(event_id="evt-a", event_type=EventType.MESSAGE_RECEIVED,
                          schema_version=1, producer_id="worker-a", producer_incarnation="boot-a",
                          producer_sequence=1, occurred_at=datetime.now(timezone.utc),
                          origin="synthetic", observation_layer="bridge",
                          capture_completeness="complete", payload={"content_id": "body-a"})
    assert ingest_batch(test_dsn, "worker-a", [event], [body]) == ["committed"]
    with pytest.raises(PermissionError):
        read_content(test_dsn, "body-a", role="producer")
    assert read_content(test_dsn, "body-a", role="operator") == raw


def test_content_and_events_rollback_together(test_dsn):
    body = ContentObject.from_bytes("body-a", b"one", role="assistant", direction="out",
                                    content_type="text/plain", origin="synthetic",
                                    capture_layer="runtime")
    event = EventEnvelope(event_id="evt-a", event_type=EventType.MESSAGE_RECEIVED,
                          schema_version=1, producer_id="worker-a", producer_incarnation="boot-a",
                          producer_sequence=1, occurred_at=datetime.now(timezone.utc),
                          origin="synthetic", observation_layer="bridge",
                          capture_completeness="complete", payload={"content_id": "body-a"})
    with pytest.raises(ValueError):
        ingest_batch(test_dsn, "worker-a", [event], [body, body])
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM content_objects").fetchone()[0] == 0


def test_out_of_order_chunks_keep_order_and_reject_conflicting_sequence(test_dsn):
    first = ContentObject.from_bytes("chunk-a", b"A", role="assistant", direction="in",
                                     content_type="text/plain", origin="synthetic", capture_layer="stream")
    second = ContentObject.from_bytes("chunk-b", b"B", role="assistant", direction="in",
                                      content_type="text/plain", origin="synthetic", capture_layer="stream")
    def delta(event_id, producer_sequence, chunk_sequence, content_id):
        return EventEnvelope(event_id=event_id, event_type=EventType.RESPONSE_DELTA,
            schema_version=1, producer_id="worker-a", producer_incarnation="boot-a",
            producer_sequence=producer_sequence, occurred_at=datetime.now(timezone.utc),
            origin="synthetic", observation_layer="runtime", capture_completeness="partial",
            payload={"message_id": "stream-1", "chunk_sequence": chunk_sequence,
                     "content_id": content_id})
    assert ingest_batch(test_dsn, "worker-a", [delta("evt-b", 2, 2, "chunk-b"),
                                         delta("evt-a", 1, 1, "chunk-a")], [first, second]) == [
        "committed", "committed"]
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT content_id FROM message_chunks ORDER BY chunk_sequence").fetchall() == [
            ("chunk-a",), ("chunk-b",)]
    with pytest.raises(ValueError, match="chunk sequence conflict"):
        ingest_batch(test_dsn, "worker-a", [delta("evt-conflict", 3, 1, "chunk-b")], [])


def test_content_identity_cannot_be_reused_with_different_metadata(test_dsn):
    original = ContentObject.from_bytes("body-a", b"same", role="user", direction="out",
                                        content_type="text/plain", origin="synthetic",
                                        capture_layer="runtime")
    altered = original.model_copy(update={"role": "assistant"})
    assert ingest_batch(test_dsn, "worker-a", [], [original]) == []
    with pytest.raises(ValueError, match="content ID conflict"):
        ingest_batch(test_dsn, "worker-a", [], [altered])


def test_simultaneous_identical_content_uploads_are_idempotent(test_dsn):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic, sleep

    body = ContentObject.from_bytes("simultaneous-content", b"same", role="user",
                                    direction="out", content_type="text/plain",
                                    origin="synthetic", capture_layer="test")
    race_dsn = test_dsn + " application_name=bridge_monitor_content_race"
    with psycopg.connect(test_dsn) as gate:
        gate.execute("LOCK TABLE content_objects IN SHARE MODE")
        with psycopg.connect(test_dsn, autocommit=True) as monitor, ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ingest_batch, race_dsn, "worker-a", [], [body])
                       for _ in range(2)]
            try:
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    waiting = monitor.execute("""SELECT count(*) FROM pg_stat_activity
                        WHERE application_name='bridge_monitor_content_race'
                        AND wait_event_type='Lock'""").fetchone()[0]
                    if waiting == 2:
                        break
                    sleep(0.05)
                assert waiting == 2
            finally:
                gate.commit()
            assert [future.result(timeout=10) for future in futures] == [[], []]
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM content_objects WHERE content_id='simultaneous-content'").fetchone()[0] == 1
