import uuid
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from bridge.adapters.base import AdapterResult
from bridge.adapters.qwen_runtime import ModelPreflight
from bridge.mcp_server import BridgeCoordinator
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from contracts.work import TaskPacket
from contracts.events import EventType
from contracts.outcomes import CompletionEnvelope
from bridge.runtime.policy_startup import activate_policy
from contracts.policy import validate_policy
from service.ingest.store import ingest_batch
from tests.integration.test_ownership import _setup
from tests.integration.test_admission import _policy


class Collector:
    def __init__(self, dsn): self.dsn = dsn
    def ingest_batch(self, producer, events, content):
        return ingest_batch(self.dsn, producer, events, content)


class FakeClaude:
    def run(self, lease, task, prompt, emitter):
        assert prompt == "Exact GPT prompt"
        return AdapterResult(state="completed", answer="Claude answer", reported_model="claude-test")


def test_queued_paid_call_dispatches_when_pilot_slot_reopens(runtime_dsn, tmp_path):
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"bridge-{uuid.uuid4()}", incarnation="boot-1")
    started, release = threading.Event(), threading.Event()

    class BlockingClaude:
        def run(self, lease, task, prompt, sink):
            if prompt == "first":
                started.set()
                assert release.wait(10)
            return AdapterResult(state="completed", answer=f"answer:{prompt}",
                                 reported_model="claude-test")

    service = BridgeCoordinator(dsn, policy, version, emitter,
                                claude_adapter=BlockingClaude())
    def task():
        return TaskPacket(task_id=f"task-{uuid.uuid4()}",
            work_item_id=f"work-{uuid.uuid4()}", category="analysis", scope=["read"],
            token_budget=1000, call_budget=2)

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(service.delegate_claude, task(), "first",
            requested_model="claude-test", reserve_tokens=100)
        assert started.wait(5)
        second = workers.submit(service.delegate_claude, task(), "second",
            requested_model="claude-test", reserve_tokens=100)
        deadline = time.monotonic() + 5
        queued = False
        while time.monotonic() < deadline:
            with psycopg.connect(dsn) as conn:
                queued = conn.execute("SELECT count(*) FROM runtime_jobs WHERE pool='claude' "
                    "AND state='queued'").fetchone()[0] == 1
            if queued:
                break
            time.sleep(0.02)
        assert queued and not second.done()
        release.set()
        assert first.result(timeout=10)["answer"] == "answer:first"
        assert second.result(timeout=10)["answer"] == "answer:second"


def test_paid_queue_expiry_releases_reservation_without_launch(runtime_dsn, tmp_path):
    dsn = runtime_dsn
    policy = _policy()
    policy = policy.model_copy(update={"deployment": policy.deployment.model_copy(update={
        "queue_deadline_seconds": 1, "queue_poll_ms": 20})})
    proposed = policy.model_dump()
    capabilities = {"claude-test": {"context_tokens": 10000}}
    digest = validate_policy(proposed, capabilities).snapshot.source_hash
    version = activate_policy(dsn, proposed, capabilities,
        approved_hash=digest, operator_id="operator-1").version
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"bridge-{uuid.uuid4()}", incarnation="boot-1")
    started, release = threading.Event(), threading.Event()
    launches = []

    class BlockingClaude:
        def run(self, lease, task, prompt, sink):
            launches.append(prompt)
            if prompt == "first":
                started.set()
                assert release.wait(10)
            return AdapterResult(state="completed", answer=f"answer:{prompt}")

    service = BridgeCoordinator(dsn, policy, version, emitter,
                                claude_adapter=BlockingClaude())
    def task():
        return TaskPacket(task_id=f"task-{uuid.uuid4()}",
            work_item_id=f"work-{uuid.uuid4()}", category="analysis", scope=["read"],
            token_budget=1000, call_budget=2)

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(service.delegate_claude, task(), "first",
            requested_model="claude-test", reserve_tokens=100)
        assert started.wait(5)
        second = workers.submit(service.delegate_claude, task(), "second",
            requested_model="claude-test", reserve_tokens=100)
        deadline = time.monotonic() + 5
        child_id = None
        while time.monotonic() < deadline:
            with psycopg.connect(dsn) as conn:
                row = conn.execute("SELECT call_id FROM runtime_jobs WHERE pool='claude' "
                    "AND state='queued'").fetchone()
            if row:
                child_id = row[0]
                break
            time.sleep(0.02)
        assert child_id
        outcome = second.result(timeout=10)
        assert outcome["state"] == "queue_expired"
        assert launches == ["first"]
        with psycopg.connect(dsn) as conn:
            assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                                (child_id,)).fetchone() == ("cancelled", False)
        release.set()
        assert first.result(timeout=10)["state"] == "completed"


