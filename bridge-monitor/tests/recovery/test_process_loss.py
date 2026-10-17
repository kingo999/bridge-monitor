"""A lost worker process cannot replay an already-claimed inference."""

import json
import subprocess
import sys
import uuid

import psycopg

from bridge.runtime.admission import admit_call
from bridge.runtime.ownership import claim_dispatch, register_incarnation
from bridge.runtime.reconciliation import reconcile_execution
from tests.integration.test_admission import _call
from tests.integration.test_ownership import _setup


def test_abrupt_worker_exit_keeps_capacity_until_correlated_reconciliation(
        runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "process-loss-token")
    policy, version = _setup(dsn)
    call_id = f"call-{uuid.uuid4()}"
    assert admit_call(dsn, _call(f"root-{call_id}", call_id), policy,
                      policy_version=version, pool="claude", reserve_tokens=100).state == "queued"
    incarnation = f"inc-{uuid.uuid4()}"
    register_incarnation(dsn, incarnation, "lost-worker")
    worker = "import json,os,sys; from bridge.runtime.ownership import claim_dispatch,claim_launch; " \
        "lease=claim_dispatch(sys.argv[1],pool='claude',incarnation_id=sys.argv[2]); " \
        "assert lease and claim_launch(sys.argv[1],lease); " \
        "print(json.dumps({'call_id':lease.call_id,'lease_id':lease.lease_id}),flush=True); os._exit(0)"
    completed = subprocess.run([sys.executable, "-c", worker, dsn, incarnation],
        capture_output=True, text=True, timeout=10, check=True)
    receipt = json.loads(completed.stdout.strip())
    assert receipt["call_id"] == call_id
    newcomer = f"inc-{uuid.uuid4()}"
    register_incarnation(dsn, newcomer, "new-worker")
    assert claim_dispatch(dsn, pool="claude", incarnation_id=newcomer, call_id=call_id) is None
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held,launch_claimed_at IS NOT NULL "
            "FROM runtime_jobs WHERE call_id=%s", (call_id,)).fetchone() == ("active", True, True)
    assert reconcile_execution(dsn, call_id, {"kind": "heartbeat_expired"},
        actor_kind="operator", actor_id="reviewer", auth_token="process-loss-token") == "uncertain"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT slot_held FROM runtime_jobs WHERE call_id=%s",
                            (call_id,)).fetchone()[0]
    assert reconcile_execution(dsn, call_id, {"kind": "process_exit_with_call_correlation",
        "call_id": call_id, "lease_id": receipt["lease_id"],
        "owner_incarnation": incarnation, "terminated": True},
        actor_kind="operator", actor_id="reviewer", auth_token="process-loss-token") == "terminated"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (call_id,)).fetchone() == ("terminated", False)
