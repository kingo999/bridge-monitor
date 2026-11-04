import json
from datetime import datetime, timezone

import psycopg
import pytest
from database.migrate import migrate
from service.queries.audit import refresh, start_search, page_snapshot, network_status
from service.queries.audit import cohort_snapshot, measured
from service.queries.codex_claude import query_codex_claude
from contracts.queries import CohortQuery
from psycopg.types.json import Jsonb


@pytest.fixture
def audit_db(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn) as db:
        db.execute("TRUNCATE audit_ui.snapshot_rows, audit_ui.snapshots, audit_ui.entries, audit_ui.dirty")
        db.execute("TRUNCATE outbound_audit.observations, outbound_audit.source")
        db.execute("UPDATE audit_ui.state SET network_high=0")
        db.execute("INSERT INTO outbound_audit.source(journal_path,file_identity,last_id,synced_at) "
                   "VALUES ('test','test',3,now())")
        rows = [
            (1, dict(kind="transfer", layer="host", at_ns=1_000_000_000,
                     ip="192.0.2.1", port=443, bytes_out=0, image="old.exe")),
            (2, dict(kind="health", layer="collector", at_ns=2_000_000_000)),
            (3, {"kind": "transfer", "unexpected_owner": "invalid"}),
        ]
        for i, data in rows:
            db.execute("INSERT INTO outbound_audit.observations(id,evidence) VALUES (%s,%s)",
                       (i, json.dumps(data)))
    return test_dsn


def test_full_history_coverage_snapshot_and_owner(audit_db):
    refresh(audit_db)
    status = network_status(audit_db)
    assert status["indexed"] == 3 and status["invalid"] == 1
    assert status["health_processed_at"] is not None
    token = start_search(audit_db, "operator-a", dict(view="network", range="all", q="old.exe"))
    first = page_snapshot(audit_db, "operator-a", token["token"])
    assert first["total"] == 1 and first["rows"][0]["bytes"] == 0
    with psycopg.connect(audit_db) as db:
        db.execute("INSERT INTO outbound_audit.observations(id,evidence) VALUES (4,%s)",
                   (json.dumps(dict(kind="transfer", at_ns=3_000_000_000, image="old.exe")),))
        db.execute("UPDATE outbound_audit.source SET last_id=4")
    refresh(audit_db)
    assert page_snapshot(audit_db, "operator-a", token["token"])["total"] == 1
    with pytest.raises(ValueError, match="expired"):
        page_snapshot(audit_db, "operator-b", token["token"])
    assert start_search(audit_db, "operator-a", dict(view="network", range="all", q="old.exe"))["total"] == 2


def test_gap_bytes_and_invalid_metadata(audit_db):
    refresh(audit_db)
    result = start_search(audit_db, "a", dict(view="network", range="all", q="3", mode="exact"))
    rows = page_snapshot(audit_db, "a", result["token"])["rows"]
    assert rows[0]["error"]
    with pytest.raises(ValueError):
        start_search(audit_db, "a", dict(view="network", sort="drop table", range="all"))


def test_execution_dirty_and_historical_cohort(audit_db):
    import uuid
    wid = "audit-fixture-" + uuid.uuid4().hex
    payload = dict(action="keep_owner", reason="test", facts=dict(category="audit-test", project_root="historic"))
    with psycopg.connect(audit_db) as db:
        db.execute("INSERT INTO cc_decisions(decision_id,work_item_id,task_id,prompt_sha256,payload) "
                   "VALUES(%s,%s,'test','test',%s)", (wid, wid, Jsonb(payload)))
    refresh(audit_db)
    result = start_search(audit_db, "a", dict(view="executions", range="all", q=wid, mode="exact"))
    assert result["total"] == 1
    with psycopg.connect(audit_db) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        report = query_codex_claude(audit_db, CohortQuery(origin="observed"), connection=db, work_item_ids=[wid])
        token = cohort_snapshot(db, "a", report)
    with psycopg.connect(audit_db) as db:
        db.execute("UPDATE cc_decisions SET payload=jsonb_set(payload,'{facts,project_root}',%s) "
                   "WHERE work_item_id=%s", (Jsonb("current"), wid))
    refresh(audit_db)
    historical = start_search(audit_db, "a", dict(view="executions", range="all", project="historic",
                                                cohort_token=token, q=wid, mode="exact"))
    rows = page_snapshot(audit_db, "a", historical["token"])
    assert rows["total"] == 1 and rows["rows"][0]["project"] == "historic"
    assert measured({"usage": {"input_tokens": 0}})
    assert not measured({"usage": {"input_tokens": None}, "cost_usd_equivalent": "0.1"})


