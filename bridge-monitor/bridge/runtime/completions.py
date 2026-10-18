"""Answer delivery and independent consumer accounting, without inference replay."""

import uuid
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from contracts.content import ContentObject
from contracts.outcomes import CompletionEnvelope, ResultReceipt
from contracts.work import ExecutionBinding
from service.ingest.lineage import would_create_cycle
from bridge.runtime.admission import ARBITRATION_LOCK


@dataclass(frozen=True)
class CompletionReceipt:
    answer: ResultReceipt
    accounting_complete: bool
    diagnostics: tuple[str, ...]


def _check_binding(conn, binding: ExecutionBinding, envelope: CompletionEnvelope) -> tuple[str, str, str]:
    if binding.call_id != envelope.call_id or binding.attempt_id != envelope.attempt_id:
        raise PermissionError("completion binding mismatch")
    row = conn.execute("SELECT state,owner_incarnation,worker_instance_id,pool,execution_mode FROM runtime_jobs "
                       "WHERE call_id=%s", (binding.call_id,)).fetchone()
    if not row or row[0] not in ("active", "uncertain", "fallback", "completed"):
        raise PermissionError("no owned execution for completion")
    if row[1] != binding.producer_incarnation or row[2] != binding.worker_instance_id:
        raise PermissionError("completion actor does not own execution")
    attempt = conn.execute("SELECT call_id FROM attempts WHERE attempt_id=%s",
                           (binding.attempt_id,)).fetchone()
    if not attempt or attempt[0] != binding.call_id:
        raise PermissionError("attempt is not bound to call")
    return row[3], row[0], row[4]


def _store_answer(conn, envelope: CompletionEnvelope, answer_bytes: bytes | None,
                  raw_answer_bytes: bytes | None) -> ResultReceipt:
    receipt = envelope.result
    if receipt.delivered and not receipt.content_id:
        raise ValueError("delivered answer requires exact content identity")
    if answer_bytes is not None:
        if not receipt.content_id:
            raise ValueError("content identity required for answer bytes")
        content = ContentObject.from_bytes(receipt.content_id, answer_bytes,
            role="assistant", direction="in", content_type="text/plain",
            origin="observed", capture_layer="bridge", completeness="complete")
        if receipt.sha256 and receipt.sha256 != content.sha256:
            raise ValueError("answer hash mismatch")
        conn.execute("INSERT INTO content_objects(content_id,raw_bytes,byte_length,sha256,role,direction,"
                     "content_type,encoding,origin,capture_layer,completeness,message_id,block_order) "
                     "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,0) ON CONFLICT(content_id) DO NOTHING",
                     (content.content_id, content.raw_bytes, content.byte_length, content.sha256,
                      content.role, content.direction, content.content_type, content.encoding,
                      content.origin, content.capture_layer, content.completeness))
    stored = conn.execute("SELECT sha256 FROM content_objects WHERE content_id=%s",
                          (receipt.content_id,)).fetchone() if receipt.content_id else None
    if receipt.content_id and not stored:
        raise ValueError("answer content is unavailable")
    if receipt.sha256 and stored and receipt.sha256 != stored[0]:
        raise ValueError("answer hash mismatch")
    if receipt.delivered and (not stored or not stored[0]):
        raise ValueError("delivered answer needs verified content hash")
    prior = conn.execute("SELECT raw_content_id FROM results WHERE result_id=%s",
                         (receipt.result_id,)).fetchone()
    raw_content_id = (receipt.raw_content_id or
                      (prior[0] if prior and answer_bytes is None else receipt.content_id))
    if raw_content_id != receipt.content_id:
        if raw_answer_bytes is None and prior and prior[0] == raw_content_id:
            pass
        elif raw_answer_bytes is None:
            raise ValueError("separate raw content requires exact raw bytes")
        else:
            raw = ContentObject.from_bytes(raw_content_id, raw_answer_bytes,
                role="assistant", direction="in", content_type="text/plain",
                origin="observed", capture_layer="bridge", completeness="complete")
            conn.execute("INSERT INTO content_objects(content_id,raw_bytes,byte_length,sha256,role,direction,"
                         "content_type,encoding,origin,capture_layer,completeness,message_id,block_order) "
                         "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,0) ON CONFLICT(content_id) DO NOTHING",
                         (raw.content_id, raw.raw_bytes, raw.byte_length, raw.sha256,
                          raw.role, raw.direction, raw.content_type, raw.encoding,
                          raw.origin, raw.capture_layer, raw.completeness))
            persisted_raw = conn.execute("SELECT sha256 FROM content_objects WHERE content_id=%s",
                                         (raw_content_id,)).fetchone()
            if not persisted_raw or persisted_raw[0] != raw.sha256:
                raise ValueError("raw content identity conflict")
    conn.execute("INSERT INTO results(result_id,call_id,attempt_id,raw_content_id,"
                 "delivered_content_id,delivered_at) VALUES(%s,%s,%s,%s,%s,"
                 "CASE WHEN %s THEN now() ELSE NULL END) ON CONFLICT(result_id) DO NOTHING",
                 (receipt.result_id, envelope.call_id, envelope.attempt_id, raw_content_id,
                  receipt.content_id if receipt.delivered else None, receipt.delivered))
    row = conn.execute("SELECT call_id,attempt_id,raw_content_id,delivered_content_id "
                       "FROM results WHERE result_id=%s", (receipt.result_id,)).fetchone()
    expected = (envelope.call_id, envelope.attempt_id, raw_content_id,
                receipt.content_id if receipt.delivered else None)
    if row != expected:
        raise ValueError("result identity conflict")
    return receipt.model_copy(update={"sha256": stored[0] if stored else None})


