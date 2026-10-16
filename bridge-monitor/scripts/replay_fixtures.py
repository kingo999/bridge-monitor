import json
from datetime import datetime, timedelta
from pathlib import Path

from contracts.content import ContentObject
from contracts.events import EventEnvelope, EventType
from service.ingest.store import ingest_batch


def replay_fixture(dsn: str, source: Path) -> list[str]:
    fixture = json.loads(source.read_text(encoding="utf-8"))
    start = datetime.fromisoformat(fixture["created_at"].replace("Z", "+00:00"))
    producer = fixture["producer_id"]
    incarnation = fixture["producer_incarnation"]
    if "events" in fixture:
        records = []
        for sequence, source_event in enumerate(fixture["events"], start=1):
            details = dict(source_event)
            records.append(EventEnvelope(
                event_id=details.pop("event_id"),
                event_type=details.pop("event_type"),
                schema_version=fixture["schema_version"],
                producer_id=producer, producer_incarnation=incarnation,
                producer_sequence=sequence,
                occurred_at=start + timedelta(milliseconds=sequence),
                origin="synthetic", observation_layer="fixture",
                capture_completeness=fixture.get("capture_completeness", "unknown"),
                **details,
            ))
        return ingest_batch(dsn, producer, records, [])
    seq = 0
    events = []
    content = []

    def add(kind: EventType, when: datetime, *, task_id=None, work_item_id=None,
            call_id=None, attempt_id=None, model_execution_id=None,
            payload=None, requested_model=None, reported_model=None):
        nonlocal seq
        seq += 1
        events.append(EventEnvelope(
            event_id=f"demo-event-{seq:04d}", event_type=kind, schema_version=1,
            producer_id=producer, producer_incarnation=incarnation,
            producer_sequence=seq, occurred_at=when, origin="synthetic",
            observation_layer="fixture", capture_completeness="complete",
            task_id=task_id, work_item_id=work_item_id, call_id=call_id,
            attempt_id=attempt_id, model_execution_id=model_execution_id,
            requested_model=requested_model, reported_model=reported_model,
            payload=payload or {}))

    root = fixture["root_task_id"]
    add(EventType.TASK_CREATED, start, task_id=root, payload={"scope": {"kind": "demonstration"}})
    item_no = 0
    for group in fixture["groups"]:
        for _ in range(group["count"]):
            item_no += 1
            item = f"demo-work-{item_no:03d}"
            when = start + timedelta(seconds=item_no)
            add(EventType.WORK_ITEM_CREATED, when, task_id=root, work_item_id=item,
                payload={"category": "arithmetic"})
            route = group["name"]
            if route == "paid_direct":
                add(EventType.DELEGATION_DECIDED, when + timedelta(milliseconds=100),
                    task_id=root, work_item_id=item,
                    payload={"decision_id": f"decision-{item}", "route": "paid",
                             "eligibility": "eligible", "reason": "paid_owner_selected"})
                add(EventType.WORK_RESOLVED, when + timedelta(milliseconds=900),
                    task_id=root, work_item_id=item,
                    payload={"resolution_id": f"resolution-{item}", "outcome": "paid_direct"})
                continue
            call = f"demo-call-{item_no:03d}"
            attempt = f"demo-attempt-{item_no:03d}"
            model_execution = f"demo-model-{item_no:03d}"
            result = f"demo-result-{item_no:03d}"
            prompt_content = f"demo-prompt-{item_no:03d}"
            response_content = f"demo-response-{item_no:03d}"
            content.extend([
                ContentObject.from_bytes(prompt_content, b"What is 2 + 2?\n",
                    role="user", direction="out", content_type="text/plain",
                    origin="synthetic", capture_layer="fixture"),
                ContentObject.from_bytes(response_content, b"4\n",
                    role="assistant", direction="in", content_type="text/plain",
                    origin="synthetic", capture_layer="fixture"),
            ])
            add(EventType.CALL_RECEIVED, when + timedelta(milliseconds=100),
                task_id=root, work_item_id=item, call_id=call, requested_model="qwen-seed")
            add(EventType.ATTEMPT_STARTED, when + timedelta(milliseconds=110),
                task_id=root, work_item_id=item, call_id=call, attempt_id=attempt,
                payload={"attempt_number": 1})
            add(EventType.MODEL_SELECTED, when + timedelta(milliseconds=120),
                task_id=root, work_item_id=item, call_id=call, attempt_id=attempt,
                model_execution_id=model_execution, requested_model="qwen-seed")
            add(EventType.MODEL_REPORTED, when + timedelta(milliseconds=130),
                task_id=root, work_item_id=item, call_id=call, attempt_id=attempt,
                model_execution_id=model_execution, reported_model="qwen-seed")
            add(EventType.DELEGATION_DECIDED, when + timedelta(milliseconds=200),
                task_id=root, work_item_id=item, call_id=call,
                payload={"decision_id": f"decision-{item}", "route": "local",
                         "eligibility": "eligible", "guidance_manifest": {"state": "no_guidance", "version": 1}})
            add(EventType.MESSAGE_SENT, when + timedelta(milliseconds=250),
                task_id=root, work_item_id=item, call_id=call,
                payload={"message_id": f"demo-message-{item_no:03d}", "role": "user", "direction": "out",
                         "original_content_id": prompt_content,
                         "effective_content_id": prompt_content})
            add(EventType.CALL_DISPATCHED, when + timedelta(milliseconds=300),
                task_id=root, work_item_id=item, call_id=call,
                payload={"destination": "skynet"})
            add(EventType.RESULT_DELIVERED, when + timedelta(milliseconds=600),
                task_id=root, work_item_id=item, call_id=call, attempt_id=attempt,
                payload={"result_id": result,
                         "raw_content_id": response_content,
                         "delivered_content_id": response_content})
            if route != "missing_disposition":
                add(EventType.DISPOSITION_RECORDED, when + timedelta(milliseconds=700),
                    task_id=root, work_item_id=item, call_id=call,
                    payload={"disposition_id": f"disp-{item}", "result_id": result,
                             "actor_id": "claude-seed", "stage": "claude", "judgment": route})
            outcome = {"accepted": "local_retained", "revised": "paid_revision_with_contribution",
                       "replaced": "paid_replaced", "unused": "unresolved",
                       "missing_disposition": "unknown"}[route]
            add(EventType.WORK_RESOLVED, when + timedelta(milliseconds=900),
                task_id=root, work_item_id=item,
                payload={"resolution_id": f"resolution-{item}", "outcome": outcome,
                         "final_result_id": result if route == "accepted" else None})
    add(EventType.CAPACITY_RESERVED, start + timedelta(minutes=3),
        payload={"active": 1, "queued": 2, "held_uncertain": 1, "blocked": 3})
    add(EventType.SPOOL_PRESSURE, start + timedelta(minutes=3, milliseconds=100),
        payload={"spool_pressure": 0.8, "capture_state": "degraded"})
    add(EventType.CAPTURE_GAP, start + timedelta(minutes=3, milliseconds=200),
        payload={"known_gaps": 1, "capture_state": "degraded"})
    add(EventType.CAPTURE_RECOVERED, start + timedelta(minutes=3, milliseconds=300),
        payload={"capture_state": "healthy"})
    return ingest_batch(dsn, producer, events, content)


if __name__ == "__main__":
    import os
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m scripts.replay_fixtures path-to-fixture.json")
    dsn = os.environ.get("BRIDGE_MONITOR_TEST_DSN")
    if not dsn:
        raise SystemExit("BRIDGE_MONITOR_TEST_DSN is required")
    receipts = replay_fixture(dsn, Path(sys.argv[1]))
    print({state: receipts.count(state) for state in sorted(set(receipts))})
