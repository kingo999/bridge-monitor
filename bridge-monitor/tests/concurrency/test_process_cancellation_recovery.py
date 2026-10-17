"""Process receipts and durable cancellation/recovery in the isolated test DB."""

import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg

from bridge.adapters.base import AdapterResult
from bridge.adapters.claude_cli import SubprocessRunner
from bridge.adapters.qwen_runtime import ModelPreflight
from bridge.mcp_server import BridgeCoordinator
from bridge.runtime.admission import admit_call
from bridge.runtime.ownership import claim_dispatch, register_incarnation
from bridge.runtime.reconciliation import reconcile_execution
from bridge.runtime.recovery import reconcile_owner_and_recover
from bridge.runtime.scheduler import cancellation_requested, request_cancel_tree
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from contracts.work import CallRequest, TaskPacket
from service.ingest.store import ingest_batch
from tests.integration.test_dispositions import _seed_chain
from tests.integration.test_ownership import _setup
from tests.integration.test_admission import _policy
from contracts.events import EventType


def _owner_evidence(dsn, owner):
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute(
            "SELECT lease_id,owner_incarnation FROM runtime_jobs WHERE call_id=%s",
            (owner,)).fetchone()
    return {"kind": "process_exit_with_call_correlation", "call_id": owner,
            "lease_id": lease, "owner_incarnation": incarnation, "terminated": True}


def test_process_cancellation_keeps_remote_child_held(runtime_dsn, tmp_path, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "process-test-token")
    owner, child, _ = _seed_chain(dsn, budget=3, recipient_call_id=f"gpt-{uuid.uuid4()}")
    runner = SubprocessRunner(cwd=tmp_path, timeout_seconds=10,
        client_tool_timeout_seconds=5, cancel_poll_seconds=0.05)
    command = [sys.executable, "-c", "import time; time.sleep(10)"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: list(runner.stream(command, "probe",
            cancel_requested=lambda: cancellation_requested(dsn, owner))))
        assert request_cancel_tree(dsn, owner, reason="caller_cancelled")
        assert future.result(timeout=5) == []
    assert runner.cancelled and runner.terminated
    decision = reconcile_owner_and_recover(dsn, owner, _owner_evidence(dsn, owner),
        actor_kind="system", actor_id="coordinator", auth_token="process-test-token")
    assert decision == {"state": "terminated", "recoveries": []}
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (owner,)).fetchone() == ("terminated", False)
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("uncertain", True)


def test_process_timeout_queues_one_paid_child_recovery(runtime_dsn, tmp_path, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "process-test-token")
    owner, child, _ = _seed_chain(dsn, budget=3, recipient_call_id=f"gpt-{uuid.uuid4()}")
    runner = SubprocessRunner(cwd=tmp_path, timeout_seconds=0.25,
        client_tool_timeout_seconds=5)
    command = [sys.executable, "-c", "import time; time.sleep(10)"]
    assert list(runner.stream(command, "probe")) == []
    assert runner.timed_out and runner.terminated
    evidence = _owner_evidence(dsn, owner)
    first = reconcile_owner_and_recover(dsn, owner, evidence, actor_kind="system",
        actor_id="coordinator", auth_token="process-test-token")
    second = reconcile_owner_and_recover(dsn, owner, evidence, actor_kind="system",
        actor_id="coordinator", auth_token="process-test-token")
    assert first == second
    assert first["recoveries"][0]["child_call_id"] == child
    assert first["recoveries"][0]["state"] == "queued"
    recovery = first["recoveries"][0]["recovery_call_id"]
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (owner,)).fetchone() == ("terminated", False)
        assert conn.execute("SELECT c.raw_bytes FROM runtime_jobs j JOIN content_objects c "
                            "ON c.content_id=j.prompt_content_id WHERE j.call_id=%s",
                            (recovery,)).fetchone()[0] == b"scoped child prompt"
        assert conn.execute("SELECT count(*) FROM runtime_recovery_links WHERE child_call_id=%s",
                            (child,)).fetchone()[0] == 1


