"""Restart only resumes already-admitted paid recovery calls."""

import time
import uuid

import psycopg

from bridge.adapters.base import AdapterResult
from bridge.mcp_server import BridgeCoordinator
from bridge.runtime.recovery import reconcile_owner_and_recover
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from service.ingest.store import ingest_batch
from tests.integration.test_admission import _policy
from tests.integration.test_dispositions import _seed_chain


def test_parent_restart_resumes_queued_recovery_exactly_once(
        runtime_dsn, tmp_path, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "restart-test-token")
    owner, child, _ = _seed_chain(dsn, dispatch_qwen=False, budget=3,
                                  recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM "
            "runtime_jobs WHERE call_id=%s", (owner,)).fetchone()
        version = conn.execute("SELECT version FROM runtime_active_policy "
            "WHERE singleton=true").fetchone()[0]
    evidence = {"kind": "process_exit_with_call_correlation", "call_id": owner,
                "lease_id": lease, "owner_incarnation": incarnation,
                "terminated": True}
    recovery = reconcile_owner_and_recover(dsn, owner, evidence,
        actor_kind="system", actor_id="coordinator", auth_token="restart-test-token")
    recovery_id = recovery["recoveries"][0]["recovery_call_id"]
    assert recovery["recoveries"][0]["state"] == "queued"

    class Collector:
        def ingest_batch(self, producer, events, content):
            return ingest_batch(dsn, producer, events, content)

    emitter = TelemetryEmitter(DurableSpool(tmp_path / "restart-spool.sqlite", limit_bytes=100000),
        Collector(), producer_id=f"bridge-{uuid.uuid4()}", incarnation="new-parent")
    launched = []

    class Claude:
        def run(self, lease, task, prompt, sink):
            launched.append(lease.call_id)
            assert prompt == "scoped child prompt"
            return AdapterResult(state="completed", answer="paid recovery answer",
                                 reported_model="claude-test")

    restarted = BridgeCoordinator(dsn, _policy(), version, emitter,
                                  claude_adapter=Claude())
    assert restarted.resume_queued_recoveries() == [recovery_id]
    restarted.resume_queued_recoveries()
    deadline = time.monotonic() + 5
    state = None
    while time.monotonic() < deadline:
        with psycopg.connect(dsn) as conn:
            state = conn.execute("SELECT state FROM runtime_jobs WHERE call_id=%s",
                                 (recovery_id,)).fetchone()[0]
        if state == "completed":
            break
        time.sleep(0.02)
    assert state == "completed"
    assert launched == [recovery_id]
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM results WHERE call_id=%s",
                            (recovery_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT state FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone()[0] == "cancelled"
