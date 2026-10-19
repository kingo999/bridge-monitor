"""Optional actual-route enrichment must never cost the classification, and never store 'unknown'."""

import psycopg
import psycopg.rows
import pytest

from bridge.classifier import ingest, store
from bridge.classifier.runner import ShadowRunner
from tests.classifier.conftest import GENERAL_FACTS, table_counts
from tests.classifier.fakes import FakeOllama, reply_json


def seed_decision(dsn, tag, route):
    """A production parent-router decision for work item ``w-<tag>`` (route None: no decision)."""
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO tasks(task_id,origin,scope,created_at) VALUES (%s,'observed','{}',now()) "
                     "ON CONFLICT DO NOTHING", (f"t-{tag}",))
        conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,category,created_at) VALUES "
                     "(%s,%s,'observed','x',now()) ON CONFLICT DO NOTHING", (f"w-{tag}", f"t-{tag}"))
        conn.execute("INSERT INTO calls(call_id,work_item_id,created_at) VALUES (%s,%s,now()) "
                     "ON CONFLICT DO NOTHING", (f"c-{tag}", f"w-{tag}"))
        if route is not None:
            conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,route,"
                         "eligibility,decided_at,decision_stage,execution_mode) VALUES "
                         "(%s,%s,%s,%s,'eligible',now(),'parent_router','production') ON CONFLICT DO NOTHING",
                         (f"d-{tag}", f"w-{tag}", f"c-{tag}", route))
    return f"w-{tag}"


def observe(dsn, cfg, tag, text, *, ref=None):
    return ingest.capture_text(dsn, cfg, origin_kind="controlled", origin_ref=ref or tag,
        request="Fix it.", facts=dict(GENERAL_FACTS), work_item_id=f"w-{tag}").observation_id


def row(dsn, observation_id):
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        return conn.execute("SELECT * FROM classifier_observations WHERE observation_id=%s",
                            (observation_id,)).fetchone()


def raw_response(dsn, content_id):
    with psycopg.connect(dsn) as conn:
        return bytes(conn.execute("SELECT raw_bytes FROM content_objects WHERE content_id=%s",
                                  (content_id,)).fetchone()[0])


@pytest.mark.parametrize("failure", [psycopg.OperationalError("connection lost"),
                                     psycopg.errors.QueryCanceled("statement timeout"),
                                     RuntimeError("unexpected")])
def test_lookup_failure_keeps_the_response_verdict_usage_and_sends_once(cdsn, cfg, monkeypatch, failure):
    seed_decision(cdsn, "lf", "paid")
    obs = observe(cdsn, cfg, "lf", None)
    def broken(*args, **kwargs):
        raise failure
    monkeypatch.setattr(store, "actual_route_for", broken)
    fake = FakeOllama(reply_json("code_local", "code"))
    with ShadowRunner(cdsn, cfg, fake.classifier()) as runner:
        result = runner.process_one()
        assert runner.process_one().status == "idle"             # nothing left to resend
    done = row(cdsn, obs)
    assert (result.state, done["state"], done["valid"]) == ("completed", "completed", True)
    assert (done["rec_category"], done["rec_route"]) == ("code", "code_local")
    assert done["policy_allowed"] is True and done["policy_version"] == "recommendation-policy-v1"
    assert (done["input_tokens"], done["output_tokens"], done["usage_provenance"]) == (700, 40, "ollama_terminal")
    assert done["error_type"] is None and done["attempts"] == 1
    assert raw_response(cdsn, done["response_content_id"]).decode() == reply_json("code_local", "code")
    assert done["actual_route"] is None and done["actual_route_observed_at"] is None
    assert done["actual_route_source"] == {"status": "lookup_failed", "error_type": type(failure).__name__}
    assert fake.chat_calls == 1


def test_lookup_failure_does_not_disturb_a_contract_failure_outcome(cdsn, cfg, monkeypatch):
    seed_decision(cdsn, "lg", "paid")
    obs = observe(cdsn, cfg, "lg", None)
    monkeypatch.setattr(store, "actual_route_for",
                        lambda *a, **k: (_ for _ in ()).throw(psycopg.OperationalError("down")))
    fake = FakeOllama('{"category":"code","route":"tools"}')
    with ShadowRunner(cdsn, cfg, fake.classifier()) as runner:
        runner.process_one()
    done = row(cdsn, obs)
    assert (done["state"], done["error_type"], done["validation_error"]) == ("failed", "contract_invalid", "missing_field")
    assert raw_response(cdsn, done["response_content_id"]) == b'{"category":"code","route":"tools"}'
    assert done["actual_route"] is None and fake.chat_calls == 1


