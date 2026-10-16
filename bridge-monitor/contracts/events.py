from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    TASK_CREATED = "task.created"
    WORK_ITEM_CREATED = "work_item.created"
    WORKER_STARTED = "worker.started"
    WORKER_HEARTBEAT = "worker.heartbeat"
    WORKER_STOPPED = "worker.stopped"
    WORKER_LOST = "worker.lost"
    WORKER_RECONCILED = "worker.reconciled"
    CALL_RECEIVED = "call.received"
    CALL_QUEUED = "call.queued"
    CALL_DISPATCHED = "call.dispatched"
    CALL_COMPLETED = "call.completed"
    CALL_FAILED = "call.failed"
    CALL_CANCELLED = "call.cancelled"
    ATTEMPT_STARTED = "attempt.started"
    ATTEMPT_WAITING = "attempt.waiting"
    ATTEMPT_RESUMED = "attempt.resumed"
    ATTEMPT_COMPLETED = "attempt.completed"
    ATTEMPT_FAILED = "attempt.failed"
    ATTEMPT_UNCERTAIN = "attempt.uncertain"
    CAPACITY_RESERVED = "capacity.reserved"
    CAPACITY_RELEASED = "capacity.released"
    ADMISSION_BLOCKED = "admission.blocked"
    QUEUE_EXPIRED = "queue.expired"
    MODEL_SELECTED = "model.selected"
    MODEL_REPORTED = "model.reported"
    MESSAGE_SENT = "message.sent"
    MESSAGE_RECEIVED = "message.received"
    RESPONSE_STARTED = "response.started"
    RESPONSE_DELTA = "response.delta"
    RESPONSE_COMPLETED = "response.completed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    DELEGATION_DECIDED = "delegation.decided"
    FALLBACK_REQUESTED = "fallback.requested"
    FALLBACK_STARTED = "fallback.started"
    FALLBACK_COMPLETED = "fallback.completed"
    FALLBACK_FAILED = "fallback.failed"
    FALLBACK_CANCELLED = "fallback.cancelled"
    FALLBACK_TIMED_OUT = "fallback.timed_out"
    DISPOSITION_RECORDED = "disposition.recorded"
    RESULT_DELIVERED = "result.delivered"
    WORK_RESOLVED = "work.resolved"
    USAGE_REPORTED = "usage.reported"
    CAPTURE_GAP = "capture.gap"
    SPOOL_PRESSURE = "capture.spool_pressure"
    CAPTURE_DEGRADED = "capture.degraded"
    CAPTURE_RECOVERED = "capture.recovered"
    CONFIG_ACTIVATED = "config.activated"
    CONFIG_REJECTED = "config.rejected"


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    event_type: EventType
    schema_version: int = Field(ge=1)
    producer_id: str = Field(min_length=1)
    producer_incarnation: str = Field(min_length=1)
    producer_sequence: int = Field(ge=1)
    occurred_at: datetime
    received_at: datetime | None = None
    origin: Literal["observed", "imported", "synthetic"]
    observation_layer: str = Field(min_length=1)
    capture_completeness: Literal["complete", "partial", "unavailable", "removed", "unknown"]
    task_id: str | None = None
    work_item_id: str | None = None
    session_id: str | None = None
    call_id: str | None = None
    attempt_id: str | None = None
    model_execution_id: str | None = None
    worker_instance_id: str | None = None
    parent_event_id: str | None = None
    requested_model: str | None = None
    reported_model: str | None = None
    config_version: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at", "received_at")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value)):
            raise ValueError("UTC timestamp required")
        return value


class DashboardStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: int = Field(ge=0)
    queued: int = Field(ge=0)
    held_uncertain: int = Field(ge=0)
    blocked: int = Field(ge=0)
    capture_state: Literal["healthy", "degraded", "unknown"]
    known_gaps: int = Field(ge=0)
    spool_pressure: float | None = Field(default=None, ge=0, le=1)


class ProducerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    producer_id: str = Field(min_length=1)
    incarnation: str = Field(min_length=1)
    credential_reference: str = Field(min_length=1)
