from pathlib import Path

import psycopg
import pytest

from contracts.queries import CohortQuery
from database.migrate import migrate
from service.queries.metrics import query_metrics
from scripts.replay_fixtures import replay_fixture


FIXTURE = Path(__file__).parents[1] / "fixtures" / "demonstration.json"


@pytest.fixture
def seeded(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
        conn.execute("UPDATE dashboard_status SET active=0,queued=0,held_uncertain=0,blocked=0,capture_state='unknown',known_gaps=0,spool_pressure=NULL WHERE singleton=true")
    replay_fixture(test_dsn, FIXTURE)
    return test_dsn


def test_demonstration_statistics_are_reproduced_from_stored_facts(seeded):
    report = query_metrics(seeded, CohortQuery(origin="synthetic"))
    assert report.metrics["routed"].percent == 40.0
    assert report.metrics["not_routed"].percent == 60.0
    assert report.coverage["disposition"].percent == 90.0
    assert report.metrics["confirmed_acceptance"].percent == 60.0
    assert report.metrics["reported_acceptance"].percent == 66.7
    assert report.metrics["contribution"].percent == 75.0
    assert report.metrics["takeover"].percent == 10.0
    assert report.metrics["local_resolution"].percent == 24.0
    assert report.metrics["eligible_delegation"].percent == 40.0
    assert report.coverage["eligibility"].percent == 100.0
    assert report.coverage["final_resolution"].percent == 85.0


def test_replay_is_idempotent_and_synthetic_excluded_by_default(seeded):
    replay_fixture(seeded, FIXTURE)
    report = query_metrics(seeded, CohortQuery(origin="synthetic"))
    assert report.metrics["routed"].denominator == 100
    assert report.metrics["routed"].numerator == 40
    ordinary = query_metrics(seeded, CohortQuery())
    assert ordinary.metrics["routed"].denominator == 0
    assert ordinary.metrics["routed"].percent is None


def test_as_of_cutoff_does_not_gain_later_dispositions(seeded):
    from datetime import datetime, timezone
    report = query_metrics(seeded, CohortQuery(origin="synthetic",
                            as_of=datetime(2026, 10, 16, 10, 1, 1, 650000,
                                           tzinfo=timezone.utc)))
    assert report.metrics["routed"].denominator == 61
    assert report.metrics["routed"].numerator == 1
    assert report.coverage["disposition"].denominator == 1
    assert report.coverage["disposition"].numerator == 0


def test_paid_result_after_local_dispatch_is_not_a_local_response(seeded):
    from datetime import datetime, timezone
    from contracts.events import EventEnvelope, EventType
    from service.ingest.store import ingest_batch
    now = datetime.now(timezone.utc)
    def event(identifier, sequence, kind, call, payload):
        return EventEnvelope(event_id=identifier, event_type=kind, schema_version=1,
            producer_id="paid-seed", producer_incarnation="paid-1", producer_sequence=sequence,
            occurred_at=now, origin="synthetic", observation_layer="fixture",
            capture_completeness="complete", task_id="demo-task-001",
            work_item_id="demo-work-061", call_id=call, payload=payload)
    ingest_batch(seeded, "paid-seed", [
        event("paid-call-061", 1, EventType.CALL_RECEIVED, "paid-call-061", {}),
        event("paid-result-061", 2, EventType.RESULT_DELIVERED, "paid-call-061",
              {"result_id": "paid-result-061"}),
    ], [])
    report = query_metrics(seeded, CohortQuery(origin="synthetic"))
    assert report.coverage["disposition"].denominator == 40
    assert report.metrics["confirmed_acceptance"].denominator == 40


def test_supersession_and_conflicting_dispositions_do_not_invent_a_judgment(seeded):
    from datetime import datetime, timezone
    from contracts.events import EventEnvelope, EventType
    from service.ingest.store import ingest_batch
    now = datetime.now(timezone.utc)
    def correction(identifier, sequence, judgment, supersedes=None):
        return EventEnvelope(event_id=identifier, event_type=EventType.DISPOSITION_RECORDED,
            schema_version=1, producer_id="review-seed", producer_incarnation="review-1",
            producer_sequence=sequence, occurred_at=now, origin="synthetic",
            observation_layer="fixture", capture_completeness="complete",
            task_id="demo-task-001", work_item_id="demo-work-061", call_id="demo-call-061",
            payload={"disposition_id": identifier, "result_id": "demo-result-061",
                     "actor_id": "claude-seed", "stage": "claude", "judgment": judgment,
                     "supersedes_id": supersedes})
    ingest_batch(seeded, "review-seed", [correction("disp-corrected-061", 1, "revised",
                                                   "disp-demo-work-061")], [])
    after_correction = query_metrics(seeded, CohortQuery(origin="synthetic"))
    assert after_correction.metrics["confirmed_acceptance"].numerator == 23
    assert after_correction.metrics["contribution"].numerator == 30
    ingest_batch(seeded, "review-seed", [correction("disp-conflict-061", 2, "unused")], [])
    after_conflict = query_metrics(seeded, CohortQuery(origin="synthetic"))
    assert after_conflict.coverage["disposition"].numerator == 35
    assert after_conflict.metrics["contribution"].numerator == 29
