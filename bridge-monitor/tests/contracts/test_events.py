from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from contracts.events import EventEnvelope, EventType, DashboardStatus
from contracts.content import ContentObject
from contracts.outcomes import DispositionRecord


def event(**changes):
    values = dict(
        event_id="e-1", event_type=EventType.CALL_QUEUED, schema_version=1,
        producer_id="bridge-a", producer_incarnation="boot-1", producer_sequence=1,
        occurred_at=datetime(2026, 10, 16, tzinfo=timezone.utc),
        origin="synthetic", observation_layer="bridge", capture_completeness="complete",
        payload={"call_id": "c-1"},
    )
    values.update(changes)
    return EventEnvelope(**values)


def test_event_requires_utc_and_positive_sequence():
    assert event().producer_sequence == 1
    with pytest.raises(ValidationError):
        event(occurred_at=datetime(2026, 10, 16))
    with pytest.raises(ValidationError):
        event(producer_sequence=0)


def test_capture_and_model_unknowns_are_explicit():
    item = event(event_type=EventType.CAPTURE_GAP, capture_completeness="unknown",
                 requested_model="claude-alias", reported_model=None)
    assert item.reported_model is None
    assert item.capture_completeness == "unknown"
    assert "capture.gap" == item.event_type.value


def test_capacity_snapshot_keeps_uncertainty_and_capture_gap():
    status = DashboardStatus(active=1, queued=2, held_uncertain=1, blocked=3,
                             capture_state="degraded", known_gaps=1, spool_pressure=0.8)
    assert status.held_uncertain == 1
    assert status.known_gaps == 1
    with pytest.raises(ValidationError):
        DashboardStatus(active=-1, queued=0, held_uncertain=0, blocked=0,
                        capture_state="healthy", known_gaps=0)


def test_exact_content_hash_and_completeness():
    raw = b" {\"x\": 1, \"y\": 2}\n"
    item = ContentObject.from_bytes("body-1", raw, role="assistant", direction="out",
                                    content_type="application/json", origin="synthetic",
                                    capture_layer="runtime")
    assert item.raw_bytes == raw
    assert item.byte_length == len(raw)
    assert item.sha256 == "65b8347692a5181ede99214483c9b44a85a9ce955f0be2760baaf7fa91b0959a"
    with pytest.raises(ValidationError):
        ContentObject(content_id="bad", raw_bytes=raw[:2], byte_length=len(raw),
                      sha256=item.sha256, role="assistant", direction="out",
                      content_type="application/json", origin="synthetic",
                      capture_layer="runtime", completeness="complete")


def test_invalid_disposition_is_rejected():
    with pytest.raises(ValidationError):
        DispositionRecord(disposition_id="d-1", result_id="r-1", actor_id="claude-1",
                          stage="claude", judgment="maybe")