def test_bridge_operation_captures_prompt_and_delivered_answer(runtime_dsn, tmp_path):
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"bridge-{uuid.uuid4()}", incarnation="boot-1")
    service = BridgeCoordinator(dsn, policy, version, emitter,
                                claude_adapter=FakeClaude(), qwen_adapter=None)
    task = TaskPacket(task_id=f"task-{uuid.uuid4()}", work_item_id=f"work-{uuid.uuid4()}",
                      category="analysis", scope=["read"], token_budget=1000, call_budget=2)
    outcome = service.delegate_claude(task, "Exact GPT prompt", requested_model="claude-test",
                                      reserve_tokens=100)
    assert outcome["state"] == "completed" and outcome["answer"] == "Claude answer"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (outcome["call_id"],)).fetchone() == ("completed", False)
        assert conn.execute("SELECT count(*) FROM events WHERE call_id=%s "
                            "AND event_type='result.delivered'", (outcome["call_id"],)).fetchone()[0] == 1


def test_structured_claude_reply_retains_raw_and_delivered_bodies(runtime_dsn, tmp_path):
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"bridge-{uuid.uuid4()}", incarnation="boot-1")

    class StructuredClaude:
        def run(self, lease, task, prompt, sink):
            raw = '{"answer":"Useful answer","local_results":[],"work_resolution":"resolved"}'
            return AdapterResult(state="completed", answer="Useful answer", raw_answer=raw,
                                 raw_content_id=str(uuid.uuid4()), completion_claim={
                                     "answer": "Useful answer", "local_results": [],
                                     "work_resolution": "resolved"})

    service = BridgeCoordinator(dsn, policy, version, emitter,
                                claude_adapter=StructuredClaude())
    task = TaskPacket(task_id=f"task-{uuid.uuid4()}", work_item_id=f"work-{uuid.uuid4()}",
                      category="analysis", scope=["read"], token_budget=1000, call_budget=2)
    outcome = service.delegate_claude(task, "Exact GPT prompt", requested_model="claude-test",
                                      reserve_tokens=100)
    assert outcome["state"] == "completed"
    with psycopg.connect(dsn) as conn:
        raw, delivered = conn.execute("SELECT r.raw_content_id,r.delivered_content_id "
            "FROM results r WHERE r.result_id=%s", (outcome["result_id"],)).fetchone()
        assert raw != delivered
        bodies = conn.execute("SELECT content_id,raw_bytes FROM content_objects "
            "WHERE content_id IN (%s,%s)", (raw, delivered)).fetchall()
        assert dict(bodies) == {raw: b'{"answer":"Useful answer","local_results":[],"work_resolution":"resolved"}',
                                delivered: b"Useful answer"}
        assert conn.execute("SELECT outcome,final_result_id FROM work_resolutions "
            "WHERE work_item_id=%s", (task.work_item_id,)).fetchone() == (
            "paid_direct", outcome["result_id"])
    later = CompletionEnvelope.model_validate({"call_id": outcome["call_id"],
        "attempt_id": f"{outcome['call_id']}:attempt:1",
        "result": {"result_id": outcome["result_id"], "content_id": delivered,
                   "delivered": True}})
    assert service.complete_work(outcome["call_id"], later)["accounting_complete"]


