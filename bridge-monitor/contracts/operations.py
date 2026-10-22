"""Opt-in operation facts. This contract is not wired into production ingestion."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, StrictBool,
    StringConstraints, TypeAdapter, model_validator,
)


def _nonblank(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('nonblank string required')
    return value


Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256), BeforeValidator(_nonblank)]
ShortIdentifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128), BeforeValidator(_nonblank)]
Sha256 = Annotated[str, StringConstraints(strict=True, pattern=r'^[a-f0-9]{64}$')]


def _utc(value: object) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('UTC ISO timestamp required') from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError('UTC timestamp required')
    return value


UtcTimestamp = Annotated[datetime, BeforeValidator(_utc)]


def _version(value: object) -> int:
    if type(value) is not int or value != 2:
        raise ValueError('integer schema_version 2 required')
    return value


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')


class StartedPayload(_FrozenModel):
    start_basis: Literal['observed', 'inferred']


class CompletedPayload(_FrozenModel):
    result_ref: Identifier | None = None


class FailedPayload(_FrozenModel):
    failure_code: ShortIdentifier
    retryable: StrictBool


class CancelledPayload(_FrozenModel):
    cancelled_by: Literal['user', 'parent', 'worker', 'system']
    reason_code: ShortIdentifier


class UnknownPayload(_FrozenModel):
    unknown_reason: Literal['lost_contact', 'process_exit', 'deadline_exceeded', 'capture_gap', 'unconfirmed_effect']
    last_observed_at: UtcTimestamp


def _evidence_array(value: object) -> object:
    if not isinstance(value, (list, tuple)):
        raise ValueError('evidence references require an ordered array')
    return value


class VerificationRecordedPayload(_FrozenModel):
    verification_id: Identifier
    verifier_id: Identifier
    verdict: Literal['passed', 'failed', 'inconclusive']
    input_sha256: Sha256
    evidence_refs: Annotated[tuple[Identifier, ...], Field(max_length=32), BeforeValidator(_evidence_array)]


class DeliveryRecordedPayload(_FrozenModel):
    delivery_id: Identifier
    result_ref: Identifier
    delivery_status: Literal['delivered', 'failed', 'not_attempted']


class _OperationEnvelope(_FrozenModel):
    schema_version: Annotated[Literal[2], BeforeValidator(_version)]
    event_type: Annotated[str, StringConstraints(strict=True)]
    event_id: Identifier
    producer_id: Identifier
    producer_incarnation: Identifier
    producer_sequence: Annotated[int, Field(strict=True, gt=0)]
    occurred_at: UtcTimestamp
    origin: Literal['observed', 'imported', 'synthetic']
    observation_layer: ShortIdentifier
    capture_completeness: Literal['complete', 'partial', 'unavailable', 'removed', 'unknown']
    operation_id: Identifier
    operation_kind: ShortIdentifier
    task_id: Identifier
    work_item_id: Identifier
    session_id: Identifier | None = None
    call_id: Identifier | None = None
    attempt_id: Identifier | None = None
    decision_id: Identifier | None = None
    parent_operation_id: Identifier | None = None
    model_execution_id: Identifier | None = None
    worker_instance_id: Identifier | None = None
    parent_event_id: Identifier | None = None
    requested_model: Identifier | None = None
    reported_model: Identifier | None = None
    config_version: Identifier | None = None
    received_at: UtcTimestamp | None = None
    payload: object

    @model_validator(mode='after')
    def _correlation(self):
        if (self.attempt_id is not None or self.model_execution_id is not None) and self.call_id is None:
            raise ValueError('attempt/model execution requires call_id')
        return self


class StartedEvent(_OperationEnvelope):
    event_type: Literal['operation.started']
    payload: StartedPayload


class CompletedEvent(_OperationEnvelope):
    event_type: Literal['operation.completed']
    payload: CompletedPayload


class FailedEvent(_OperationEnvelope):
    event_type: Literal['operation.failed']
    payload: FailedPayload


class CancelledEvent(_OperationEnvelope):
    event_type: Literal['operation.cancelled']
    payload: CancelledPayload


class UnknownEvent(_OperationEnvelope):
    event_type: Literal['operation.unknown']
    payload: UnknownPayload


class VerificationRecordedEvent(_OperationEnvelope):
    event_type: Literal['operation.verification_recorded']
    payload: VerificationRecordedPayload


class DeliveryRecordedEvent(_OperationEnvelope):
    event_type: Literal['operation.delivery_recorded']
    payload: DeliveryRecordedPayload


OperationEvent = Annotated[
    StartedEvent | CompletedEvent | FailedEvent | CancelledEvent | UnknownEvent
    | VerificationRecordedEvent | DeliveryRecordedEvent,
    Field(discriminator='event_type'),
]
_ADAPTER = TypeAdapter(OperationEvent)
OPERATION_EVENT_TYPES = frozenset(_ADAPTER.json_schema()['discriminator']['mapping'])
OPERATION_EVENT_CLASSES = (StartedEvent, CompletedEvent, FailedEvent, CancelledEvent, UnknownEvent, VerificationRecordedEvent, DeliveryRecordedEvent)


def _validate_operation_event(raw: bytes | str | dict[str, object]) -> OperationEvent:
    value = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    # Validate correlations/envelope first, independently of payload validity.
    _OperationEnvelope.model_validate(value)
    return _ADAPTER.validate_python(value)


def operation_event_json_schema() -> dict[str, object]:
    """Return the candidate schema without changing the legacy export registry."""
    return _ADAPTER.json_schema()


def canonical_candidate_bytes(event: OperationEvent) -> bytes:
    if not isinstance(event, OPERATION_EVENT_CLASSES):
        raise TypeError('validated operation event required')
    validated = _ADAPTER.validate_python(event)
    body = json.dumps(validated.model_dump(mode='json', exclude={'received_at'}),
                      sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return b'bridge-monitor.operation-lifecycle.v2\n' + body.encode('utf-8')


def candidate_event_hash(event: OperationEvent) -> str:
    return hashlib.sha256(canonical_candidate_bytes(event)).hexdigest()