def _create_obligation(conn, receipt: ResultReceipt, consumer_call_id: str, stage: str) -> None:
    if not receipt.delivered or not receipt.sha256:
        return
    conn.execute("INSERT INTO runtime_disposition_obligations(obligation_id,result_id,"
                 "consumer_call_id,stage,content_sha256,delivered_at) "
                 "VALUES(%s,%s,%s,%s,%s,now()) ON CONFLICT(result_id,stage,consumer_call_id,part_id) DO NOTHING",
                 (str(uuid.uuid4()), receipt.result_id, consumer_call_id, stage, receipt.sha256))


def _store_disposition(conn, disposition, binding: ExecutionBinding, pool: str,
                       output_result_id: str) -> None:
    if disposition.actor_id != binding.worker_instance_id:
        raise ValueError("disposition actor spoofing")
    if disposition.stage != "claude" or pool != "claude":
        raise ValueError("consumer stage not authorized for this execution")
    obligation = conn.execute("SELECT content_sha256 FROM runtime_disposition_obligations "
                              "WHERE result_id=%s AND stage=%s AND consumer_call_id=%s "
                              "AND part_id IS NOT DISTINCT FROM %s",
                              (disposition.result_id, disposition.stage, binding.call_id,
                               disposition.part_id)).fetchone()
    if not obligation:
        raise ValueError("result was not delivered to this consumer")
    if not disposition.input_sha256 or disposition.input_sha256 != obligation[0]:
        raise ValueError("input result hash mismatch")
    if disposition.judgment in ("accepted", "revised", "replaced"):
        if disposition.output_result_id != output_result_id:
            raise ValueError("disposition must identify the delivered output result")
    elif disposition.output_result_id:
        raise ValueError("unused result cannot claim an output contribution")
    existing = conn.execute("SELECT disposition_id,supersedes_id FROM dispositions "
                            "WHERE result_id=%s AND stage=%s AND actor_id=%s "
                            "AND part_id IS NOT DISTINCT FROM %s",
                            (disposition.result_id, disposition.stage, disposition.actor_id,
                             disposition.part_id)).fetchall()
    if any(row[0] == disposition.disposition_id for row in existing):
        return
    effective = [row[0] for row in existing if not any(other[1] == row[0] for other in existing)]
    if effective and disposition.supersedes_id not in effective:
        raise ValueError("conflicting disposition requires explicit supersession")
    if not effective and disposition.supersedes_id:
        raise ValueError("supersession target is not effective")
    conn.execute("INSERT INTO dispositions(disposition_id,result_id,actor_id,stage,judgment,reason,"
                 "supersedes_id,recorded_at,part_id,input_sha256,output_result_id,details) "
                 "VALUES(%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s)",
                 (disposition.disposition_id, disposition.result_id, binding.worker_instance_id,
                  disposition.stage, disposition.judgment, disposition.reason,
                  disposition.supersedes_id, disposition.part_id, disposition.input_sha256,
                  disposition.output_result_id, Jsonb(disposition.model_dump(exclude={"disposition_id",
                      "result_id", "actor_id", "stage", "judgment", "reason", "supersedes_id",
                      "part_id", "input_sha256", "output_result_id"}))))


def _accounting_complete(conn, call_id: str) -> bool:
    pending = conn.execute("""SELECT 1 FROM runtime_disposition_obligations o
        WHERE o.consumer_call_id=%s AND NOT EXISTS (
            SELECT 1 FROM dispositions d WHERE d.result_id=o.result_id AND d.stage=o.stage
            AND d.part_id IS NOT DISTINCT FROM o.part_id
            AND NOT EXISTS (SELECT 1 FROM dispositions s WHERE s.supersedes_id=d.disposition_id)
        ) LIMIT 1""", (call_id,)).fetchone()
    return not bool(pending)