def test_invalid_claude_claim_is_diagnostic_without_losing_answer(runtime_dsn, tmp_path):
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"bridge-{uuid.uuid4()}", incarnation="boot-1")

    class InvalidClaimClaude:
        def run(self, lease, task, prompt, sink):
            return AdapterResult(state="completed", answer="Useful answer",
                completion_claim={"local_results": [{"result_id": "bad", "judgment": "invalid"}],
                                  "work_resolution": "not-a-resolution"})

    service = BridgeCoordinator(dsn, policy, version, emitter,
                                claude_adapter=InvalidClaimClaude())
    task = TaskPacket(task_id=f"task-{uuid.uuid4()}", work_item_id=f"work-{uuid.uuid4()}",
                      category="analysis", scope=["read"], token_budget=1000, call_budget=2)
    outcome = service.delegate_claude(task, "Exact GPT prompt", requested_model="claude-test",
                                      reserve_tokens=100)
    assert outcome["state"] == "completed" and outcome["answer"] == "Useful answer"
    assert outcome["accounting_complete"] is False
    with psycopg.connect(dsn) as conn:
        reasons = [row[0] for row in conn.execute(
            "SELECT reason FROM runtime_completion_diagnostics WHERE call_id=%s",
            (outcome["call_id"],)).fetchall()]
    assert any("local_results[0] invalid" in reason for reason in reasons)
    assert "invalid work_resolution" in reasons


@pytest.mark.parametrize("verified,timeout,auto_claim,drift,paid_timeout_verified,cutoff_before_post,aged_client", [
    (True, False, False, False, True, False, False),
    (True, False, True, False, True, False, False),
    (False, False, False, False, True, False, False),
    (True, True, False, False, True, False, False),
    (True, False, False, True, True, False, False),
    (True, False, False, False, False, False, False),
    (True, False, False, False, True, True, False),
    (True, False, False, False, True, False, True)])
