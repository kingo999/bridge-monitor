from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from contracts.events import EventEnvelope, EventType
from contracts.content import ContentObject
from database.migrate import migrate
from service.ingest.store import ingest_batch


BASE = datetime(2026, 10, 16, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean_store(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")


def event(identifier, sequence, kind, *, when=BASE, producer="review-test", **fields):
    return EventEnvelope(
        event_id=identifier, event_type=kind, schema_version=1,
        producer_id=producer, producer_incarnation="first", producer_sequence=sequence,
        occurred_at=when, origin="synthetic", observation_layer="test",
        capture_completeness="complete", **fields,
    )


def test_malformed_deferred_decision_is_rejected_before_it_can_poison_reconciliation(test_dsn):
    malformed = event("bad-decision", 1, EventType.DELEGATION_DECIDED,
                      task_id="task-later", work_item_id="work-later",
                      payload={"decision_id": "decision-later"})
    with pytest.raises(ValueError):
        ingest_batch(test_dsn, "review-test", [malformed], [])
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_dispatch_without_work_identity_is_rejected_before_accounting(test_dsn):
    malformed = event("bad-dispatch", 1, EventType.CALL_DISPATCHED,
                      call_id="c", payload={"destination": "skynet"})
    with pytest.raises(ValueError):
        ingest_batch(test_dsn, "review-test", [malformed], [])
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_invalid_capacity_observation_is_rejected_before_storage(test_dsn):
    malformed = event("bad-capacity", 1, EventType.CAPACITY_RESERVED,
                      payload={"active": "many"})
    with pytest.raises(ValueError):
        ingest_batch(test_dsn, "review-test", [malformed], [])
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_result_requires_call_identity_on_envelope(test_dsn):
    malformed = event("bad-result", 1, EventType.RESULT_DELIVERED,
                      payload={"result_id": "r", "call_id": "c"})
    with pytest.raises(ValueError, match="call_id"):
        ingest_batch(test_dsn, "review-test", [malformed], [])
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_reconciliation_reaches_a_resolvable_event_after_1000_stale_references(test_dsn):
    stale = [event(f"stale-{n}", n, EventType.CALL_COMPLETED,
                   when=BASE + timedelta(milliseconds=n),
                   call_id="call-never") for n in range(1, 1001)]
    assert len(ingest_batch(test_dsn, "review-test", stale, [])) == 1000
    child = event("child", 1001, EventType.WORK_ITEM_CREATED,
                  when=BASE + timedelta(seconds=2), task_id="task-later",
                  work_item_id="work-later", payload={"category": "test"})
    parent = event("parent", 1002, EventType.TASK_CREATED,
                   when=BASE + timedelta(seconds=3), task_id="task-later")
    ingest_batch(test_dsn, "review-test", [child], [])
    ingest_batch(test_dsn, "review-test", [parent], [])
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM work_items WHERE work_item_id='work-later'").fetchone()[0] == 1
        assert conn.execute("SELECT unresolved_references FROM events WHERE event_id='child'").fetchone()[0] == []


def test_attempt_result_disposition_and_resolution_project_from_one_batch(test_dsn):
    records = [
        event("task", 1, EventType.TASK_CREATED, task_id="t"),
        event("work", 2, EventType.WORK_ITEM_CREATED, task_id="t", work_item_id="w"),
        event("call", 3, EventType.CALL_RECEIVED, task_id="t", work_item_id="w", call_id="c"),
        event("attempt", 4, EventType.ATTEMPT_STARTED, task_id="t", work_item_id="w",
              call_id="c", attempt_id="a", payload={"attempt_number": 1}),
        event("result", 5, EventType.RESULT_DELIVERED, task_id="t", work_item_id="w",
              call_id="c", attempt_id="a", payload={"result_id": "r"}),
        event("disposition", 6, EventType.DISPOSITION_RECORDED,
              task_id="t", work_item_id="w", call_id="c",
              payload={"disposition_id": "d", "result_id": "r", "actor_id": "claude",
                       "stage": "claude", "judgment": "accepted"}),
        event("resolution", 7, EventType.WORK_RESOLVED, task_id="t", work_item_id="w",
              payload={"resolution_id": "resolution", "outcome": "local_retained", "final_result_id": "r"}),
    ]
    assert ingest_batch(test_dsn, "review-test", records, []) == ["committed"] * len(records)
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT call_id,attempt_number,status FROM attempts WHERE attempt_id='a'").fetchone() == ("c", 1, "started")
        assert conn.execute("SELECT attempt_id FROM results WHERE result_id='r'").fetchone() == ("a",)
        assert conn.execute("SELECT result_id FROM dispositions WHERE disposition_id='d'").fetchone() == ("r",)
        assert conn.execute("SELECT final_result_id FROM work_resolutions WHERE resolution_id='resolution'").fetchone() == ("r",)
        assert conn.execute("SELECT count(*) FROM events WHERE cardinality(unresolved_references)>0").fetchone()[0] == 0


def test_conflicting_deferred_chunk_is_quarantined_without_blocking_content(test_dsn):
    chunks = [
        event("chunk-1", 1, EventType.RESPONSE_DELTA,
              payload={"message_id": "message", "chunk_sequence": 1, "content_id": "content-1"}),
        event("chunk-2", 2, EventType.RESPONSE_DELTA,
              payload={"message_id": "message", "chunk_sequence": 1, "content_id": "content-2"}),
    ]
    ingest_batch(test_dsn, "review-test", chunks, [])
    content = [ContentObject.from_bytes(f"content-{n}", str(n).encode(),
                role="assistant", direction="in", content_type="text/plain",
                origin="synthetic", capture_layer="test") for n in (1, 2)]
    assert ingest_batch(test_dsn, "review-test", [], content) == []
    assert ingest_batch(test_dsn, "review-test", [event("later", 3, EventType.TASK_CREATED,
                                             task_id="later")], []) == ["committed"]
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT content_id FROM message_chunks WHERE message_id='message' AND chunk_sequence=1").fetchone() == ("content-1",)
        assert conn.execute("SELECT unresolved_references FROM events WHERE event_id='chunk-2'").fetchone()[0] == ["projection_error:ValueError"]
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 3


def test_requested_and_reported_models_reconcile_without_overwriting_each_other(test_dsn):
    records = [
        event("task", 1, EventType.TASK_CREATED, task_id="t"),
        event("work", 2, EventType.WORK_ITEM_CREATED, task_id="t", work_item_id="w"),
        event("call", 3, EventType.CALL_RECEIVED, task_id="t", work_item_id="w", call_id="c"),
        event("attempt", 4, EventType.ATTEMPT_STARTED, call_id="c", attempt_id="a",
              payload={"attempt_number": 1}),
        event("reported-first", 5, EventType.MODEL_REPORTED, call_id="c", attempt_id="a",
              model_execution_id="m", reported_model="qwen-actual"),
        event("selected-later", 6, EventType.MODEL_SELECTED, call_id="c", attempt_id="a",
              model_execution_id="m", requested_model="qwen-requested"),
    ]
    ingest_batch(test_dsn, "review-test", records, [])
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT attempt_id,requested_model,reported_model FROM model_executions WHERE model_execution_id='m'").fetchone() == ("a", "qwen-requested", "qwen-actual")
        assert conn.execute("SELECT unresolved_references FROM events WHERE event_id='reported-first'").fetchone()[0] == []