def test_production_write_commits_while_indexer_publishes(audit_db, monkeypatch):
    import threading
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from service.queries import audit
    wid = "concurrent-" + uuid.uuid4().hex
    payload = dict(action="keep_owner", facts=dict(category="audit-test", project_root="old"))
    with psycopg.connect(audit_db) as db:
        db.execute("INSERT INTO cc_decisions VALUES(%s,%s,'test','test',%s,now())", (wid, wid, Jsonb(payload)))
    reached, release = threading.Event(), threading.Event()
    original = audit.query_codex_claude
    def paused(*args, **kwargs):
        reached.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(audit, "query_codex_claude", paused)
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(refresh, audit_db)
        assert reached.wait(5)
        try:
            with psycopg.connect(audit_db) as db:
                db.execute("SET LOCAL lock_timeout='1s'")
                db.execute("UPDATE cc_decisions SET payload=jsonb_set(payload,'{facts,project_root}',%s) "
                           "WHERE work_item_id=%s", (Jsonb("new"), wid))
        finally:
            release.set()
        worker.result(timeout=10)
    monkeypatch.setattr(audit, "query_codex_claude", original)
    with psycopg.connect(audit_db) as db:
        assert db.execute("SELECT count(*) FROM audit_ui.dirty WHERE work_item_id=%s", (wid,)).fetchone()[0] >= 1
    refresh(audit_db)
    query = start_search(audit_db, "a", dict(view="executions", range="all", q=wid, mode="exact"))
    assert page_snapshot(audit_db, "a", query["token"])["rows"][0]["project"] == "new"


def test_overview_survives_missing_snapshot_schema(audit_db):
    from fastapi.testclient import TestClient
    from service.http.app import create_app
    app = create_app(audit_db, producer_tokens={"test": "producer"}, operator_token="operator", payload_limit_bytes=1024)
    client = TestClient(app)
    with psycopg.connect(audit_db) as db:
        db.execute("ALTER SCHEMA audit_ui RENAME TO audit_temporarily_offline")
    try:
        response = client.get("/api/v1/codex-claude/cohort?audit_snapshot=true", headers={"Authorization": "Bearer operator"})
        assert response.status_code == 200
        assert response.json()["audit_unavailable"] is True
    finally:
        with psycopg.connect(audit_db) as db:
            db.execute("ALTER SCHEMA audit_temporarily_offline RENAME TO audit_ui")


def test_late_call_link_is_captured_in_new_snapshot(audit_db):
    import uuid
    key = "late-" + uuid.uuid4().hex
    with psycopg.connect(audit_db) as db:
        event = dict(kind="transfer", layer="host", at_ns=3_000_000_000, run=key,
                     attribution="registered", bytes_out=0)
        db.execute("INSERT INTO outbound_audit.observations(id,evidence) VALUES(4,%s)", (json.dumps(event),))
        db.execute("UPDATE outbound_audit.source SET last_id=4")
    refresh(audit_db)
    filters = dict(view="network", range="all", run=key)
    original = start_search(audit_db, "a", filters)
    assert "linked_work_item" not in page_snapshot(audit_db, "a", original["token"])["rows"][0]["data"]
    with psycopg.connect(audit_db) as db:
        db.execute("INSERT INTO tasks(task_id,origin,created_at) VALUES(%s,'synthetic',now())", (key,))
        db.execute("INSERT INTO work_items(work_item_id,task_id,origin,created_at) VALUES(%s,%s,'synthetic',now())", (key,key))
        db.execute("INSERT INTO calls(call_id,work_item_id,created_at) VALUES(%s,%s,now())", (key,key))
    later = start_search(audit_db, "a", filters)
    assert page_snapshot(audit_db, "a", later["token"])["rows"][0]["data"]["linked_work_item"] == key
    assert "linked_work_item" not in page_snapshot(audit_db, "a", original["token"])["rows"][0]["data"]


def test_invalid_filters_and_active_snapshot_survives_poll_churn(audit_db):
    refresh(audit_db)
    query = start_search(audit_db, "a", dict(view="network", range="all"))
    page_snapshot(audit_db, "a", query["token"])
    for _ in range(8):
        start_search(audit_db, "a", dict(view="network", range="all"))
    assert page_snapshot(audit_db, "a", query["token"])["total"] == 3
    for filters in ({"view": ["network"]}, {"view": "network", "from": 1}, {"view": "network", "sql": "x"}):
        with pytest.raises(ValueError):
            start_search(audit_db, "a", filters)
