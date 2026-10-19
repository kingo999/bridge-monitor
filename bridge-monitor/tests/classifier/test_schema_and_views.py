"""Migration 007 in an isolated database, immutability, rollback and the separated cohort view."""

import threading
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from bridge.classifier import ingest, store
from bridge.classifier.runner import ShadowRunner
from contracts.queries import CohortQuery
from database.migrate import MIGRATIONS, migrate
from service.http.app import create_app
from service.queries.classifier import ClassifierCohortFilter, query_classifier_cohort
from service.queries.metrics import query_metrics
from service.queries.router import RouterCohortFilter, query_shadow_cohort
from tests.classifier.conftest import GENERAL_FACTS, table_counts
from tests.classifier.fakes import FakeOllama, reply_json

DOWN = Path(MIGRATIONS).parent / "rollbacks" / "007_classifier_shadow_down.sql"


@pytest.fixture
def isolated(test_dsn):
    """A throwaway database migrated only through 006, like the current operational schema."""
    name = f"bridge_monitor_test_cls_{uuid.uuid4().hex[:8]}"
    admin = make_conninfo(test_dsn, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    dsn = make_conninfo(test_dsn, dbname=name)
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute("CREATE TABLE schema_migrations (version text PRIMARY KEY, "
                         "applied_at timestamptz NOT NULL DEFAULT now())")
            for source in sorted(MIGRATIONS.glob("00[1-6]_*.sql")):
                conn.execute(source.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (source.stem,))
            conn.execute("INSERT INTO tasks(task_id,origin,scope,created_at) VALUES ('t1','observed','{}',now())")
            conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,category,created_at) "
                         "VALUES ('w1','t1','observed','x',now())")
            conn.execute("INSERT INTO content_objects(content_id,raw_bytes,byte_length,sha256,role,"
                         "direction,content_type,encoding,origin,capture_layer,completeness,block_order) "
                         "VALUES ('c1',E'\\\\x6162',2,'x','user','out','text/plain','utf-8','observed',"
                         "'bridge','complete',0)")
        yield dsn
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def preserved(dsn):
    with psycopg.connect(dsn) as conn:
        return (conn.execute("SELECT count(*) FROM tasks").fetchone()[0],
                conn.execute("SELECT count(*) FROM work_items").fetchone()[0],
                conn.execute("SELECT count(*) FROM content_objects").fetchone()[0])


def test_migration_is_additive_preserves_records_and_rolls_back_cleanly(isolated):
    before = preserved(isolated)
    assert migrate(isolated) == ["007_classifier_shadow"]
    assert preserved(isolated) == before
    with psycopg.connect(isolated) as conn:
        assert conn.execute("SELECT to_regclass('classifier_observations') IS NOT NULL").fetchone()[0]
    with psycopg.connect(isolated) as conn:
        conn.execute(DOWN.read_text(encoding="utf-8"))
        assert conn.execute("SELECT to_regclass('classifier_observations')").fetchone()[0] is None
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert versions[-1] == "006_router_shadow" and preserved(isolated) == before
    assert migrate(isolated) == ["007_classifier_shadow"]           # re-applicable


def test_cohort_reports_schema_not_applied_instead_of_failing(isolated):
    report = query_classifier_cohort(isolated, ClassifierCohortFilter())
    assert report["available"] is False and report["reason"] == "schema_not_applied"
    app = create_app(isolated, producer_tokens={"p": "producer-token"}, operator_token="operator-token",
                     payload_limit_bytes=1_000_000)
    answer = TestClient(app).get("/api/v1/classifier/cohort",
                                 headers={"Authorization": "Bearer operator-token"})
    assert answer.status_code == 200 and answer.json()["reason"] == "schema_not_applied"


def make_observation(dsn, cfg, ref="g1"):
    return ingest.capture_text(dsn, cfg, origin_kind="synthetic", origin_ref=ref,
        request="Rewrite this note.", source="secret source body", facts=dict(GENERAL_FACTS)).observation_id


def test_guards_keep_capture_facts_outcomes_and_evaluations_immutable(cdsn, cfg):
    obs = make_observation(cdsn, cfg)
    with ShadowRunner(cdsn, cfg, FakeOllama(reply_json("general_local", "text")).classifier()) as runner:
        runner.process_one()
    ev = store.append_evaluation(cdsn, obs, kind="reference_label", actor_id="op",
                                 reference_route="general_local", acceptable_routes=["general_local"])
    def blocked(sql, *args):
        with psycopg.connect(cdsn) as conn, pytest.raises(psycopg.errors.RaiseException):
            conn.execute(sql, args)
    blocked("UPDATE classifier_observations SET facts='{}'::jsonb WHERE observation_id=%s", obs)
    blocked("UPDATE classifier_observations SET rec_route='paid' WHERE observation_id=%s", obs)
    blocked("UPDATE classifier_observations SET state='captured' WHERE observation_id=%s", obs)
    blocked("DELETE FROM classifier_observations WHERE observation_id=%s", obs)
    blocked("UPDATE classifier_evaluations SET judgment='changed' WHERE evaluation_id=%s", ev)
    blocked("DELETE FROM classifier_evaluations WHERE evaluation_id=%s", ev)
    corrected = store.append_evaluation(cdsn, obs, kind="correction", actor_id="op",
                                        reference_route="paid", supersedes_id=ev)
    assert corrected != ev


