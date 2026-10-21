"""Current execution evidence must not replace historical agent observations."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from contracts.events import EventEnvelope
from scripts.replay_fixtures import replay_fixture
from service.ingest.store import ingest_batch
from service.queries.history import read_work_item_detail


@pytest.fixture
def inspection_db(runtime_dsn):
    with psycopg.connect(runtime_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
    replay_fixture(runtime_dsn, Path(__file__).parents[1] / "fixtures/demonstration.json")
    return runtime_dsn


def add_job(conn, work_id="demo-work-061", *, state="terminated", held=False):
    call_id = conn.execute("SELECT call_id FROM events WHERE work_item_id=%s "
        "AND event_type='call.dispatched' AND payload->>'destination'='skynet' LIMIT 1",
        (work_id,)).fetchone()[0]
    task_id = str(uuid.uuid4())
    conn.execute("INSERT INTO runtime_task_budgets(task_id,scope,call_budget,token_budget) "
                 "VALUES(%s,'[\"analysis\"]',2,8192)", (task_id,))
    conn.execute("INSERT INTO runtime_jobs(call_id,task_id,work_item_id,pool,request,"
        "policy_version,state,queue_deadline_at,reserved_tokens,slot_held) "
        "VALUES(%s,%s,%s,'qwen','{}','test',%s,now(),2048,%s)",
        (call_id, task_id, work_id, state, held))
    return call_id


def add_receipt(conn, call_id, identifier="receipt-1", evidence=None):
    conn.execute("INSERT INTO runtime_reconciliations(reconciliation_id,call_id,"
        "observed_state,evidence,actor_kind,actor_id,recorded_at) "
        "VALUES(%s,%s,'terminated',%s,'operator','test-operator','2026-10-21T10:50:00Z')",
        (identifier, call_id, Jsonb(evidence or {
            "kind": "adapter_termination_receipt", "provenance": "operator_correlated_server_abort",
            "basis": "Server aborted the unique request", "terminated": True,
            "source_sha256": "a" * 64, "historical_runner_pid": None,
        })))


def test_current_reconciliation_preserves_timeout_and_outcome(inspection_db):
    before = read_work_item_detail(inspection_db, "demo-work-061")
    with psycopg.connect(inspection_db) as conn:
        call_id = add_job(conn)
        add_receipt(conn, call_id)
    now = datetime.now(timezone.utc)
    events = [EventEnvelope(schema_version=1, event_id=str(uuid.uuid4()),
        producer_id="inspector-test", producer_incarnation="once", producer_sequence=index,
        event_type=kind, occurred_at=now, origin="synthetic", observation_layer="test",
        capture_completeness="complete", task_id="demo-task-001",
        work_item_id="demo-work-061", call_id=call_id, payload=payload)
        for index, (kind, payload) in enumerate([
            ("attempt.status", {"agent_state": "ollama_connecting", "agent_reachable": True}),
            ("attempt.uncertain", {"reason": "first_output_deadline"}),
        ], 1)]
    ingest_batch(inspection_db, "inspector-test", events, [])
    detail = read_work_item_detail(inspection_db, "demo-work-061")
    execution = next(x for x in detail["runtime_executions"] if x["call_id"] == call_id)
    assert execution["state"] == "terminated" and execution["slot_held"] is False
    assert execution["receipts"][0]["reconciliation_id"] == "receipt-1"
    assert execution["receipts"][0]["actor_id"] == "test-operator"
    assert execution["receipts"][0]["evidence"]["source_sha256"] == "a" * 64
    assert next(x for x in detail["agent_statuses"] if x["call_id"] == call_id)[
        "timeout_reason"] == "first_output_deadline"
    assert detail["resolutions"] == before["resolutions"]
    with psycopg.connect(inspection_db) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (call_id,)).fetchone() == ("terminated", False)


def test_receipts_are_scoped_bounded_and_project_only_safe_evidence(inspection_db):
    with psycopg.connect(inspection_db) as conn:
        call_id = add_job(conn, state="fallback", held=True)
        other = add_job(conn, "demo-work-062")
        add_receipt(conn, other, "other-receipt")
        for index in range(25):
            add_receipt(conn, call_id, f"receipt-{index:02d}", {
                "basis": "x" * 5000, "token": "NEVER_RETURN_SECRET",
                "nested": {"password": "NEVER_RETURN_SECRET"},
                "kind": {"token": "NEVER_RETURN_SECRET"}, "terminated": "false",
                "source_sha256": "invalid", "historical_runner_pid": None,
            })
    detail = read_work_item_detail(inspection_db, "demo-work-061")
    assert len(detail["runtime_executions"]) == 1
    execution = detail["runtime_executions"][0]
    assert execution["slot_held"] is True and execution["state"] == "fallback"
    assert execution["receipts_truncated"] is True
    assert len(execution["receipts"]) == 20
    assert execution["receipts"][0]["reconciliation_id"] == "receipt-24"
    evidence = execution["receipts"][0]["evidence"]
    assert len(evidence["basis"]) <= 4000
    assert not {"kind", "terminated", "source_sha256", "token", "nested"} & evidence.keys()
    assert "NEVER_RETURN_SECRET" not in json.dumps(detail, default=str)
    assert "other-receipt" not in json.dumps(detail, default=str)


def test_legacy_calls_without_runtime_record_remain_unknown(inspection_db):
    detail = read_work_item_detail(inspection_db, "demo-work-061")
    assert detail["runtime_executions"] == []
