"""Bounded operator projections of execution state and reconciliation evidence."""

import re


RECEIPT_LIMIT = 20
TEXT_LIMITS = {
    "kind": 128, "receipt_id": 256, "provenance": 256, "basis": 4000,
    "observed_at": 128, "source_evidence_path": 1024, "source_path": 1024,
}


def project_evidence(value: dict) -> dict:
    """Never expose arbitrary evidence JSON or coerce malformed values."""
    safe = {key: value[key][:limit] for key, limit in TEXT_LIMITS.items()
            if isinstance(value.get(key), str)}
    digest = value.get("source_sha256")
    if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
        safe["source_sha256"] = digest
    if type(value.get("terminated")) is bool:
        safe["terminated"] = value["terminated"]
    for key in ("historical_runner_pid", "exit_code"):
        candidate = value.get(key)
        if candidate is None or (type(candidate) is int and 0 <= candidate <= 2**31 - 1):
            safe[key] = candidate
    return safe


def read_runtime_executions(conn, work_item_id: str) -> list[dict]:
    """Use the caller's consistent read-only transaction and strict call scope."""
    rows = conn.execute("""SELECT j.call_id,j.pool,j.state,j.slot_held,j.policy_version,
        j.lease_id,j.owner_incarnation FROM runtime_jobs j
        JOIN calls c ON c.call_id=j.call_id AND c.work_item_id=j.work_item_id
        WHERE c.work_item_id=%s AND c.execution_mode='production'
        ORDER BY c.created_at,c.call_id""", (work_item_id,)).fetchall()
    executions = {row["call_id"]: {**dict(row), "receipts": [], "receipts_truncated": False}
                  for row in rows}
    if not executions:
        return []
    receipts = conn.execute("""SELECT rr.* FROM runtime_jobs j
        JOIN LATERAL (
            SELECT reconciliation_id,call_id,observed_state,recorded_at,actor_kind,actor_id,
                jsonb_build_object(
                    'kind',evidence->'kind','receipt_id',evidence->'receipt_id',
                    'provenance',evidence->'provenance','basis',evidence->'basis',
                    'observed_at',evidence->'observed_at',
                    'source_sha256',evidence->'source_sha256',
                    'source_evidence_path',evidence->'source_evidence_path',
                    'source_path',evidence->'source_path','terminated',evidence->'terminated',
                    'historical_runner_pid',evidence->'historical_runner_pid',
                    'exit_code',evidence->'exit_code') AS evidence
            FROM runtime_reconciliations WHERE call_id=j.call_id
            ORDER BY recorded_at DESC,reconciliation_id DESC LIMIT %s
        ) rr ON true WHERE j.call_id=ANY(%s)
        ORDER BY rr.call_id,rr.recorded_at DESC,rr.reconciliation_id DESC""",
        (RECEIPT_LIMIT + 1, list(executions))).fetchall()
    for row in receipts:
        execution = executions[row["call_id"]]
        if len(execution["receipts"]) == RECEIPT_LIMIT:
            execution["receipts_truncated"] = True
            continue
        execution["receipts"].append({**dict(row),
            "actor_id": row["actor_id"][:256],
            "evidence": project_evidence(row["evidence"])})
    return list(executions.values())
