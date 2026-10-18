from pathlib import Path
import hashlib
from datetime import datetime, timedelta, timezone

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


def test_gpt_stage_counts_delivered_claude_results_including_missing_ack(seeded):
    delivered_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    acknowledged_at = delivered_at + timedelta(seconds=1)
    parent = "gpt:demo-task-001"
    with psycopg.connect(seeded) as conn:
        conn.execute("INSERT INTO calls(call_id,work_item_id,created_at) "
                     "VALUES(%s,'demo-work-061',%s) ON CONFLICT DO NOTHING",
                     (parent, delivered_at))
        for index in (1, 2):
            call_id = f"gpt-metric-claude-{index}"
            result_id = f"gpt-metric-result-{index}"
            content_id = f"gpt-metric-content-{index}"
            body = f"Claude answer {index}".encode()
            digest = hashlib.sha256(body).hexdigest()
            conn.execute("INSERT INTO calls(call_id,work_item_id,parent_call_id,"
                "requested_model,created_at) VALUES(%s,'demo-work-061',%s,'sonnet',%s)",
                (call_id, parent, delivered_at))
            conn.execute("INSERT INTO content_objects(content_id,raw_bytes,byte_length,sha256,"
                "role,direction,content_type,encoding,origin,capture_layer,completeness,"
                "block_order) VALUES(%s,%s,%s,%s,'assistant','in','text/plain','utf-8',"
                "'synthetic','test','complete',0)", (content_id, body, len(body), digest))
            conn.execute("INSERT INTO results(result_id,call_id,raw_content_id,"
                "delivered_content_id,delivered_at) VALUES(%s,%s,%s,%s,%s)",
                (result_id, call_id, content_id, content_id, delivered_at))
        conn.execute("INSERT INTO dispositions(disposition_id,result_id,actor_id,stage,"
            "judgment,recorded_at,input_sha256) VALUES('gpt-metric-ack-1',"
            "'gpt-metric-result-1',%s,'gpt','accepted',%s,%s)",
            (f"gpt-parent:{parent}", acknowledged_at,
             hashlib.sha256(b"Claude answer 1").hexdigest()))
        conn.execute("INSERT INTO dispositions(disposition_id,result_id,actor_id,stage,"
            "judgment,recorded_at,input_sha256) VALUES('gpt-metric-bad-hash',"
            "'gpt-metric-result-2',%s,'gpt','accepted',%s,%s)",
            (f"gpt-parent:{parent}", acknowledged_at, "0" * 64))
    before = query_metrics(seeded, CohortQuery(origin="synthetic", consumer_stage="gpt",
        as_of=delivered_at + timedelta(milliseconds=500)))
    after = query_metrics(seeded, CohortQuery(origin="synthetic", consumer_stage="gpt"))
    claude = query_metrics(seeded, CohortQuery(origin="synthetic"))
    assert before.coverage["disposition"].denominator == 2
    assert before.coverage["disposition"].numerator == 0
    assert after.coverage["disposition"].denominator == 2
    assert after.coverage["disposition"].numerator == 1
    assert after.metrics["confirmed_acceptance"].numerator == 1
    assert claude.coverage["disposition"].denominator == 40
    with psycopg.connect(seeded) as conn:
        conn.execute("INSERT INTO dispositions(disposition_id,result_id,actor_id,stage,"
            "judgment,supersedes_id,recorded_at,input_sha256) VALUES("
            "'gpt-metric-revision-1','gpt-metric-result-1',%s,'gpt','revised',"
            "'gpt-metric-ack-1',now(),%s)",
            (f"gpt-parent:{parent}", hashlib.sha256(b"Claude answer 1").hexdigest()))
    revised = query_metrics(seeded, CohortQuery(origin="synthetic", consumer_stage="gpt"))
    assert revised.coverage["disposition"].numerator == 1
    assert revised.metrics["confirmed_acceptance"].numerator == 0
    assert revised.metrics["contribution"].numerator == 1


def test_resolved_work_without_delegation_record_is_separate_from_pending(seeded):
    from datetime import datetime, timezone

    created = datetime(2026, 10, 16, 11, 0, tzinfo=timezone.utc)
    resolved = datetime(2026, 10, 16, 11, 1, tzinfo=timezone.utc)
    with psycopg.connect(seeded) as conn:
        conn.execute("INSERT INTO tasks(task_id,origin,created_at) VALUES(%s,%s,%s)",
                     ("no-route-task", "synthetic", created))
        conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,category,created_at) "
                     "VALUES(%s,%s,%s,%s,%s)",
                     ("no-route-work", "no-route-task", "synthetic", "analysis", created))
        conn.execute("INSERT INTO work_resolutions(resolution_id,work_item_id,outcome,recorded_at) "
                     "VALUES(%s,%s,%s,%s)",
                     ("no-route-resolution", "no-route-work", "paid_direct", resolved))

    before = query_metrics(seeded, CohortQuery(origin="synthetic", as_of=created))
    after = query_metrics(seeded, CohortQuery(origin="synthetic", as_of=resolved))
    assert before.coverage["resolved_without_delegation_record"].numerator == 0
    assert after.coverage["resolved_without_delegation_record"].numerator == 1
    assert after.metrics["pending"].numerator == before.metrics["pending"].numerator
    assert after.metrics["not_routed"].numerator == before.metrics["not_routed"].numerator


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