def test_lookup_failure_is_recoverable_later_by_filling_the_empty_route(cdsn, cfg, monkeypatch):
    seed_decision(cdsn, "lr", "paid")
    obs = observe(cdsn, cfg, "lr", None)
    monkeypatch.setattr(store, "actual_route_for",
                        lambda *a, **k: (_ for _ in ()).throw(psycopg.OperationalError("down")))
    with ShadowRunner(cdsn, cfg, FakeOllama().classifier()) as runner:
        runner.process_one()
    before = row(cdsn, obs)
    with psycopg.connect(cdsn) as conn:
        conn.execute("UPDATE classifier_observations SET actual_route='paid' WHERE observation_id=%s", (obs,))
    after = row(cdsn, obs)
    assert after["actual_route"] == "paid"
    for column in ("state", "rec_route", "rec_category", "valid", "policy_allowed", "response_sha256",
                   "input_tokens", "output_tokens", "attempts"):
        assert after[column] == before[column]


def test_termination_signals_are_not_swallowed_by_the_lookup_guard(cdsn, cfg, monkeypatch):
    seed_decision(cdsn, "lk", "paid")
    observe(cdsn, cfg, "lk", None)
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(store, "actual_route_for", interrupted)
    with ShadowRunner(cdsn, cfg, FakeOllama().classifier()) as runner:
        with pytest.raises(KeyboardInterrupt):
            runner.process_one()


@pytest.mark.parametrize("tag,route", [("nd", None), ("pe", "pending"), ("un", "some-future-route")])
def test_missing_pending_or_unmapped_routes_are_stored_as_sql_null(cdsn, cfg, tag, route):
    seed_decision(cdsn, tag, route)
    obs = observe(cdsn, cfg, tag, None)
    with ShadowRunner(cdsn, cfg, FakeOllama().classifier()) as runner:
        runner.process_one()
    done = row(cdsn, obs)
    assert done["state"] == "completed" and done["actual_route"] is None
    if route is None:
        assert done["actual_route_source"] is None and done["actual_route_observed_at"] is None
    else:
        assert done["actual_route_source"]["status"] == "unmapped_route"
        assert done["actual_route_source"]["project_route"] == route
        assert done["actual_route_source"]["decision_id"] == f"d-{tag}"
    # the empty value remains fillable once a mapped decision exists
    with psycopg.connect(cdsn) as conn:
        conn.execute("UPDATE classifier_observations SET actual_route='paid' WHERE observation_id=%s", (obs,))
    assert row(cdsn, obs)["actual_route"] == "paid"


def test_mapped_routes_are_still_recorded_with_provenance(cdsn, cfg):
    for tag, route, mapped in (("mp", "paid", "paid"), ("ml", "local", "code_local")):
        seed_decision(cdsn, tag, route)
        obs = observe(cdsn, cfg, tag, None)
        with ShadowRunner(cdsn, cfg, FakeOllama().classifier()) as runner:
            runner.process_one()
        done = row(cdsn, obs)
        assert done["actual_route"] == mapped and done["actual_route_observed_at"] is not None
        assert done["actual_route_source"]["decision_id"] == f"d-{tag}"
        assert "status" not in done["actual_route_source"]


def test_existing_non_null_history_cannot_be_rewritten_including_a_historical_unknown(cdsn, cfg):
    seed_decision(cdsn, "hi", "paid")
    obs = observe(cdsn, cfg, "hi", None)
    with ShadowRunner(cdsn, cfg, FakeOllama().classifier()) as runner:
        runner.process_one()
    with psycopg.connect(cdsn) as conn:       # a record written by the earlier runner behavior
        conn.execute("ALTER TABLE classifier_observations DISABLE TRIGGER classifier_observations_guard_trigger")
        conn.execute("UPDATE classifier_observations SET actual_route='unknown' WHERE observation_id=%s", (obs,))
        conn.execute("ALTER TABLE classifier_observations ENABLE TRIGGER classifier_observations_guard_trigger")
    for attempted in ("paid", "code_local"):
        with psycopg.connect(cdsn) as conn, pytest.raises(psycopg.errors.RaiseException):
            conn.execute("UPDATE classifier_observations SET actual_route=%s WHERE observation_id=%s",
                         (attempted, obs))
    assert row(cdsn, obs)["actual_route"] == "unknown"      # untouched; correction is a later evaluation


def test_route_enrichment_failures_change_no_production_table(cdsn, cfg, monkeypatch):
    seed_decision(cdsn, "pt", "paid")
    observe(cdsn, cfg, "pt", None)
    before = table_counts(cdsn)
    monkeypatch.setattr(store, "actual_route_for",
                        lambda *a, **k: (_ for _ in ()).throw(psycopg.OperationalError("down")))
    with ShadowRunner(cdsn, cfg, FakeOllama().classifier()) as runner:
        runner.process_one()
    assert table_counts(cdsn) == before
