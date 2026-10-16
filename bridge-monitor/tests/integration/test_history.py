from pathlib import Path

import psycopg
import pytest

from contracts.queries import CohortQuery
from database.migrate import migrate
from scripts.replay_fixtures import replay_fixture
from service.queries.history import list_work_items, inspect_exchange, read_work_item_detail


FIXTURE = Path(__file__).parents[1] / "fixtures" / "demonstration.json"


@pytest.fixture
def seeded(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
    replay_fixture(test_dsn, FIXTURE)
    return test_dsn


def test_history_is_bounded_and_inspector_returns_exact_observed_bodies(seeded):
    page = list_work_items(seeded, CohortQuery(origin="synthetic"), limit=10, offset=0)
    assert len(page) == 10
    assert "raw_bytes" not in page[0]
    selected = inspect_exchange(seeded, "demo-work-061", role="operator")
    assert selected["work_item_id"] == "demo-work-061"
    assert selected["original_prompt"] == "What is 2 + 2?\n"
    assert selected["local_response"] == "4\n"


def test_detail_uses_confirmed_resolution_for_paid_direct_work(seeded):
    detail = read_work_item_detail(seeded, "demo-work-060")
    assert detail is not None
    assert detail["work_item"]["resolution"] == "paid_direct"
    assert detail["calls"] == []
    assert detail["results"] == []


def test_inspection_rejects_non_operator_and_unknown_item(seeded):
    with pytest.raises(PermissionError):
        inspect_exchange(seeded, "demo-work-061", role="producer")
    assert inspect_exchange(seeded, "missing", role="operator") is None


def test_capacity_and_capture_fixtures_project_through_milestone_one_status(seeded):
    migration = (Path(__file__).parents[2] / "database" / "migrations" /
                 "001_event_store.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS dashboard_status" in migration
    with psycopg.connect(seeded) as conn:
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE version='001_event_store'").fetchone()
        assert conn.execute("""SELECT active,queued,held_uncertain,blocked,
            capture_state,known_gaps,spool_pressure FROM dashboard_status WHERE singleton=true""").fetchone() == (
            1, 2, 1, 3, "healthy", 1, 0.8)


def test_each_delegated_demonstration_item_has_visible_prompt_and_response(seeded):
    selected = inspect_exchange(seeded, "demo-work-100", role="operator")
    assert selected["original_prompt"] == "What is 2 + 2?\n"
    assert selected["local_response"] == "4\n"


def test_synthetic_delegations_retain_requested_and_reported_models(seeded):
    with psycopg.connect(seeded) as conn:
        assert conn.execute("""SELECT count(*) FROM model_executions
            WHERE requested_model='qwen-seed' AND reported_model='qwen-seed'""").fetchone()[0] == 40
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 40


def test_inspector_keeps_paid_exchange_out_of_local_prompt_and_response(seeded):
    from datetime import datetime, timezone
    from contracts.content import ContentObject
    from contracts.events import EventEnvelope, EventType
    from service.ingest.store import ingest_batch

    early = datetime(2026, 10, 16, 10, 1, tzinfo=timezone.utc)
    def paid_event(identifier, sequence, kind, payload):
        return EventEnvelope(event_id=identifier, event_type=kind, schema_version=1,
            producer_id="paid-inspection", producer_incarnation="first",
            producer_sequence=sequence, occurred_at=early, origin="synthetic",
            observation_layer="test", capture_completeness="complete",
            task_id="demo-task-001", work_item_id="demo-work-061",
            call_id="paid-call-061", payload=payload)

    paid_content = [
        ContentObject.from_bytes("paid-prompt-061", b"Paid prompt\n", role="user",
            direction="out", content_type="text/plain", origin="synthetic", capture_layer="test"),
        ContentObject.from_bytes("paid-response-061", b"Paid answer\n", role="assistant",
            direction="in", content_type="text/plain", origin="synthetic", capture_layer="test"),
    ]
    ingest_batch(seeded, "paid-inspection", [
        paid_event("paid-call-061", 1, EventType.CALL_RECEIVED, {}),
        paid_event("paid-message-061", 2, EventType.MESSAGE_SENT,
                   {"message_id": "paid-message-061", "original_content_id": "paid-prompt-061"}),
        paid_event("paid-result-061", 3, EventType.RESULT_DELIVERED,
                   {"result_id": "paid-result-061", "delivered_content_id": "paid-response-061"}),
    ], paid_content)
    selected = inspect_exchange(seeded, "demo-work-061", role="operator")
    assert selected["original_prompt_content_id"] == "demo-prompt-061"
    assert selected["local_response_content_id"] == "demo-response-061"
