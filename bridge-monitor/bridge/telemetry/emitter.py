"""Convert bridge observations to contract events and replay the durable spool."""

import uuid
import time
from datetime import datetime, timezone

from contracts.content import ContentObject
from contracts.events import EventEnvelope, EventType


class TelemetryEmitter:
    def __init__(self, spool, collector, *, producer_id: str, incarnation: str,
                 allow_operations: bool = False):
        if type(allow_operations) is not bool:
            raise TypeError("allow_operations must be a boolean")
        if allow_operations and getattr(spool, "allow_operations", False) is not True:
            raise ValueError("operation-enabled spool required")
        self.allow_operations = allow_operations
        self.spool = spool
        self.collector = collector
        self.producer_id = producer_id
        self.incarnation = incarnation
        self.collector_healthy = True

    def emit(self, event_type: EventType, *, call_id: str | None = None,
             task_id: str | None = None, work_item_id: str | None = None,
             attempt_id: str | None = None, model_execution_id: str | None = None,
             worker_instance_id: str | None = None,
             requested_model: str | None = None, reported_model: str | None = None,
             config_version: str | None = None, body: bytes | None = None,
             role: str = "assistant", direction: str = "out", payload: dict | None = None,
             message_id: str | None = None, chunk_sequence: int | None = None,
             content_id: str | None = None) -> bool:
        event_id = str(uuid.uuid4())
        p = dict(payload or {})
        content = None
        if body is not None:
            content_id = content_id or str(uuid.uuid4())
            content = ContentObject.from_bytes(content_id, body, role=role, direction=direction,
                                               content_type="text/plain", origin="observed",
                                               capture_layer="bridge", completeness="complete",
                                               message_id=message_id)
            if event_type in (EventType.MESSAGE_SENT, EventType.MESSAGE_RECEIVED):
                p.update({"message_id": message_id or str(uuid.uuid4()), "role": role,
                          "direction": direction, "original_content_id": content_id})
                p.setdefault("effective_content_id", content_id)
            elif event_type == EventType.RESPONSE_DELTA:
                p.update({"message_id": message_id or str(uuid.uuid4()),
                          "chunk_sequence": chunk_sequence or 1, "content_id": content_id})
            else:
                p["content_id"] = content_id
        event = EventEnvelope(event_id=event_id, event_type=event_type, schema_version=1,
            producer_id=self.producer_id, producer_incarnation=self.incarnation,
            producer_sequence=self.spool.allocate_sequence(), occurred_at=datetime.now(timezone.utc),
            origin="observed", observation_layer="bridge", capture_completeness="complete",
            task_id=task_id, work_item_id=work_item_id, call_id=call_id, attempt_id=attempt_id,
            model_execution_id=model_execution_id,
            worker_instance_id=worker_instance_id, requested_model=requested_model,
            reported_model=reported_model, config_version=config_version, payload=p)
        stored = self.spool.append(event, content)
        if stored:
            self._flush_pending()
        return stored

    def emit_operation(self, event_type: str, *, operation_id: str, operation_kind: str,
                       task_id: str, work_item_id: str, payload: dict, origin: str,
                       capture_completeness: str, **correlation) -> bool:
        """Record an observation only; never execute the described operation."""
        if not self.allow_operations:
            raise ValueError("operation capture disabled")
        allowed = {"session_id", "call_id", "attempt_id", "decision_id", "parent_operation_id",
                   "model_execution_id", "worker_instance_id", "parent_event_id",
                   "requested_model", "reported_model", "config_version"}
        if not correlation.keys() <= allowed:
            raise ValueError("unsupported operation correlation field")
        from contracts.event_reader import read_event
        raw = dict(correlation, schema_version=2, event_type=event_type,
                   event_id=str(uuid.uuid4()), producer_id=self.producer_id,
                   producer_incarnation=self.incarnation, producer_sequence=1,
                   occurred_at=datetime.now(timezone.utc), origin=origin,
                   observation_layer="bridge", capture_completeness=capture_completeness,
                   operation_id=operation_id, operation_kind=operation_kind,
                   task_id=task_id, work_item_id=work_item_id, payload=payload)
        # Validate before allocating durable sequence state.
        decoded = read_event(raw, allow_candidate=True)
        if decoded.kind != "operation_v2":
            raise ValueError("operation event required")
        raw["producer_sequence"] = self.spool.allocate_sequence()
        event = read_event(raw, allow_candidate=True).event
        stored = self.spool.append(event)
        if stored:
            self._flush_pending()
        return stored

    def _flush_pending(self) -> None:
        with self.spool.drain_lock():
            while entry := self.spool.first():
                seq, event, content = entry
                if not isinstance(event, EventEnvelope) and not self.allow_operations:
                    self.collector_healthy = False
                    break
                try:
                    receipts = self.collector.ingest_batch(self.producer_id, [event],
                                                           [content] if content else [])
                    if len(receipts) != 1 or receipts[0] not in ("committed", "duplicate"):
                        raise RuntimeError("collector did not acknowledge event")
                except Exception:
                    self.collector_healthy = False
                    break
                self.spool.acknowledge(seq)
                self.collector_healthy = True

    def replay(self) -> None:
        self._flush_pending()
        if not self.collector_healthy or self.spool.pending_count():
            return
        count = self.spool.unreported_gaps()
        if count:
            if self.emit(EventType.CAPTURE_GAP, payload={"gap_count": count,
                                                        "reason": "spool_overflow"}):
                self.spool.mark_gaps_reported()
                self.emit(EventType.CAPTURE_RECOVERED, payload={"known_gaps": self.spool.known_gaps()})

    def may_admit_new_work(self, degraded_admission: str) -> bool:
        if degraded_admission == "allow_with_gaps":
            return True
        if getattr(self.spool, "quarantined_count", lambda: 0)():
            return False
        if self.spool.unreported_gaps():
            return False
        # Multiple bridge processes can briefly share a pending spool entry.
        # Give its collector acknowledgement a bounded chance to settle before
        # treating that ordinary contention as a capture outage.
        deadline = time.monotonic() + 0.5
        while True:
            if self.spool.pending_count() == 0 and self.collector_healthy:
                return True
            self._flush_pending()
            if self.spool.pending_count() == 0 and self.collector_healthy:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.025)

    def health(self) -> dict:
        quarantined = getattr(self.spool, "quarantined_count", lambda: 0)()
        result = {"state": "healthy" if self.collector_healthy and not self.spool.pending_count() and not quarantined else "degraded",
                "known_gaps": self.spool.known_gaps(), "pending": self.spool.pending_count(),
                "spool_pressure": self.spool.pressure()}
        if quarantined:
            result["quarantined"] = quarantined
        return result
