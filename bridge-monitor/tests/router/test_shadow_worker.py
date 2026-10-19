import hashlib
import uuid

import psycopg
import pytest

from bridge.adapters.base import AdapterResult
from bridge.adapters.qwen_runtime import ModelPreflight
from bridge.code_fix_verifier import VerificationResult
from bridge.mcp_server import BridgeCoordinator
from bridge.router.shadow_worker import run_one_shadow
from bridge.runtime.ownership import claim_dispatch, claim_launch
from bridge.runtime.scheduler import cancel_queued
from bridge.runtime.completions import record_completion
from bridge.runtime.policy_startup import activate_policy
from contracts.policy import validate_policy
from contracts.outcomes import CompletionEnvelope
from contracts.work import ExecutionBinding
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from tests.router.test_ingress import Collector, router_env


class Qwen:
    def __init__(self, answer=None, state="completed"):
        self.answer = answer or '{"answer":"def square(n):\\n    return n * n\\n"}'
        self.state = state
        self.calls = 0

    def preflight(self):
        return ModelPreflight(True, "model_profile_verified", "b" * 64)

    def run(self, lease, prompt, emitter, *, task, deadline_at):
        self.calls += 1
        assert "expected" not in prompt
        return AdapterResult(state=self.state,
            answer=self.answer if self.state == "completed" else None,
            raw_answer=self.answer if self.state == "completed" else None,
            reported_model="qwen-test", uncertainty="agent_status_unavailable"
            if self.state != "completed" else None)


class Verifier:
    def __init__(self, state="pass", reason="tests_passed"):
        self.state, self.reason = state, reason
        self.calls = 0

    def verify(self, source, tests):
        self.calls += 1
        return VerificationResult(self.state, self.reason, 0.2,
            hashlib.sha256(source.encode()).hexdigest())


def _worker(parent, tmp_path, qwen):
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "worker-spool.sqlite", limit_bytes=1000000),
        Collector(parent.dsn), producer_id=f"router-worker-{uuid.uuid4()}", incarnation="boot-worker")
    return BridgeCoordinator(parent.dsn, parent.policy, parent.policy_version, emitter,
        claude_adapter=None, qwen_adapter=qwen, project_root=parent.project_root,
        local_capability={"context_tokens": 10000})


def _queue(router_env):
    parent, _, task, case, receipt = router_env
    routed = parent.route_work(task, case.task_text,
        requested_model="claude-test", reserve_tokens=100,
        output_contract="complete_python_module_v1", verifier_ref=receipt.verifier_ref)
    assert routed["shadow_state"] == "queued"
    return parent, routed, task, case


