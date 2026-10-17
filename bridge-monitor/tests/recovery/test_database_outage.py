"""Database loss cannot partially commit an owner termination/recovery decision."""

import uuid

import psycopg
import pytest

from bridge.runtime import recovery
from tests.integration.test_dispositions import _seed_chain


def test_reconciliation_retries_after_database_connection_outage(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "db-outage-test-token")
    owner, child, _ = _seed_chain(dsn, budget=3,
                                  recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    evidence = {"kind": "process_exit_with_call_correlation", "call_id": owner,
                "lease_id": lease, "owner_incarnation": incarnation, "terminated": True}
    real_connect = recovery.psycopg.connect
    def disconnected(*args, **kwargs):
        raise psycopg.OperationalError("simulated database disconnection")
    monkeypatch.setattr(recovery.psycopg, "connect", disconnected)
    with pytest.raises(psycopg.OperationalError, match="disconnection"):
        recovery.reconcile_owner_and_recover(dsn, owner, evidence,
            actor_kind="system", actor_id="coordinator", auth_token="db-outage-test-token")
    monkeypatch.setattr(recovery.psycopg, "connect", real_connect)
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state FROM runtime_jobs WHERE call_id=%s",
                            (owner,)).fetchone() == ("active",)
        assert conn.execute("SELECT count(*) FROM runtime_recovery_links WHERE child_call_id=%s",
                            (child,)).fetchone()[0] == 0
    outcome = recovery.reconcile_owner_and_recover(dsn, owner, evidence,
        actor_kind="system", actor_id="coordinator", auth_token="db-outage-test-token")
    assert outcome["recoveries"][0]["state"] == "queued"