def test_coordinator_dispatches_paid_child_recovery_after_owner_exit(
        runtime_dsn, tmp_path, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "process-test-token")
    policy, version = _setup(dsn)

    class Collector:
        def ingest_batch(self, producer, events, content):
            return ingest_batch(dsn, producer, events, content)

    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(), producer_id=f"bridge-{uuid.uuid4()}", incarnation="boot-1")
    launches = []
    child_calls = []
    service = None

    class OwnerThenRecovery:
        def run(self, lease, task, prompt, sink):
            launches.append((lease.call_id, prompt))
            if prompt == "owner prompt":
                child = CallRequest(call_id=str(uuid.uuid4()), task=task,
                    requested_model="qwen-test", parent_call_id=lease.call_id,
                    parent_attempt_id=f"{lease.call_id}:attempt:1")
                admitted = service.ingress.submit(child, prompt="scoped child prompt",
                    pool="qwen", reserve_tokens=100,
                    local_cutoff_at=datetime.now(timezone.utc) + timedelta(seconds=30))
                assert admitted.state == "queued"
                child_calls.append(child.call_id)
                return AdapterResult(state="uncertain", uncertainty="cli_timeout",
                                     terminated=True)
            return AdapterResult(state="completed", answer="paid child result",
                                 reported_model="claude-test")

    service = BridgeCoordinator(dsn, policy, version, emitter,
                                claude_adapter=OwnerThenRecovery())
    task = TaskPacket(task_id=f"task-{uuid.uuid4()}",
        work_item_id=f"work-{uuid.uuid4()}", category="analysis", scope=["read"],
        token_budget=1000, call_budget=3)
    owner = service.delegate_claude(task, "owner prompt", requested_model="claude-test",
                                    reserve_tokens=100)
    assert owner["state"] == "terminated"
    recovery_call = owner["recovery"]["recoveries"][0]["recovery_call_id"]
    deadline = time.monotonic() + 5
    state = None
    while time.monotonic() < deadline:
        with psycopg.connect(dsn) as conn:
            state = conn.execute("SELECT state FROM runtime_jobs WHERE call_id=%s",
                                 (recovery_call,)).fetchone()[0]
        if state == "completed":
            break
        time.sleep(0.02)
    assert state == "completed"
    assert launches == [(owner["call_id"], "owner prompt"),
                        (recovery_call, "scoped child prompt")]
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child_calls[0],)).fetchone() == ("cancelled", False)
        assert conn.execute("SELECT count(*) FROM runtime_recovery_links WHERE child_call_id=%s",
                            (child_calls[0],)).fetchone()[0] == 1