def test_shadow_worker_verifies_candidate_without_delivery_or_replay(router_env, tmp_path):
    parent, routed, task, _ = _queue(router_env)
    qwen = Qwen()
    worker = _worker(parent, tmp_path, qwen)
    assert claim_dispatch(parent.dsn, pool="qwen",
        incarnation_id=worker.incarnations["qwen"], execution_mode="production") is None
    verifier = Verifier()
    outcome = run_one_shadow(worker, verifier)
    assert outcome["state"] == "completed", outcome
    assert outcome["evaluation_state"] == "pass"
    assert qwen.calls == 1 and verifier.calls == 1
    assert run_one_shadow(worker, verifier) is None
    with psycopg.connect(parent.dsn) as conn:
        result = conn.execute("SELECT r.result_id,r.delivered_at,r.delivered_content_id,"
            "raw.raw_bytes,parsed.raw_bytes FROM results r "
            "JOIN content_objects raw ON raw.content_id=r.raw_content_id "
            "LEFT JOIN result_parts part ON part.result_id=r.result_id "
            "LEFT JOIN content_objects parsed ON parsed.content_id=part.content_id "
            "WHERE r.call_id=%s", (routed["shadow_call_id"],)).fetchone()
        assert result[1] is None and result[2] is None
        assert bytes(result[3]).decode() == qwen.answer
        assert "return n * n" in bytes(result[4]).decode()
        evidence = conn.execute("SELECT evidence FROM evaluations WHERE result_id=%s",
                                (result[0],)).fetchone()[0]
        assert evidence["state"] == "pass" and evidence["reason"] == "tests_passed"
        assert conn.execute("SELECT count(*) FROM events WHERE call_id=%s "
            "AND event_type='result.delivered'", (routed["shadow_call_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM runtime_disposition_obligations "
            "WHERE result_id=%s", (result[0],)).fetchone()[0] == 0


def test_valid_json_wrong_code_is_a_failed_undelivered_result(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    worker = _worker(parent, tmp_path, Qwen(answer='{"answer":"def square(n):\\n    return n + 1\\n"}'))
    outcome = run_one_shadow(worker, Verifier(state="fail", reason="tests_failed"))
    assert outcome["evaluation_state"] == "fail"
    with psycopg.connect(parent.dsn) as conn:
        row = conn.execute("SELECT r.delivered_at,e.evidence->>'reason' FROM results r "
            "JOIN evaluations e ON e.result_id=r.result_id WHERE r.call_id=%s",
            (routed["shadow_call_id"],)).fetchone()
    assert row == (None, "tests_failed")


def test_fenced_json_is_verified_with_raw_and_format_audit(router_env, tmp_path):
    parent, _, task, case, receipt = router_env
    proposed = parent.policy.model_dump(exclude={"version", "source_hash"})
    proposed["router"]["prompt_version"] = "complete-module-envelope-v2"
    caps = {"claude-test": {"context_tokens": 10000}}
    digest = validate_policy(proposed, caps).snapshot.source_hash
    activate_policy(parent.dsn, proposed, caps,
        approved_hash=digest, operator_id="router-test-v2")
    parent._refresh_policy()
    routed = parent.route_work(task, case.task_text,
        requested_model="claude-test", reserve_tokens=100,
        output_contract="complete_python_module_v1", verifier_ref=receipt.verifier_ref)
    assert routed["shadow_state"] == "queued"
    raw = '```json\r\n{"answer":"def square(n):\\n    return n * n\\n"}\r\n```\r\n'
    verifier = Verifier()
    outcome = run_one_shadow(_worker(parent, tmp_path, Qwen(answer=raw)), verifier)
    assert outcome["evaluation_state"] == "pass"
    assert verifier.calls == 1
    with psycopg.connect(parent.dsn) as conn:
        row = conn.execute("SELECT r.delivered_at,raw.raw_bytes,parsed.raw_bytes,e.evidence "
            "FROM results r JOIN content_objects raw ON raw.content_id=r.raw_content_id "
            "JOIN result_parts part ON part.result_id=r.result_id "
            "JOIN content_objects parsed ON parsed.content_id=part.content_id "
            "JOIN evaluations e ON e.result_id=r.result_id WHERE r.call_id=%s",
            (routed["shadow_call_id"],)).fetchone()
    assert row[0] is None
    assert bytes(row[1]).decode() == raw
    assert bytes(row[2]).decode() == "def square(n):\n    return n * n\n"
    assert row[3]["parser_version"] == "json-envelope-v2"
    assert row[3]["output_envelope"] == "fenced_json"


def test_shadow_result_not_completed_if_evaluation_cannot_commit(router_env, tmp_path,
                                                                  monkeypatch):
    parent, routed, _, _ = _queue(router_env)
    worker = _worker(parent, tmp_path, Qwen())
    def fail_persistence(*args, **kwargs):
        raise RuntimeError("database write unavailable")
    monkeypatch.setattr("bridge.router.shadow_worker.record_shadow_completion", fail_persistence)
    outcome = run_one_shadow(worker, Verifier())
    assert outcome["state"] == "uncertain"
    with psycopg.connect(parent.dsn) as conn:
        row = conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
            (routed["shadow_call_id"],)).fetchone()
        assert row == ("uncertain", True)
        assert conn.execute("SELECT count(*) FROM results WHERE call_id=%s",
            (routed["shadow_call_id"],)).fetchone()[0] == 0


def test_malformed_output_does_not_run_verifier(router_env, tmp_path):
    parent, _, _, _ = _queue(router_env)
    verifier = Verifier()
    outcome = run_one_shadow(_worker(parent, tmp_path, Qwen(answer='{"answer":42}')), verifier)
    assert outcome["evaluation_state"] == "fail"
    assert outcome["reason"] == "output_format_invalid"
    assert verifier.calls == 0


def test_uncertain_remote_job_holds_capacity_and_is_not_replayed(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    qwen = Qwen(state="uncertain")
    worker = _worker(parent, tmp_path, qwen)
    outcome = run_one_shadow(worker, Verifier())
    assert outcome["state"] == "uncertain"
    assert run_one_shadow(_worker(parent, tmp_path / "restart", qwen), Verifier()) is None
    assert qwen.calls == 1
    with psycopg.connect(parent.dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (routed["shadow_call_id"],)).fetchone() == ("uncertain", True)


def test_restart_after_launch_claim_never_sends_same_shadow_call(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    qwen = Qwen()
    worker = _worker(parent, tmp_path, qwen)
    lease = claim_dispatch(parent.dsn, pool="qwen",
        incarnation_id=worker.incarnations["qwen"], execution_mode="shadow")
    assert lease.call_id == routed["shadow_call_id"]
    assert claim_launch(parent.dsn, lease)
    assert run_one_shadow(_worker(parent, tmp_path / "restart", qwen), Verifier()) is None
    assert qwen.calls == 0


def test_own_unlaunched_shadow_lease_is_recovered_without_second_claim(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    qwen = Qwen()
    worker = _worker(parent, tmp_path, qwen)
    lease = claim_dispatch(parent.dsn, pool="qwen",
        incarnation_id=worker.incarnations["qwen"], execution_mode="shadow")
    assert lease.call_id == routed["shadow_call_id"]
    outcome = run_one_shadow(worker, Verifier())
    assert outcome["evaluation_state"] == "pass"
    assert qwen.calls == 1


def test_withdrawn_case_is_cancelled_before_remote_send(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    ref = router_env[4].verifier_ref
    with psycopg.connect(parent.dsn) as conn:
        original = conn.execute("SELECT policy FROM config_versions WHERE version=%s",
            (parent.policy_version,)).fetchone()[0]
        conn.execute("UPDATE config_versions SET policy=jsonb_set(policy, "
            "'{router,withdrawn_refs}', %s::jsonb) WHERE version=%s",
            ('["' + ref + '"]', parent.policy_version))
    qwen = Qwen()
    try:
        outcome = run_one_shadow(_worker(parent, tmp_path, qwen), Verifier())
    finally:
        from psycopg.types.json import Jsonb
        with psycopg.connect(parent.dsn) as conn:
            conn.execute("UPDATE config_versions SET policy=%s WHERE version=%s",
                (Jsonb(original), parent.policy_version))
    assert outcome["state"] == "cancelled"
    assert outcome["reason"] == "verifier_withdrawn"
    assert qwen.calls == 0
    with psycopg.connect(parent.dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
            (routed["shadow_call_id"],)).fetchone() == ("cancelled", False)


def test_verifier_unavailable_remains_undelivered_and_unknown(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    class OfflineVerifier:
        def verify(self, source, tests):
            raise ConnectionError("daemon offline")
    outcome = run_one_shadow(_worker(parent, tmp_path, Qwen()), OfflineVerifier())
    assert outcome["evaluation_state"] == "uncertain"
    assert outcome["reason"] == "verifier_unavailable:ConnectionError"
    with psycopg.connect(parent.dsn) as conn:
        assert conn.execute("SELECT delivered_at FROM results WHERE call_id=%s",
            (routed["shadow_call_id"],)).fetchone() == (None,)


def test_shadow_completion_api_refuses_delivery(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    worker = _worker(parent, tmp_path, Qwen())
    lease = claim_dispatch(parent.dsn, pool="qwen",
        incarnation_id=worker.incarnations["qwen"], execution_mode="shadow")
    assert claim_launch(parent.dsn, lease)
    attempt_id = f"{lease.call_id}:attempt:1"
    worker.emitter.emit("call.dispatched", call_id=lease.call_id,
        task_id=router_env[2].task_id, work_item_id=router_env[2].work_item_id,
        payload={"destination": "skynet", "execution_mode": "shadow"})
    worker.emitter.emit("attempt.started", call_id=lease.call_id,
        task_id=router_env[2].task_id, work_item_id=router_env[2].work_item_id,
        attempt_id=attempt_id, payload={"attempt_number": 1})
    binding = ExecutionBinding(worker_instance_id=worker.workers["qwen"],
        producer_id=worker.emitter.producer_id,
        producer_incarnation=worker.incarnations["qwen"],
        call_id=lease.call_id, attempt_id=attempt_id)
    envelope = CompletionEnvelope(call_id=lease.call_id, attempt_id=attempt_id,
        result={"result_id": str(uuid.uuid4()), "content_id": str(uuid.uuid4()),
                "delivered": True})
    with pytest.raises(PermissionError, match="shadow result cannot be delivered"):
        record_completion(parent.dsn, envelope, binding, answer_bytes=b"secret candidate")
    with psycopg.connect(parent.dsn) as conn:
        assert conn.execute("SELECT count(*) FROM results WHERE call_id=%s",
            (routed["shadow_call_id"],)).fetchone()[0] == 0


def test_oversize_response_has_specific_failure_reason(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    verifier = Verifier()
    huge = '{"answer":"' + ('a' * (parent.policy.router.max_source_bytes + 1)) + '"}'
    outcome = run_one_shadow(_worker(parent, tmp_path, Qwen(answer=huge)), verifier)
    assert outcome["reason"] == "output_too_large"
    assert verifier.calls == 0
    with psycopg.connect(parent.dsn) as conn:
        assert conn.execute("SELECT delivered_at FROM results WHERE call_id=%s",
            (routed["shadow_call_id"],)).fetchone() == (None,)


def test_disabled_worker_claims_no_shadow_job(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    with psycopg.connect(parent.dsn) as conn:
        original = conn.execute("SELECT policy FROM config_versions WHERE version=%s",
            (parent.policy_version,)).fetchone()[0]
        conn.execute("UPDATE config_versions SET policy=jsonb_set(policy, "
            "'{router,mode}', '\"disabled\"'::jsonb) WHERE version=%s",
            (parent.policy_version,))
    qwen = Qwen()
    try:
        assert run_one_shadow(_worker(parent, tmp_path, qwen), Verifier()) is None
    finally:
        from psycopg.types.json import Jsonb
        with psycopg.connect(parent.dsn) as conn:
            conn.execute("UPDATE config_versions SET policy=%s WHERE version=%s",
                (Jsonb(original), parent.policy_version))
    assert qwen.calls == 0
    with psycopg.connect(parent.dsn) as conn:
        assert conn.execute("SELECT state FROM runtime_jobs WHERE call_id=%s",
            (routed["shadow_call_id"],)).fetchone() == ("queued",)


def test_transient_database_failure_before_dispatch_does_not_stop_worker(
        router_env, tmp_path, monkeypatch):
    parent, _, _, _ = _queue(router_env)
    worker = _worker(parent, tmp_path, Qwen())
    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError("No buffer space available")
    monkeypatch.setattr("bridge.router.shadow_worker.claim_dispatch", unavailable)
    outcome = run_one_shadow(worker, Verifier())
    assert outcome == {"state": "unavailable",
                       "reason": "database_unavailable_before_claim"}


def test_transient_database_failure_during_policy_refresh_is_unavailable(
        router_env, tmp_path, monkeypatch):
    parent, routed, _, _ = _queue(router_env)
    worker = _worker(parent, tmp_path, Qwen())
    def unavailable():
        raise psycopg.OperationalError("No buffer space available")
    monkeypatch.setattr(worker, "_refresh_policy", unavailable)
    outcome = run_one_shadow(worker, Verifier())
    assert outcome == {"state": "unavailable",
                       "reason": "database_unavailable_before_claim"}
    with psycopg.connect(parent.dsn) as conn:
        assert conn.execute("SELECT state FROM runtime_jobs WHERE call_id=%s",
            (routed["shadow_call_id"],)).fetchone() == ("queued",)


def test_transient_database_failure_after_claim_aborts_before_model_send(
        router_env, tmp_path, monkeypatch):
    parent, routed, _, _ = _queue(router_env)
    qwen = Qwen()
    worker = _worker(parent, tmp_path, qwen)
    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError("No buffer space available")
    monkeypatch.setattr(worker, "_load_job", unavailable)
    outcome = run_one_shadow(worker, Verifier())
    assert outcome == {"state": "cancelled", "call_id": routed["shadow_call_id"],
                       "reason": "database_unavailable_before_launch"}
    assert qwen.calls == 0
    with psycopg.connect(parent.dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
            (routed["shadow_call_id"],)).fetchone() == ("cancelled", False)


def test_cancelled_queue_does_not_send_shadow_job(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    assert cancel_queued(parent.dsn, routed["shadow_call_id"])
    qwen = Qwen()
    assert run_one_shadow(_worker(parent, tmp_path, qwen), Verifier()) is None
    assert qwen.calls == 0


def test_capture_gap_before_send_holds_slot_without_model_call(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    qwen = Qwen()
    worker = _worker(parent, tmp_path, qwen)
    emitted = worker.emitter.emit
    def gap(event_type, *args, **kwargs):
        if str(event_type) == "EventType.CALL_DISPATCHED" or event_type == "call.dispatched":
            return False
        return emitted(event_type, *args, **kwargs)
    worker.emitter.emit = gap
    outcome = run_one_shadow(worker, Verifier())
    assert outcome["reason"] == "capture_gap_before_send"
    assert qwen.calls == 0
    with psycopg.connect(parent.dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
            (routed["shadow_call_id"],)).fetchone() == ("uncertain", True)


def test_shadow_timeout_is_unknown_and_does_not_replay(router_env, tmp_path):
    parent, routed, _, _ = _queue(router_env)
    from bridge.adapters.base import AdapterResult
    class Timeout(Qwen):
        def run(self, *args, **kwargs):
            self.calls += 1
            return AdapterResult(state="uncertain", uncertainty="local_execution_deadline")
    qwen = Timeout()
    worker = _worker(parent, tmp_path, qwen)
    outcome = run_one_shadow(worker, Verifier())
    assert outcome["reason"] == "local_execution_deadline"
    assert run_one_shadow(_worker(parent, tmp_path / "after-timeout", qwen), Verifier()) is None
    assert qwen.calls == 1
    with psycopg.connect(parent.dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
            (routed["shadow_call_id"],)).fetchone() == ("uncertain", True)
