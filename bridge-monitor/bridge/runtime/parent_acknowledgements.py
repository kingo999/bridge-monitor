"""Persist parent-reported use of a delivered Claude result."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from bridge.runtime.admission import ARBITRATION_LOCK
from contracts.outcomes import ParentAcknowledgement
from contracts.events import EventEnvelope, EventType
from service.ingest.lineage import would_create_cycle
from service.ingest.store import ingest_batch


@dataclass(frozen=True)
class ParentAcknowledgementReceipt:
    acknowledgement_id: str
    output_result_id: str | None
    judgment: str
    created: bool


def _output_ids(parent_call_id: str, answer: bytes) -> tuple[str, str, str]:
    digest = hashlib.sha256(answer).hexdigest()
    base = f"bridge-monitor:parent-output:{parent_call_id}:{digest}"
    return (str(uuid.uuid5(uuid.NAMESPACE_URL, base + ":result")),
            str(uuid.uuid5(uuid.NAMESPACE_URL, base + ":content")), digest)


def record_parent_acknowledgement(dsn: str, *, call_id: str, producer_id: str,
                                  report: ParentAcknowledgement) -> ParentAcknowledgementReceipt:
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        owner = conn.execute("SELECT c.parent_call_id,c.work_item_id,w.task_id,j.pool "
            "FROM calls c JOIN work_items w ON w.work_item_id=c.work_item_id "
            "JOIN runtime_jobs j ON j.call_id=c.call_id WHERE c.call_id=%s",
            (call_id,)).fetchone()
        if not owner or owner[0] != f"gpt:{owner[2]}" or owner[3] != "claude":
            raise PermissionError("call is not a Claude delivery to this GPT parent")
        parent_call_id = owner[0]
        result = conn.execute("SELECT r.call_id,r.delivered_content_id,r.delivered_at,"
            "content.sha256 FROM results r LEFT JOIN content_objects content "
            "ON content.content_id=r.delivered_content_id WHERE r.result_id=%s",
            (report.result_id,)).fetchone()
        if not result or result[0] != call_id or not result[1] or not result[2]:
            raise PermissionError("result was not delivered by the owned Claude call")
        obligation = conn.execute("SELECT content_sha256 FROM runtime_disposition_obligations "
            "WHERE result_id=%s AND consumer_call_id=%s AND stage='gpt' AND part_id IS NULL",
            (report.result_id, parent_call_id)).fetchone()
        if not obligation or report.input_sha256 != obligation[0] or result[3] != obligation[0]:
            raise ValueError("parent input result hash or delivery obligation mismatch")

        actor_id = f"gpt-parent:{parent_call_id}"
        output_result_id = output_content_id = output_hash = None
        output_bytes = report.answer_text.encode("utf-8") if report.answer_text is not None else None
        if output_bytes is not None:
            output_result_id, output_content_id, output_hash = _output_ids(parent_call_id, output_bytes)
        disposition_output_id = output_result_id if report.judgment != "unused" else None
        prior = conn.execute("SELECT result_id,actor_id,stage,judgment,reason,supersedes_id,"
            "input_sha256,output_result_id,details FROM dispositions WHERE disposition_id=%s",
            (report.acknowledgement_id,)).fetchone()
        details = {"provenance": "parent_mcp_report", "producer_id": producer_id,
                   "parent_output_result_id": output_result_id}
        expected = (report.result_id, actor_id, "gpt", report.judgment, report.reason,
                    report.supersedes_id, report.input_sha256, disposition_output_id, details)
        if prior:
            if (prior[:8] != expected[:8] or
                    prior[8].get("provenance") != "parent_mcp_report" or
                    prior[8].get("parent_output_result_id") != output_result_id):
                raise ValueError("acknowledgement ID conflicts with different report")
            return ParentAcknowledgementReceipt(report.acknowledgement_id,
                                                output_result_id, report.judgment, False)

        existing = conn.execute("SELECT disposition_id,supersedes_id FROM dispositions "
            "WHERE result_id=%s AND stage='gpt' AND actor_id=%s AND part_id IS NULL",
            (report.result_id, actor_id)).fetchall()
        superseded = {target for _, target in existing if target}
        effective = [identifier for identifier, _ in existing if identifier not in superseded]
        if effective and (len(effective) != 1 or report.supersedes_id != effective[0]):
            raise ValueError("conflicting parent report requires effective supersession")
        if not effective and report.supersedes_id:
            raise ValueError("no effective parent report to supersede")

        if output_bytes is not None:
            conn.execute("INSERT INTO content_objects(content_id,raw_bytes,byte_length,sha256,"
                "role,direction,content_type,encoding,origin,capture_layer,completeness,"
                "message_id,block_order) VALUES(%s,%s,%s,%s,'assistant','out','text/plain',"
                "'utf-8','observed','parent_mcp_submission','complete',NULL,0) "
                "ON CONFLICT(content_id) DO NOTHING",
                (output_content_id, output_bytes, len(output_bytes), output_hash))
            stored = conn.execute("SELECT raw_bytes,sha256,capture_layer FROM content_objects "
                "WHERE content_id=%s", (output_content_id,)).fetchone()
            if stored != (output_bytes, output_hash, "parent_mcp_submission"):
                raise ValueError("parent output content identity conflict")
            conn.execute("INSERT INTO results(result_id,call_id,attempt_id,raw_content_id,"
                "delivered_content_id,delivered_at) VALUES(%s,%s,NULL,%s,NULL,NULL) "
                "ON CONFLICT(result_id) DO NOTHING",
                (output_result_id, parent_call_id, output_content_id))
            output = conn.execute("SELECT call_id,raw_content_id,delivered_content_id,"
                "delivered_at FROM results WHERE result_id=%s", (output_result_id,)).fetchone()
            if output != (parent_call_id, output_content_id, None, None):
                raise ValueError("parent output result identity conflict")

        conn.execute("INSERT INTO dispositions(disposition_id,result_id,actor_id,stage,"
            "judgment,reason,supersedes_id,recorded_at,part_id,input_sha256,"
            "output_result_id,details) VALUES(%s,%s,%s,'gpt',%s,%s,%s,now(),NULL,%s,%s,%s)",
            (report.acknowledgement_id, report.result_id, actor_id, report.judgment,
             report.reason, report.supersedes_id, report.input_sha256, disposition_output_id,
             Jsonb(details)))
        if report.supersedes_id:
            previous = conn.execute("SELECT judgment,output_result_id FROM dispositions "
                "WHERE disposition_id=%s", (report.supersedes_id,)).fetchone()
            if previous and previous[1]:
                previous_relation = {"accepted": "accepted_contribution",
                                     "revised": "revised_contribution",
                                     "replaced": "replacement"}.get(previous[0])
                if previous_relation:
                    conn.execute("DELETE FROM result_links WHERE from_result_id=%s "
                        "AND to_result_id=%s AND relation=%s",
                        (report.result_id, previous[1], previous_relation))
        if output_result_id is not None and report.judgment != "unused":
            relation = {"accepted": "accepted_contribution",
                        "revised": "revised_contribution",
                        "replaced": "replacement"}[report.judgment]
            if would_create_cycle(conn, report.result_id, output_result_id):
                raise ValueError("cyclic parent result lineage")
            conn.execute("INSERT INTO result_links(from_result_id,to_result_id,relation) "
                "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (report.result_id, output_result_id, relation))
        event = EventEnvelope(
            event_id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                f"bridge-monitor:parent-disposition:{report.acknowledgement_id}")),
            event_type=EventType.DISPOSITION_RECORDED, schema_version=1,
            producer_id=producer_id,
            producer_incarnation=f"parent-report:{report.acknowledgement_id}",
            producer_sequence=1, occurred_at=datetime.now(timezone.utc),
            origin="observed", observation_layer="bridge", capture_completeness="complete",
            task_id=owner[2], work_item_id=owner[1], call_id=call_id,
            payload={"disposition_id": report.acknowledgement_id,
                     "result_id": report.result_id, "actor_id": actor_id,
                     "stage": "gpt", "judgment": report.judgment, "reason": report.reason,
                     "supersedes_id": report.supersedes_id,
                     "input_sha256": report.input_sha256,
                     "output_result_id": disposition_output_id})
        if ingest_batch(dsn, producer_id, [event], [], connection=conn) != ["committed"]:
            raise RuntimeError("parent disposition event was not published")
        return ParentAcknowledgementReceipt(report.acknowledgement_id,
                                            output_result_id, report.judgment, True)