def test_classifier_cohort_is_separate_and_cannot_inflate_production_totals(cdsn, cfg):
    production_query = CohortQuery(origin="observed")
    def metrics():
        return {k: v for k, v in query_metrics(cdsn, production_query).model_dump(mode="json").items()
                if k != "as_of"}
    metrics_before = metrics()
    router_before = query_shadow_cohort(cdsn, RouterCohortFilter(origin="all"))
    counts_before = table_counts(cdsn)
    ids = [make_observation(cdsn, cfg, f"g{i}") for i in range(3)]
    store.append_evaluation(cdsn, ids[0], kind="reference_label", actor_id="ref",
                            reference_route="general_local", acceptable_routes=["general_local"])
    with ShadowRunner(cdsn, cfg, FakeOllama(reply_json("general_local", "text")).classifier()) as runner:
        runner.run(stop=threading.Event(), max_items=2)
    make_observation(cdsn, cfg, "g-late")
    assert metrics() == metrics_before
    assert query_shadow_cohort(cdsn, RouterCohortFilter(origin="all")) == router_before
    assert table_counts(cdsn) == counts_before

    report = query_classifier_cohort(cdsn, ClassifierCohortFilter())
    assert report["available"] and report["counts"]["submitted"] == 4
    assert report["counts"]["completed"] == 2 and report["counts"]["attempted"] == 2
    assert report["counts"]["pending"] == 2 and report["counts"]["unknown_outcome"] == 0
    assert report["recommendations"]["routes"] == {"general_local": 2}
    assert report["reference"]["labelled"] == 1 and report["reference"]["raw_route_acceptable"] == 1
    assert report["actual_route"]["known"] == 0 and report["actual_route"]["unknown"] == 2
    assert report["tokens"]["provenance"] == {"ollama_terminal": 2}
    assert report["latency_ms"]["queue"]["samples"] == 2
    assert report["cost"]["electricity_usd"] is None and report["cost"]["billed_savings"] is None
    assert report["cost"]["local_provider_charge_usd"] == 0
    assert report["filters"]["models"] == ["gemma4:12b"]
    assert query_classifier_cohort(cdsn, ClassifierCohortFilter(origin_kind="natural"))["counts"]["submitted"] == 0
    assert "secret source body" not in str(report)


def test_endpoints_require_operator_auth_and_keep_bodies_behind_content_access(cdsn, cfg):
    obs = make_observation(cdsn, cfg)
    app = create_app(cdsn, producer_tokens={"p": "producer-token"}, operator_token="operator-token",
                     payload_limit_bytes=1_000_000)
    client = TestClient(app)
    operator = {"Authorization": "Bearer operator-token"}
    assert client.get("/api/v1/classifier/cohort").status_code == 401
    assert client.get("/api/v1/classifier/cohort", headers={"Authorization": "Bearer producer-token"}).status_code == 401
    assert client.get(f"/api/v1/classifier/observations/{obs}").status_code == 401
    detail = client.get(f"/api/v1/classifier/observations/{obs}", headers=operator).json()
    assert "secret source body" not in str(detail) and "source_key" not in detail["observation"]
    body = client.get(f"/api/v1/content/{detail['content_refs']['source']}", headers=operator)
    assert body.content == b"secret source body"
    assert client.get("/api/v1/classifier/observations/nope", headers=operator).status_code == 404
    assert client.get("/api/v1/classifier/cohort?origin_kind=bogus", headers=operator).status_code == 422


def test_cohort_links_existing_router_evidence_without_treating_it_as_correctness(cdsn, cfg):
    with psycopg.connect(cdsn) as conn:
        conn.execute("INSERT INTO tasks(task_id,origin,scope,created_at) VALUES ('t-ev','observed','{}',now()) ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,category,created_at) VALUES ('w-ev','t-ev','observed','x',now()) ON CONFLICT DO NOTHING")
        for call, mode in (("c-ev-paid", "production"), ("c-ev-shadow", "shadow")):
            conn.execute("INSERT INTO calls(call_id,work_item_id,created_at,execution_mode) VALUES (%s,'w-ev',now(),%s) ON CONFLICT DO NOTHING", (call, mode))
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,route,eligibility,decided_at,decision_stage,execution_mode) VALUES ('d-ev-p','w-ev','c-ev-paid','paid','eligible',now(),'parent_router','production'),('d-ev-s','w-ev','c-ev-shadow','local','eligible',now(),'parent_router','shadow') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO results(result_id,call_id) VALUES ('r-ev-paid','c-ev-paid'),('r-ev-shadow','c-ev-shadow') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO evaluations(evaluation_id,result_id,evidence,recorded_at) VALUES ('e-ev','r-ev-shadow','{\"state\":\"pass\"}'::jsonb,now()) ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO dispositions(disposition_id,result_id,actor_id,stage,judgment,recorded_at) VALUES ('x-ev','r-ev-paid','parent','claude','accepted',now()) ON CONFLICT DO NOTHING")
    linked = ingest.capture_text(cdsn, cfg, origin_kind="controlled", origin_ref="ev-1", request="Fix it.",
                                 facts=dict(GENERAL_FACTS), work_item_id="w-ev").observation_id
    unlinked = make_observation(cdsn, cfg, "ev-unlinked")
    with ShadowRunner(cdsn, cfg, FakeOllama(reply_json("code_local", "code")).classifier()) as runner:
        runner.run(stop=threading.Event(), until_idle=True, idle_seconds=0.01)
    report = query_classifier_cohort(cdsn, ClassifierCohortFilter())
    evidence = report["router_evidence"]
    assert evidence["completed_with_work_item"] == 1
    assert evidence["qwen_shadow_independent_evaluation"] == {"pass": 1}
    assert evidence["paid_result_dispositions"] == {"accepted": 1}
    assert report["actual_route"]["known"] == 1 and linked and unlinked
    assert report["follow_up"]["with_verification_or_correction"] == 0   # not a correctness judgment
