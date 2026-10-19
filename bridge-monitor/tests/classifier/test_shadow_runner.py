"""Capture, bounded queueing, single-flight inference and fail-safe behavior (real Postgres)."""

import json
import os
import subprocess
import sys
import threading
from datetime import timedelta
from pathlib import Path

import psycopg
import psycopg.rows
import pytest

from bridge.classifier import ingest, store
from bridge.classifier.config import ClassifierShadowConfig
from bridge.classifier.runner import RunnerBusy, ShadowRunner
from tests.classifier.conftest import GENERAL_FACTS, table_counts
from tests.classifier.fakes import FakeOllama, reply_json


def submit(dsn, cfg, ref="case-1", request="Count the retries per request id.", source="", *,
           facts=None, replay_id=None, kind="synthetic", work_item_id=None):
    return ingest.capture_text(dsn, cfg, origin_kind=kind, origin_ref=ref, request=request,
        source=source, facts=dict(facts or GENERAL_FACTS), replay_id=replay_id,
        work_item_id=work_item_id)


def runner_for(dsn, cfg, fake):
    return ShadowRunner(dsn, cfg, fake.classifier())


def row(dsn, observation_id):
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        return conn.execute("SELECT * FROM classifier_observations WHERE observation_id=%s",
                            (observation_id,)).fetchone()


def test_disabled_feature_captures_nothing_and_never_calls_the_model(cdsn):
    off = ClassifierShadowConfig(enabled=False)
    fake = FakeOllama()
    before = table_counts(cdsn)
    assert submit(cdsn, off).state == "disabled"
    with pytest.raises(RuntimeError):
        ShadowRunner(cdsn, off, fake.classifier()).start()
    with psycopg.connect(cdsn) as conn:
        assert conn.execute("SELECT count(*) FROM classifier_observations").fetchone()[0] == 0
    assert fake.requests == [] and table_counts(cdsn) == before


def test_capture_records_hashes_facts_and_protected_content_before_any_inference(cdsn, cfg):
    result = submit(cdsn, cfg, source="def f(): pass", work_item_id="w-1")
    stored = row(cdsn, result.observation_id)
    assert stored["state"] == "captured" and stored["attempts"] == 0
    assert stored["facts"] == GENERAL_FACTS and stored["origin_kind"] == "synthetic"
    assert stored["rubric_version"] == "router-rubric-v1" and stored["profile_id"] == "gemma-router-profile-v1"
    assert stored["requested_model"] == "gemma4:12b" and len(stored["request_sha256"]) == 64
    with psycopg.connect(cdsn) as conn:
        body = conn.execute("SELECT raw_bytes FROM content_objects WHERE content_id=%s",
                            (stored["request_content_id"],)).fetchone()[0]
    assert bytes(body) == b"Count the retries per request id."
    assert "retries" not in json.dumps({k: str(v) for k, v in stored.items() if k != "request_content_id"})


def test_duplicate_capture_is_idempotent_and_rerun_needs_explicit_replay_identity(cdsn, cfg):
    first = submit(cdsn, cfg)
    again = submit(cdsn, cfg)
    assert again.state == "duplicate" and again.observation_id == first.observation_id
    rerun = submit(cdsn, cfg, replay_id="rerun-2")
    assert rerun.state == "captured" and rerun.observation_id != first.observation_id


def test_full_queue_records_backpressure_instead_of_blocking(cdsn, cfg):
    for index in range(3):
        assert submit(cdsn, cfg, ref=f"c{index}").state == "captured"
    overflow = submit(cdsn, cfg, ref="c-over")
    assert overflow.state == "skipped" and overflow.skip_reason == "queue_full"
    assert row(cdsn, overflow.observation_id)["state"] == "skipped"


def test_oversized_input_is_skipped_not_truncated(cdsn, cfg):
    small = ClassifierShadowConfig(enabled=True, max_input_bytes=300)
    result = submit(cdsn, small, request="x" * 500)
    stored = row(cdsn, result.observation_id)
    assert (result.state, result.skip_reason) == ("skipped", "input_too_large")
    assert stored["request_content_id"] is None and stored["attempts"] == 0


