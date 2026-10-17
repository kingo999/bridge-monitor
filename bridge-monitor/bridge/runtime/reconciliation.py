"""Evidence-backed resolution of uncertain execution ownership."""

import os
import secrets
import uuid
from typing import Literal

import psycopg
from psycopg.types.json import Jsonb

from bridge.runtime.admission import ARBITRATION_LOCK


def _authenticate(actor_kind: str, actor_id: str, auth_token: str) -> None:
    if not actor_id or actor_kind not in ("operator", "system"):
        raise PermissionError("recognized provenance is required")
    key = ("BRIDGE_MONITOR_OPERATOR_TOKEN" if actor_kind == "operator"
           else "BRIDGE_MONITOR_SYSTEM_TOKEN")
    expected = os.environ.get(key)
    if not expected or not auth_token or not secrets.compare_digest(expected, auth_token):
        raise PermissionError("reconciliation credential rejected")


def _state_from_evidence(call_id: str, lease_id: str, owner: str, evidence: dict) -> str:
    kind = evidence.get("kind")
    correlated = (evidence.get("call_id") == call_id and
                  evidence.get("lease_id") == lease_id and
                  evidence.get("owner_incarnation") == owner)
    if kind in ("adapter_termination_receipt", "process_exit_with_call_correlation"):
        if correlated and evidence.get("terminated") is True:
            return "terminated"
    if kind == "adapter_live_receipt" and correlated and evidence.get("live") is True:
        return "live"
    return "uncertain"


def reconcile_execution(dsn: str, call_id: str, evidence: dict, *,
                        actor_kind: Literal["operator", "system"], actor_id: str,
                        auth_token: str) -> Literal["live", "terminated", "uncertain"]:
    _authenticate(actor_kind, actor_id, auth_token)
    if not isinstance(evidence, dict):
        raise TypeError("structured evidence required")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        state, _ = _reconcile_execution_in_tx(conn, call_id, evidence,
                                              actor_kind=actor_kind, actor_id=actor_id)
        return state


def _reconcile_execution_in_tx(conn, call_id, evidence, *, actor_kind, actor_id):
    row = conn.execute("SELECT state,lease_id,owner_incarnation,slot_held FROM runtime_jobs "
                       "WHERE call_id=%s", (call_id,)).fetchone()
    if not row or row[0] == "queued":
        raise ValueError("no dispatched execution to reconcile")
    if row[0] == "completed":
        raise ValueError("completed execution cannot be reconciled as lost")
    state = _state_from_evidence(call_id, row[1], row[2], evidence)
    if row[0] == "terminated":
        state = "terminated"
    if state == "terminated":
        conn.execute("UPDATE runtime_jobs SET state='terminated',slot_held=false "
                     "WHERE call_id=%s", (call_id,))
        conn.execute("DELETE FROM runtime_source_locks WHERE holder_call_id=%s", (call_id,))
    elif state == "live" and row[0] != "terminated":
        if row[0] != "fallback":
            conn.execute("UPDATE runtime_jobs SET state='active' WHERE call_id=%s", (call_id,))
        conn.execute("UPDATE runtime_incarnations SET state='live' WHERE incarnation_id=%s "
                     "AND state='uncertain'", (row[2],))
    elif row[0] not in ("terminated", "fallback"):
        conn.execute("UPDATE runtime_jobs SET state='uncertain' WHERE call_id=%s", (call_id,))
    reconciliation_id = str(uuid.uuid4())
    conn.execute("INSERT INTO runtime_reconciliations(reconciliation_id,call_id,observed_state,"
                 "evidence,actor_kind,actor_id) VALUES(%s,%s,%s,%s,%s,%s)",
                 (reconciliation_id, call_id, state, Jsonb(evidence), actor_kind, actor_id))
    return state, reconciliation_id
