from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from contracts.queries import CohortQuery
from database.migrate import migrate
from scripts.replay_fixtures import replay_fixture
from service.http.app import create_app
from service.queries.snapshot import read_publications, read_snapshot


@pytest.fixture
def seeded(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
        conn.execute("UPDATE dashboard_status SET active=0,queued=0,held_uncertain=0,blocked=0,capture_state='unknown',known_gaps=0,spool_pressure=NULL WHERE singleton=true")
    replay_fixture(test_dsn, Path(__file__).parents[1] / "fixtures" / "demonstration.json")
    return test_dsn


def test_snapshot_cursor_and_status_are_from_same_committed_view(seeded):
    snapshot = read_snapshot(seeded, CohortQuery(origin="synthetic"))
    assert snapshot.cursor.position > 0
    assert snapshot.status.held_uncertain >= 1
    assert snapshot.status.known_gaps >= 1
    assert read_publications(seeded, snapshot.cursor.position, limit=10) == []


def test_snapshot_attributes_effort_only_to_the_matching_pinned_call(runtime_dsn):
    from uuid import uuid4

    task = 'flow-model-' + uuid4().hex
    model = 'claude-test'
    with psycopg.connect(runtime_dsn) as conn:
        conn.execute("INSERT INTO tasks(task_id,origin,created_at) VALUES(%s,'observed',now())", (task,))
        conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,created_at) VALUES(%s,%s,'observed',now())",
                     (task, task))
        conn.execute("INSERT INTO runtime_task_budgets(task_id,scope,call_budget,token_budget) "
                     "VALUES (%s,'[]',3,1000)", (task,))
        for suffix, state, requested in [('pinned', 'active', model), ('legacy', 'uncertain', model),
                                         ('mismatch', 'active', 'claude-other')]:
            conn.execute("""INSERT INTO runtime_jobs(call_id,task_id,work_item_id,pool,request,requested_model,
                policy_version,state,queue_deadline_at,slot_held,reserved_tokens)
                VALUES(%s,%s,%s,'claude','{}',%s,'test',%s,now(),true,100)""",
                         (task + suffix, task, task, requested, state))
        conn.execute("INSERT INTO cc_decisions(decision_id,work_item_id,task_id,prompt_sha256,payload) "
                     "VALUES (%s,%s,%s,'test',%s)", (task, task, task, Jsonb({
                         'selected_profile': 'haiku', 'policy_snapshot': {'profiles': {
                             'haiku': {'model_id': model, 'effort': 'low'},
                         }},
                     })))
        for suffix in ('pinned', 'mismatch'):
            conn.execute("INSERT INTO cc_reservations(call_id,work_item_id,workflow_id,profile,amount,state) "
                         "VALUES (%s,%s,%s,'haiku',1,'held')", (task + suffix, task, task))

    try:
        executions = {entry['call_id']: entry for entry in read_snapshot(runtime_dsn, CohortQuery()).worker_executions}
        assert executions[task + 'pinned']['configured_effort'] == 'low'
        assert executions[task + 'pinned']['active'] is True
        assert executions[task + 'legacy']['configured_effort'] is None
        assert executions[task + 'legacy']['active'] is False
        assert executions[task + 'mismatch']['configured_effort'] is None
    finally:
        with psycopg.connect(runtime_dsn) as conn:
            conn.execute("DELETE FROM cc_reservations WHERE workflow_id=%s", (task,))
            conn.execute("DELETE FROM cc_decisions WHERE task_id=%s", (task,))
            conn.execute("DELETE FROM runtime_jobs WHERE task_id=%s", (task,))
            conn.execute("DELETE FROM runtime_task_budgets WHERE task_id=%s", (task,))
            conn.execute("DELETE FROM work_items WHERE work_item_id=%s", (task,))
            conn.execute("DELETE FROM tasks WHERE task_id=%s", (task,))


def test_browser_queries_require_operator_and_bodies_are_on_demand(seeded):
    client = TestClient(create_app(seeded, producer_tokens={"seed": "producer-secret"},
                                   operator_token="operator-secret", payload_limit_bytes=1024))
    assert client.get("/api/v1/snapshot").status_code == 401
    assert client.get("/api/v1/work-items").status_code == 401
    assert client.get("/api/v1/metrics").status_code == 401
    assert client.get("/api/v1/stream").status_code == 401
    headers = {"Authorization": "Bearer operator-secret"}
    snapshot = client.get("/api/v1/snapshot?origin=synthetic", headers=headers)
    assert snapshot.status_code == 200
    assert "cursor" in snapshot.json()
    assert "original_prompt" not in snapshot.text
    assert client.get("/api/v1/work-items?origin=synthetic&limit=2", headers=headers).status_code == 200
    assert client.get("/api/v1/metrics?origin=synthetic", headers=headers).status_code == 200


def test_operator_cookie_is_http_only_and_expiring(seeded):
    client = TestClient(create_app(seeded, producer_tokens={"seed": "producer-secret"},
                                   operator_token="operator-secret", payload_limit_bytes=1024,
                                   session_ttl_seconds=1))
    response = client.post("/api/v1/session", json={"operator_token": "operator-secret"})
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()
    assert client.get("/api/v1/snapshot").status_code == 200
    assert client.post("/api/v1/session/logout").status_code == 204
    assert client.get("/api/v1/snapshot").status_code == 401