def test_claude_can_delegate_scoped_local_check_and_acknowledge_result(runtime_dsn, tmp_path,
                                                                       verified, timeout,
                                                                       auto_claim, drift,
                                                                       paid_timeout_verified,
                                                                       cutoff_before_post,
                                                                       aged_client):
    dsn = runtime_dsn
    base, _ = _setup(dsn)
    policy = base.model_copy(update={"deployment": base.deployment.model_copy(update={
        "qwen_model_profile": "qwen-test", "qwen_model_digest": "a" * 64,
        "qwen_output_tokens": 20,
        "client_timeout_seconds": 500 if timeout else base.deployment.client_timeout_seconds})})
    proposed = policy.model_dump()
    capabilities = {"claude-test": {"context_tokens": 10000}}
    digest = validate_policy(proposed, capabilities).snapshot.source_hash
    version = activate_policy(dsn, proposed, capabilities,
        approved_hash=digest, operator_id="operator-1").version
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"bridge-{uuid.uuid4()}", incarnation="boot-1")

    class FakeQwen:
        model = "qwen-test"
        expected_digest = "a" * 64
        checks = 0
        def preflight(self):
            self.checks += 1
            current = verified and not (drift and self.checks > 1)
            return ModelPreflight(current,
                "model_profile_verified" if current else "model_digest_mismatch",
                "a" * 64 if current else "b" * 64)
        def run(self, lease, prompt, sink, task=None, deadline_at=None):
            assert not drift, "model drift must prevent the local POST"
            assert prompt == "Check this Java method"
            assert deadline_at is not None and deadline_at.tzinfo is not None
            if cutoff_before_post:
                return AdapterResult(state="denied", uncertainty="local_cutoff_passed")
            sink.emit(EventType.MODEL_SELECTED, call_id=lease.call_id,
                attempt_id=f"{lease.call_id}:attempt:1",
                model_execution_id=f"{lease.call_id}:model:1", requested_model="qwen-test")
            sink.emit(EventType.MODEL_REPORTED, call_id=lease.call_id,
                attempt_id=f"{lease.call_id}:attempt:1",
                model_execution_id=f"{lease.call_id}:model:1", reported_model="qwen-test")
            if timeout:
                return AdapterResult(state="uncertain", uncertainty="request_may_have_run")
            return AdapterResult(state="completed", answer="Possible null dereference",
                                 reported_model="qwen-test")

    class DelegatingClaude:
        local = None
        def run(self, lease, task, prompt, sink):
            if aged_client:
                with psycopg.connect(dsn) as conn:
                    conn.execute("UPDATE runtime_jobs SET admitted_at=now()-interval '86 seconds' "
                                 "WHERE call_id=%s", (lease.call_id,))
            sink.emit(EventType.MODEL_SELECTED, call_id=lease.call_id,
                attempt_id=f"{lease.call_id}:attempt:1",
                model_execution_id=f"{lease.call_id}:model:1", requested_model="claude-test")
            sink.emit(EventType.MODEL_REPORTED, call_id=lease.call_id,
                attempt_id=f"{lease.call_id}:attempt:1",
                model_execution_id=f"{lease.call_id}:model:1", reported_model="claude-test")
            child = BridgeCoordinator(dsn, policy, version, emitter, claude_adapter=None,
                qwen_adapter=FakeQwen(), parent_call_id=lease.call_id,
                parent_lease_id=lease.lease_id,
                local_capability={"context_tokens": 1000, "byte_upper_bound_validated": True,
                                  "client_timeout_verified": True},
                paid_capability={"context_tokens": 10000,
                                 "client_timeout_verified": paid_timeout_verified,
                                 "reported_models": ["claude-test"]})
            self.local = child.delegate_local_analysis("Check this Java method")
            assert self.local["state"] == (
                "completed" if verified and not timeout and not drift and
                paid_timeout_verified and not cutoff_before_post and not aged_client
                else "paid_continuation_required")
            claim = None
            if auto_claim:
                claim = {"answer": "Claude reviewed the null risk",
                    "local_results": [{"result_id": self.local["result_id"],
                        "input_sha256": self.local["content_sha256"],
                        "judgment": "accepted", "reason": "confirmed in source"}],
                    "work_resolution": "resolved"}
            elif timeout:
                claim = {"answer": "Claude handled the timed-out check",
                         "local_results": [],
                         "fallback_child_call_id": self.local["child_call_id"],
                         "work_resolution": "resolved"}
            return AdapterResult(state="completed", answer="Claude reviewed the null risk",
                                 reported_model="claude-test", completion_claim=claim)

    claude = DelegatingClaude()
    service = BridgeCoordinator(dsn, policy, version, emitter,
                                claude_adapter=claude, qwen_adapter=None)
    task = TaskPacket(task_id=f"task-{uuid.uuid4()}", work_item_id=f"work-{uuid.uuid4()}",
                      category="code_check", scope=["read"], token_budget=1000,
                      call_budget=2)
    outcome = service.delegate_claude(task, "Check this Java method",
        requested_model="claude-test", reserve_tokens=100)
    assert outcome["state"] == "completed"
    assert outcome["accounting_complete"] == (
        not verified or timeout or auto_claim or drift or
        not paid_timeout_verified or cutoff_before_post or aged_client)
    with psycopg.connect(dsn) as conn:
        if aged_client:
            assert claude.local["not_routed"] is True
            assert claude.local["reason"] == "fallback_time_unavailable"
            assert conn.execute("SELECT count(*) FROM runtime_jobs WHERE pool='qwen'").fetchone()[0] == 0
            return
        if cutoff_before_post:
            assert claude.local["not_routed"] is True
            assert conn.execute("SELECT state,slot_held,launch_claimed_at FROM runtime_jobs "
                "WHERE call_id=%s", (claude.local["child_call_id"],)).fetchone()[0:2] == (
                    "fallback", False)
            assert conn.execute("SELECT gate_facts->>'pre_dispatch',"
                "gate_facts->>'confirmed_not_sent' FROM delegation_decisions "
                "WHERE child_call_id=%s ORDER BY decided_at DESC LIMIT 1",
                (claude.local["child_call_id"],)).fetchone() == ("true", "true")
            return
        if not paid_timeout_verified:
            assert claude.local["not_routed"] is True
            assert claude.local["reason"] == "owner_model_incompatible"
            assert conn.execute("SELECT count(*) FROM runtime_jobs WHERE pool='qwen'").fetchone()[0] == 0
            return
        if drift:
            assert claude.local["not_routed"] is True
            assert conn.execute("SELECT state,slot_held,launch_claimed_at "
                "FROM runtime_jobs WHERE call_id=%s", (claude.local["child_call_id"],)).fetchone() == (
                    "fallback", False, None)
            assert conn.execute("SELECT route,gate_facts->>'pre_dispatch' "
                "FROM delegation_decisions WHERE child_call_id=%s "
                "ORDER BY decided_at DESC LIMIT 1", (claude.local["child_call_id"],)).fetchone() == (
                "paid", "true")
            return
        if not verified:
            assert conn.execute("SELECT count(*) FROM runtime_jobs WHERE pool='qwen'").fetchone()[0] == 0
            assert conn.execute("SELECT route,reason FROM delegation_decisions WHERE child_call_id=%s",
                (claude.local["child_call_id"],)).fetchone() == ("paid", "model_digest_mismatch")
            return
        if timeout:
            assert claude.local["not_routed"] is False
            assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                (claude.local["child_call_id"],)).fetchone() == ("fallback", True)
            execution_deadline, local_cutoff = conn.execute(
                "SELECT execution_deadline_at,local_cutoff_at FROM runtime_jobs "
                "WHERE call_id=%s", (claude.local["child_call_id"],)).fetchone()
            threshold = conn.execute("SELECT threshold_at FROM runtime_fallback_transitions "
                "WHERE child_call_id=%s", (claude.local["child_call_id"],)).fetchone()[0]
            assert threshold == min(execution_deadline, local_cutoff)
            assert conn.execute("SELECT route FROM delegation_decisions WHERE child_call_id=%s "
                "ORDER BY decided_at DESC LIMIT 1", (claude.local["child_call_id"],)).fetchone()[0] == "paid"
            assert conn.execute("SELECT state,paid_result_id FROM runtime_fallback_transitions "
                "WHERE child_call_id=%s", (claude.local["child_call_id"],)).fetchone() == (
                    "completed", outcome["result_id"])
            return
        local = conn.execute("SELECT r.result_id,c.sha256 FROM results r "
            "JOIN content_objects c ON c.content_id=r.delivered_content_id "
            "WHERE r.call_id=%s", (claude.local["call_id"],)).fetchone()
        assert local and claude.local["content_sha256"] == local[1]
        assert conn.execute("SELECT route FROM delegation_decisions WHERE child_call_id=%s",
                            (claude.local["call_id"],)).fetchone()[0] == "local"
        if auto_claim:
            assert claude.local["input_sha256"] == claude.local["content_sha256"]
            assert conn.execute("SELECT judgment FROM dispositions WHERE result_id=%s",
                (local[0],)).fetchone()[0] == "accepted"
            assert conn.execute("SELECT outcome,final_result_id FROM work_resolutions "
                "WHERE work_item_id=%s", (task.work_item_id,)).fetchone() == (
                "local_retained", outcome["result_id"])
            return
        assert conn.execute("SELECT count(*) FROM runtime_completion_diagnostics "
            "WHERE call_id=%s AND reason='missing consumer dispositions'",
            (outcome["call_id"],)).fetchone()[0] == 1
        parent_content_id = conn.execute("SELECT delivered_content_id FROM results WHERE result_id=%s",
            (outcome["result_id"],)).fetchone()[0]
    envelope = CompletionEnvelope.model_validate({"call_id": outcome["call_id"],
        "attempt_id": f"{outcome['call_id']}:attempt:1",
        "result": {"result_id": outcome["result_id"], "content_id": parent_content_id,
                   "delivered": True},
        "dispositions": [{"disposition_id": f"d-{uuid.uuid4()}",
            "result_id": local[0], "actor_id": service.workers["claude"], "stage": "claude",
            "judgment": "accepted", "input_sha256": local[1],
            "output_result_id": outcome["result_id"]}],
        "contributions": [{"from_result_id": local[0], "to_result_id": outcome["result_id"],
                           "relation": "accepted_contribution"}]})
    acknowledgement = service.complete_work(outcome["call_id"], envelope)
    assert acknowledgement["accounting_complete"] and not acknowledgement["diagnostics"]