def test_valid_classification_is_recorded_with_all_evidence(cdsn, cfg):
    obs = submit(cdsn, cfg, kind="natural").observation_id
    fake = FakeOllama(reply_json(route="tools", category="data"))
    with runner_for(cdsn, cfg, fake) as runner:
        result = runner.process_one()
    assert (result.status, result.state) == ("processed", "completed")
    done = row(cdsn, obs)
    assert (done["rec_route"], done["rec_category"], done["valid"]) == ("tools", "data", True)
    assert done["policy_allowed"] is True and done["policy_version"] == "recommendation-policy-v1"
    assert done["reported_model"] == "gemma4:12b" and done["observed_digest"] == fake.digest
    assert done["runtime_version"] == "0.30.9" and done["backend"]["state"] == "gpu_resident"
    assert (done["input_tokens"], done["output_tokens"], done["usage_provenance"]) == (700, 40, "ollama_terminal")
    assert done["load_ms"] == 40 and done["inference_ms"] == 1500
    assert done["dispatched_at"] and done["first_content_at"] and done["completed_at"]
    assert done["queue_ms"] >= 0 and done["end_to_end_ms"] >= done["request_ms"]
    assert done["response_content_id"] and done["actual_route"] is None   # unknown, not guessed
    assert fake.chat_calls == 1


def test_missing_usage_is_labelled_as_an_estimate(cdsn, cfg):
    obs = submit(cdsn, cfg).observation_id
    with runner_for(cdsn, cfg, FakeOllama(mode="no_usage")) as runner:
        runner.process_one()
    done = row(cdsn, obs)
    assert done["usage_provenance"] == "estimated_utf8_bytes_div_4" and done["output_tokens"] is None


def test_unsafe_recommendation_is_kept_raw_and_marked_blocked(cdsn, cfg):
    facts = {"general_available": True, "code_available": False, "paid_allowed": False}
    obs = submit(cdsn, cfg, facts=facts).observation_id
    with runner_for(cdsn, cfg, FakeOllama(reply_json(route="code_local", category="code"))) as runner:
        runner.process_one()
    done = row(cdsn, obs)
    assert done["state"] == "completed" and done["rec_route"] == "code_local"
    assert done["policy_allowed"] is False and "code_worker_unavailable" in done["policy_blocks"]


def test_malformed_output_is_a_failure_with_raw_text_preserved(cdsn, cfg):
    obs = submit(cdsn, cfg).observation_id
    with runner_for(cdsn, cfg, FakeOllama('{"category":"code","route":"tools"}')) as runner:
        runner.process_one()
    done = row(cdsn, obs)
    assert (done["state"], done["valid"], done["validation_error"]) == ("failed", False, "missing_field")
    assert done["rec_route"] is None and done["error_type"] == "contract_invalid"
    with psycopg.connect(cdsn) as conn:
        raw = conn.execute("SELECT raw_bytes FROM content_objects WHERE content_id=%s",
                           (done["response_content_id"],)).fetchone()[0]
    assert bytes(raw) == b'{"category":"code","route":"tools"}'


def test_unavailable_endpoint_claims_nothing_and_inputs_expire_unclassified(cdsn, cfg):
    obs = submit(cdsn, cfg).observation_id
    with runner_for(cdsn, cfg, FakeOllama(mode="down")) as runner:
        result = runner.process_one()
    assert (result.status, result.reason) == ("unavailable", "endpoint_unavailable")
    assert row(cdsn, obs)["state"] == "captured" and row(cdsn, obs)["attempts"] == 0
    late = store.utcnow() + timedelta(seconds=cfg.max_queue_age_seconds + 5)
    assert store.expire_stale(cdsn, cfg, late) == 1
    assert row(cdsn, obs)["skip_reason"] == "expired_unprocessed"


def test_wrong_model_digest_is_never_sent_a_request(cdsn, cfg):
    submit(cdsn, cfg)
    fake = FakeOllama(digest="1" * 64)
    with runner_for(cdsn, cfg, fake) as runner:
        result = runner.process_one()
    assert result.reason == "model_digest_mismatch" and fake.chat_calls == 0


def test_restart_marks_interrupted_dispatch_uncertain_and_never_resends(cdsn, cfg):
    obs = submit(cdsn, cfg).observation_id
    assert store.claim_next(cdsn, "dead-runner", store.utcnow())["observation_id"] == obs
    fake = FakeOllama()
    with runner_for(cdsn, cfg, fake) as runner:      # the new runner recovers on start
        assert runner.process_one().status == "idle"
    done = row(cdsn, obs)
    assert (done["state"], done["error_type"], done["attempts"]) == ("uncertain", "runner_interrupted", 1)
    assert fake.chat_calls == 0
    assert submit(cdsn, cfg).state == "duplicate"     # resubmission does not rerun it


def test_only_one_runner_may_hold_the_lock(cdsn, cfg):
    with runner_for(cdsn, cfg, FakeOllama()):
        with pytest.raises(RunnerBusy):
            runner_for(cdsn, cfg, FakeOllama()).start()