def test_paid_recovery_waits_for_busy_claude_slot(runtime_dsn, tmp_path, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "process-test-token")
    owner, child, _ = _seed_chain(dsn, budget=3,
                                  recipient_call_id=f"gpt-{uuid.uuid4()}")
    evidence = _owner_evidence(dsn, owner)
    assert reconcile_execution(dsn, owner, evidence, actor_kind="system",
        actor_id="coordinator", auth_token="process-test-token") == "terminated"
    policy = _policy()
    with psycopg.connect(dsn) as conn:
        version = conn.execute("SELECT version FROM runtime_active_policy WHERE singleton=true").fetchone()[0]
    blocker_id = f"blocker-{uuid.uuid4()}"
    blocker_task = TaskPacket(task_id=f"task-{uuid.uuid4()}",
        work_item_id=f"work-{uuid.uuid4()}", category="analysis", scope=["read"],
        token_budget=1000, call_budget=1)
    blocker_request = CallRequest(call_id=blocker_id, task=blocker_task,
                                  requested_model="claude-test")
    assert admit_call(dsn, blocker_request, policy, policy_version=version,
                      pool="claude", reserve_tokens=100).state == "queued"
    register_incarnation(dsn, "blocker-inc", "blocker-worker")
    blocker_lease = claim_dispatch(dsn, pool="claude", incarnation_id="blocker-inc")
    assert blocker_lease.call_id == blocker_id
    recovery = reconcile_owner_and_recover(dsn, owner, evidence, actor_kind="system",
        actor_id="coordinator", auth_token="process-test-token")
    recovery_id = recovery["recoveries"][0]["recovery_call_id"]

    class Collector:
        def ingest_batch(self, producer, events, content):
            return ingest_batch(dsn, producer, events, content)

    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(), producer_id=f"bridge-{uuid.uuid4()}", incarnation="boot-1")

    class RecoveryClaude:
        def run(self, lease, task, prompt, sink):
            assert prompt == "scoped child prompt"
            return AdapterResult(state="completed", answer="paid child result",
                                 reported_model="claude-test")

    service = BridgeCoordinator(dsn, policy, version, emitter,
                                claude_adapter=RecoveryClaude())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.run_recovery_until_terminal, recovery_id)
        time.sleep(0.2)
        assert not future.done()
        assert service.get_execution_status(recovery_id)["state"] == "queued"
        assert reconcile_execution(dsn, blocker_id, {
            "kind": "process_exit_with_call_correlation", "call_id": blocker_id,
            "lease_id": blocker_lease.lease_id,
            "owner_incarnation": blocker_lease.owner_incarnation,
            "terminated": True}, actor_kind="system", actor_id="coordinator",
            auth_token="process-test-token") == "terminated"
        assert future.result(timeout=5)["state"] == "completed"


def test_late_local_result_is_retained_without_coordinator_delivery(
        runtime_dsn, tmp_path, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "process-test-token")
    owner, child, _ = _seed_chain(dsn, dispatch_qwen=False, budget=3,
                                  recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        version = conn.execute("SELECT version FROM runtime_active_policy "
                               "WHERE singleton=true").fetchone()[0]
    started = threading.Event()
    release = threading.Event()

    class Qwen:
        def preflight(self):
            return ModelPreflight(True, "model_profile_verified")

        def run(self, lease, prompt, sink, *, task, deadline_at):
            assert lease.call_id == child and prompt == "scoped child prompt"
            started.set()
            assert release.wait(5)
            return AdapterResult(state="completed", answer="late local answer",
                                 reported_model="qwen-test")

    class Collector:
        def ingest_batch(self, producer, events, content):
            return ingest_batch(dsn, producer, events, content)

    emitter = TelemetryEmitter(DurableSpool(tmp_path / "late-spool.sqlite", limit_bytes=100000),
        Collector(), producer_id=f"bridge-{uuid.uuid4()}", incarnation="boot-1")
    service = BridgeCoordinator(dsn, _policy(), version, emitter,
                                claude_adapter=None, qwen_adapter=Qwen())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.run_one, "qwen", call_id=child)
        assert started.wait(5)
        assert reconcile_execution(dsn, owner, _owner_evidence(dsn, owner),
            actor_kind="system", actor_id="coordinator",
            auth_token="process-test-token") == "terminated"
        release.set()
        outcome = future.result(timeout=5)
    with psycopg.connect(dsn) as conn:
        result = conn.execute("SELECT delivered_at,delivered_content_id FROM results "
                              "WHERE result_id=%s", (outcome["result_id"],)).fetchone()
        delivered_events = conn.execute("SELECT count(*) FROM events WHERE call_id=%s "
            "AND event_type=%s", (child, EventType.RESULT_DELIVERED.value)).fetchone()[0]
        obligations = conn.execute("SELECT count(*) FROM runtime_disposition_obligations "
            "WHERE result_id=%s", (outcome["result_id"],)).fetchone()[0]
    assert result == (None, None)
    assert delivered_events == 0
    assert obligations == 0
    assert outcome["answer"] is None
    assert outcome["delivered"] is False
