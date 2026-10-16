from datetime import datetime, timezone

import psycopg
import pytest

from contracts.events import EventEnvelope, EventType
from database.migrate import migrate
from service.ingest.store import ingest_batch


@pytest.fixture(autouse=True)
def schema(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences RESTART IDENTITY CASCADE")


def observed(event_id, sequence, kind=EventType.CALL_QUEUED, payload=None):
    return EventEnvelope(event_id=event_id, event_type=kind, schema_version=1,
                         producer_id="worker-a", producer_incarnation="boot-a",
                         producer_sequence=sequence,
                         occurred_at=datetime(2026, 10, 16, tzinfo=timezone.utc),
                         origin="synthetic", observation_layer="bridge",
                         capture_completeness="complete", payload=payload or {})


def test_duplicate_delivery_is_one_observation(test_dsn):
    item = observed("event-1", 1)
    first = ingest_batch(test_dsn, "worker-a", [item], [])
    second = ingest_batch(test_dsn, "worker-a", [item], [])
    assert first == ["committed"]
    assert second == ["duplicate"]
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM publication_stream").fetchone()[0] == 1


def test_simultaneous_duplicate_delivery_returns_one_committed_and_one_duplicate(test_dsn):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic, sleep

    item = observed("simultaneous", 1)
    race_dsn = test_dsn + " application_name=bridge_monitor_duplicate_race"
    with psycopg.connect(test_dsn) as gate:
        gate.execute("LOCK TABLE events IN SHARE MODE")
        with psycopg.connect(test_dsn, autocommit=True) as monitor, ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ingest_batch, race_dsn, "worker-a", [item], [])
                       for _ in range(2)]
            try:
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    waiting = monitor.execute("""SELECT count(*) FROM pg_stat_activity
                        WHERE application_name='bridge_monitor_duplicate_race'
                        AND wait_event_type='Lock'""").fetchone()[0]
                    if waiting == 2:
                        break
                    sleep(0.05)
                diagnostics = monitor.execute("""SELECT application_name,state,wait_event_type,wait_event,
                    left(query,80) FROM pg_stat_activity WHERE application_name='bridge_monitor_duplicate_race'""").fetchall()
                assert waiting == 2, (diagnostics, [(f.done(), repr(f.exception()) if f.done() else None)
                                                   for f in futures])
            finally:
                gate.commit()
            receipts = [future.result(timeout=10)[0] for future in futures]
    assert sorted(receipts) == ["committed", "duplicate"]
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE event_id='simultaneous'").fetchone()[0] == 1


def test_conflicting_sequence_rejected_but_late_reference_retained(test_dsn):
    later = observed("event-later", 2, EventType.CALL_COMPLETED,
                     {"call_id": "call-unknown"})
    assert ingest_batch(test_dsn, "worker-a", [later], []) == ["committed"]
    with pytest.raises(ValueError, match="producer sequence conflict"):
        ingest_batch(test_dsn, "worker-a", [observed("event-other", 2)], [])
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT unresolved_references FROM events WHERE event_id='event-later'").fetchone()[0] == ["call:call-unknown"]


def test_work_item_before_task_is_reconciled_when_task_arrives(test_dsn):
    child = observed("child-first", 1, EventType.WORK_ITEM_CREATED,
                     {"work_item_id": "w-late", "task_id": "t-late", "category": "review"})
    child = child.model_copy(update={"task_id": "t-late", "work_item_id": "w-late"})
    parent = observed("task-second", 2, EventType.TASK_CREATED,
                      {"task_id": "t-late"}).model_copy(update={"task_id": "t-late"})
    assert ingest_batch(test_dsn, "worker-a", [child], []) == ["committed"]
    assert ingest_batch(test_dsn, "worker-a", [parent], []) == ["committed"]
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM work_items WHERE work_item_id='w-late'").fetchone()[0] == 1
        assert conn.execute("SELECT unresolved_references FROM events WHERE event_id='child-first'").fetchone()[0] == []


def test_publications_follow_commit_order_not_sequence_start_order(test_dsn):
    # A later producer sequence can commit first. The cursor follows commits.
    ingest_batch(test_dsn, "worker-a", [observed("event-2", 2)], [])
    ingest_batch(test_dsn, "worker-a", [observed("event-1", 1)], [])
    with psycopg.connect(test_dsn) as conn:
        rows = conn.execute("SELECT event_id FROM publication_stream ORDER BY position").fetchall()
    assert rows == [("event-2",), ("event-1",)]


def test_publication_counter_blocks_a_later_writer_until_first_commit(test_dsn):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from service.ingest.publication import publish
    ingest_batch(test_dsn, "worker-a", [observed("event-1", 1), observed("event-2", 2)], [])
    with psycopg.connect(test_dsn) as conn:
        conn.execute("DELETE FROM publication_stream")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
    first_has_position = Event()
    allow_first_commit = Event()
    second_has_position = Event()
    def first_writer():
        with psycopg.connect(test_dsn) as conn:
            publish(conn, "event-1")
            first_has_position.set()
            assert allow_first_commit.wait(5)
    def second_writer():
        assert first_has_position.wait(5)
        with psycopg.connect(test_dsn) as conn:
            publish(conn, "event-2")
            second_has_position.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_writer)
        second = pool.submit(second_writer)
        assert first_has_position.wait(5)
        assert not second_has_position.wait(0.15)
        allow_first_commit.set()
        first.result(timeout=5)
        second.result(timeout=5)
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT event_id FROM publication_stream ORDER BY position").fetchall() == [
            ("event-1",), ("event-2",)]


def test_capacity_and_capture_events_project_without_runtime_tables(test_dsn):
    events = [
        observed("cap-1", 1, EventType.CAPACITY_RESERVED, {"active": 1, "queued": 2,
                "held_uncertain": 1, "blocked": 0}),
        observed("gap-1", 2, EventType.CAPTURE_GAP, {"known_gaps": 1,
                "capture_state": "degraded", "spool_pressure": 0.8}),
    ]
    assert ingest_batch(test_dsn, "worker-a", events, []) == ["committed", "committed"]
    with psycopg.connect(test_dsn) as conn:
        row = conn.execute("SELECT active, queued, held_uncertain, known_gaps, capture_state FROM dashboard_status WHERE singleton=true").fetchone()
    assert row == (1, 2, 1, 1, "degraded")


def test_invalid_disposition_payload_is_rejected_before_commit(test_dsn):
    bad = observed("bad-disposition", 1, EventType.DISPOSITION_RECORDED,
                   {"disposition_id": "d-bad", "result_id": "r-bad",
                    "actor_id": "claude-1", "stage": "claude", "judgment": "maybe"})
    with pytest.raises(ValueError):
        ingest_batch(test_dsn, "worker-a", [bad], [])
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