def test_cancelled_inference_is_recorded_not_retried(cdsn, cfg):
    obs = submit(cdsn, cfg).observation_id
    fake = FakeOllama(mode="slow", pause=5.0)
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    with runner_for(cdsn, cfg, fake) as runner:
        result = runner.process_one(cancel)
    assert result.state == "cancelled" and fake.chat_calls == 1
    assert row(cdsn, obs)["rec_route"] is None


def test_timeout_is_uncertain_and_recorded(cdsn):
    short = ClassifierShadowConfig(enabled=True, request_timeout_seconds=0.4)
    obs = submit(cdsn, short).observation_id
    fake = FakeOllama(mode="slow", pause=5.0)
    with ShadowRunner(cdsn, short, fake.classifier(request_timeout=0.4)) as runner:
        runner.process_one()
    done = row(cdsn, obs)
    assert done["state"] == "uncertain" and done["error_type"] == "deadline_exceeded"
    assert fake.chat_calls == 1


def test_cpu_fallback_is_recorded_and_halts_the_runner(cdsn, cfg):
    first = submit(cdsn, cfg, ref="a").observation_id
    second = submit(cdsn, cfg, ref="b").observation_id
    fake = FakeOllama(vram=0)
    with runner_for(cdsn, cfg, fake) as runner:
        assert runner.process_one().state == "completed"
        assert runner.process_one().status == "halted"
    assert row(cdsn, first)["backend"]["state"] == "cpu_only"
    assert row(cdsn, second)["state"] == "captured" and fake.chat_calls == 1


def test_actual_route_is_taken_from_the_unchanged_system_with_provenance(cdsn, cfg):
    with psycopg.connect(cdsn) as conn:
        conn.execute("INSERT INTO tasks(task_id,origin,scope,created_at) VALUES "
                     "('t-cls','observed','{}',now()) ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,category,created_at) "
                     "VALUES ('w-cls','t-cls','observed','single_file_python_fix',now()) "
                     "ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO calls(call_id,work_item_id,created_at) VALUES "
                     "('c-cls','w-cls',now()) ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,route,"
                     "eligibility,decided_at,decision_stage,execution_mode) VALUES "
                     "('d-cls','w-cls','c-cls','paid','ineligible',now(),'parent_router','production') "
                     "ON CONFLICT DO NOTHING")
    obs = submit(cdsn, cfg, work_item_id="w-cls").observation_id
    before = table_counts(cdsn)
    with runner_for(cdsn, cfg, FakeOllama(reply_json(route="code_local", category="code"))) as runner:
        runner.process_one()
    done = row(cdsn, obs)
    assert done["actual_route"] == "paid" and done["rec_route"] == "code_local"
    assert done["actual_route_source"]["decision_id"] == "d-cls"
    assert table_counts(cdsn) == before        # reading the decision changed nothing


def test_classifier_activity_cannot_dispatch_or_alter_anything_else(cdsn, cfg):
    submit(cdsn, cfg, ref="x1")
    submit(cdsn, cfg, ref="x2")
    before = table_counts(cdsn)
    with runner_for(cdsn, cfg, FakeOllama(reply_json(route="code_local", category="code"))) as runner:
        runner.run(stop=threading.Event(), max_items=2)
    with runner_for(cdsn, cfg, FakeOllama(mode="down")) as runner:
        runner.process_one()
    assert table_counts(cdsn) == before


def test_classifier_modules_never_import_dispatch_or_the_live_router():
    code = ("import sys, bridge.classifier.runner, bridge.classifier.ingest, "
            "bridge.classifier.client, bridge.classifier.replay; "
            "bad = [m for m in sys.modules if m in ('bridge.mcp_server', 'bridge.runtime.scheduler', "
            "'bridge.runtime.admission', 'bridge.adapters.qwen_agent', 'bridge.adapters.qwen_runtime', "
            "'bridge.adapters.claude_cli', 'bridge.router.ingress', 'bridge.router.shadow_worker', "
            "'bridge.runtime.fallback')]; print(bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(Path(store.__file__).parents[2]),
                         env={**os.environ, "PYTHONPATH": "."})
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


def test_live_router_does_not_reference_the_classifier():
    root = Path(store.__file__).parents[2]
    for name in ("bridge/mcp_server.py", "bridge/router/ingress.py", "bridge/router/eligibility.py",
                 "bridge/router/shadow_worker.py", "bridge/runtime/scheduler.py",
                 "bridge/runtime/admission.py"):
        assert "classifier" not in (root / name).read_text(encoding="utf-8"), name