def record_shadow_completion(dsn: str, envelope: CompletionEnvelope,
                             binding: ExecutionBinding, *, raw_bytes: bytes,
                             parsed_bytes: bytes | None, evidence: dict) -> ResultReceipt:
    """Commit raw output, parsed candidate, evaluation and slot release atomically."""
    if envelope.result.delivered or not evidence or not evidence.get("state"):
        raise ValueError("shadow completion requires undelivered evaluated result")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        pool, job_state, mode = _check_binding(conn, binding, envelope)
        if pool != "qwen" or mode != "shadow" or job_state != "active":
            raise PermissionError("shadow completion needs active owned Qwen job")
        answer = _store_answer(conn, envelope, raw_bytes, None)
        if parsed_bytes is not None:
            content_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                f"bridge-monitor:shadow:parsed-content:{binding.call_id}"))
            part_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                f"bridge-monitor:shadow:parsed-part:{binding.call_id}"))
            content = ContentObject.from_bytes(content_id, parsed_bytes,
                role="assistant", direction="in", content_type="text/x-python",
                origin="observed", capture_layer="bridge_router_verifier",
                completeness="complete")
            conn.execute("INSERT INTO content_objects(content_id,raw_bytes,byte_length,sha256,"
                "role,direction,content_type,encoding,origin,capture_layer,completeness,"
                "message_id,block_order) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,0) "
                "ON CONFLICT(content_id) DO NOTHING",
                (content.content_id, content.raw_bytes, content.byte_length, content.sha256,
                 content.role, content.direction, content.content_type, content.encoding,
                 content.origin, content.capture_layer, content.completeness))
            conn.execute("INSERT INTO result_parts(part_id,result_id,content_id) "
                "VALUES(%s,%s,%s) ON CONFLICT(part_id) DO NOTHING",
                (part_id, answer.result_id, content_id))
        evaluation_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
            f"bridge-monitor:shadow:evaluation:{binding.call_id}"))
        conn.execute("INSERT INTO evaluations(evaluation_id,result_id,evidence,recorded_at) "
            "VALUES(%s,%s,%s,now())", (evaluation_id, answer.result_id, Jsonb(evidence)))
        conn.execute("UPDATE runtime_jobs SET state='completed',slot_held=false WHERE call_id=%s",
                     (binding.call_id,))
        return answer


def record_completion(dsn: str, envelope: CompletionEnvelope, binding: ExecutionBinding,
                      *, answer_bytes: bytes | None = None,
                      raw_answer_bytes: bytes | None = None,
                      claim_diagnostics: list[str] | None = None,
                      consumer_stage: str | None = None) -> CompletionReceipt:
    diagnostics = list(claim_diagnostics or [])
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        pool, job_state, execution_mode = _check_binding(conn, binding, envelope)
        if execution_mode == "shadow" and (envelope.result.delivered or consumer_stage or
                                            envelope.dispositions or envelope.contributions or
                                            envelope.work_resolution or envelope.fallback_child_call_id):
            raise PermissionError("shadow result cannot be delivered or enter production accounting")
        late_only = job_state == "fallback"
        if pool == "qwen":
            late_only |= conn.execute("SELECT 1 FROM runtime_fallback_transitions "
                "WHERE child_call_id=%s LIMIT 1", (envelope.call_id,)).fetchone() is not None
            owner = conn.execute("SELECT j.state,c.parent_call_id FROM calls c "
                "LEFT JOIN runtime_jobs j ON j.call_id=c.parent_call_id "
                "WHERE c.call_id=%s", (envelope.call_id,)).fetchone()
            if owner and owner[1]:
                late_only |= owner[0] in ("terminated", "completed", "cancelled")
                late_only |= conn.execute("SELECT 1 FROM runtime_reconciliations "
                    "WHERE call_id=%s AND evidence->>'kind'='cancellation_requested' "
                    "LIMIT 1", (owner[1],)).fetchone() is not None
        if late_only:
            envelope = envelope.model_copy(update={"result": envelope.result.model_copy(update={"delivered": False})})
        answer = _store_answer(conn, envelope, answer_bytes, raw_answer_bytes)
        if consumer_stage:
            parent = conn.execute("SELECT parent_call_id FROM calls WHERE call_id=%s",
                                  (envelope.call_id,)).fetchone()
            if not parent or not parent[0]:
                raise ValueError("consumer stage requires a parent call")
            _create_obligation(conn, answer, parent[0], consumer_stage)
        if pool == "claude":
            caller = conn.execute("SELECT c.parent_call_id,w.task_id FROM calls c "
                                  "JOIN work_items w ON w.work_item_id=c.work_item_id "
                                  "WHERE c.call_id=%s", (envelope.call_id,)).fetchone()
            if caller and caller[0] == f"gpt:{caller[1]}":
                _create_obligation(conn, answer, caller[0], "gpt")
        for disposition in envelope.dispositions:
            try:
                with conn.transaction():
                    _store_disposition(conn, disposition, binding, pool, answer.result_id)
            except (ValueError, psycopg.IntegrityError) as exc:
                diagnostics.append(str(exc))
        for link in envelope.contributions:
            try:
                with conn.transaction():
                    if would_create_cycle(conn, link.from_result_id, link.to_result_id):
                        raise ValueError("cyclic result lineage")
                    conn.execute("INSERT INTO result_links(from_result_id,to_result_id,relation) "
                                 "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                                 (link.from_result_id, link.to_result_id, link.relation))
            except (ValueError, psycopg.IntegrityError) as exc:
                diagnostics.append(str(exc))
        if envelope.fallback_child_call_id:
            fallback = conn.execute("SELECT state,recipient_call_id,paid_result_id,"
                                    "timeout_stage,work_item_id FROM runtime_fallback_transitions "
                                    "WHERE child_call_id=%s", (envelope.fallback_child_call_id,)).fetchone()
            if not fallback or fallback[1] != binding.call_id or fallback[0] not in (
                    "committed", "completed") or (fallback[0] == "completed" and
                    fallback[2] != answer.result_id):
                diagnostics.append("fallback transition not bound to this owner")
            elif answer.delivered:
                conn.execute("UPDATE runtime_fallback_transitions SET state='completed',"
                             "paid_result_id=%s,resolved_at=coalesce(resolved_at,now()) "
                             "WHERE child_call_id=%s",
                             (answer.result_id, envelope.fallback_child_call_id))
                if envelope.work_resolution == "resolved":
                    outcome = ("paid_replaced" if fallback[3] in ("execution", "cutoff")
                               else "paid_direct")
                    resolution_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                        f"bridge-monitor:{envelope.fallback_child_call_id}:{outcome}"))
                    conn.execute("INSERT INTO work_resolutions(resolution_id,work_item_id,"
                                 "outcome,final_result_id,recorded_at) "
                                 "VALUES(%s,%s,%s,%s,now()) ON CONFLICT(resolution_id) DO NOTHING",
                                 (resolution_id, fallback[4], outcome, answer.result_id))
        if job_state in ("active", "uncertain", "fallback"):
            conn.execute("UPDATE runtime_jobs SET state='completed',slot_held=false "
                         "WHERE call_id=%s", (binding.call_id,))
        if not _accounting_complete(conn, binding.call_id):
            diagnostics.append("missing consumer dispositions")
        if (pool == "claude" and answer.delivered and
                envelope.work_resolution == "resolved" and
                not envelope.fallback_child_call_id and not diagnostics):
            unlinked_fallback = conn.execute("SELECT 1 FROM runtime_fallback_transitions "
                "WHERE recipient_call_id=%s LIMIT 1", (binding.call_id,)).fetchone()
            judgments = [item.judgment for item in envelope.dispositions]
            outcome = None if unlinked_fallback else ({"accepted": "local_retained",
                        "revised": "paid_revision_with_contribution",
                        "replaced": "paid_replaced"}.get(judgments[0])
                       if len(judgments) == 1 else
                       "paid_direct" if not judgments else None)
            if outcome:
                work_item = conn.execute("SELECT work_item_id FROM runtime_jobs WHERE call_id=%s",
                                         (binding.call_id,)).fetchone()[0]
                resolution_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                    f"bridge-monitor:{binding.call_id}:{answer.result_id}:{outcome}"))
                conn.execute("INSERT INTO work_resolutions(resolution_id,work_item_id,"
                             "outcome,final_result_id,recorded_at) "
                             "VALUES(%s,%s,%s,%s,now()) ON CONFLICT(resolution_id) DO NOTHING",
                             (resolution_id, work_item, outcome, answer.result_id))
        complete = not diagnostics
        for reason in diagnostics:
            conn.execute("INSERT INTO runtime_completion_diagnostics(diagnostic_id,call_id,attempt_id,"
                         "envelope,reason) VALUES(%s,%s,%s,%s,%s)",
                         (str(uuid.uuid4()), binding.call_id, binding.attempt_id,
                          Jsonb(envelope.model_dump()), reason))
        return CompletionReceipt(answer=answer, accounting_complete=complete,
                                 diagnostics=tuple(diagnostics))
