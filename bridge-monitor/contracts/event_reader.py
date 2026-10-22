"""Explicit historical/candidate decoding, with no production-path registration."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from contracts.events import EventEnvelope, EventType
from contracts.operations import (
    OPERATION_EVENT_CLASSES, OPERATION_EVENT_TYPES, OperationEvent,
    _validate_operation_event, operation_event_json_schema,
)

_LEGACY_NAMES = frozenset(item.value for item in EventType)
_SCHEMA = operation_event_json_schema()
_FIELDS = frozenset(EventEnvelope.model_fields) | frozenset(
    field for definition in _SCHEMA.get('$defs', {}).values()
    for field in definition.get('properties', {})
)


class EventReadError(ValueError):
    """Sanitized rejection; issues contain only static field paths and indices."""

    def __init__(self, code: str, issues: tuple[tuple[str | int, ...], ...] = ()):
        self.code = code
        self.issues = issues
        super().__init__(code, issues)


@dataclass(frozen=True)
class DecodedEvent:
    kind: Literal['legacy', 'operation_v2']
    event: EventEnvelope | OperationEvent

    def __post_init__(self):
        if self.kind == 'legacy' and isinstance(self.event, EventEnvelope):
            return
        if self.kind == 'operation_v2' and isinstance(self.event, OPERATION_EVENT_CLASSES):
            return
        raise TypeError('decoded event tag/model mismatch')


def _issues(error: ValidationError) -> tuple[tuple[str | int, ...], ...]:
    paths = []
    for issue in error.errors(include_input=False, include_context=False, include_url=False):
        parts = tuple(part for part in issue['loc'] if part not in OPERATION_EVENT_TYPES)
        # Unknown JSON keys are data too. Do not echo them into diagnostics.
        paths.append(tuple(part if isinstance(part, int) or part in _FIELDS else '<extra>' for part in parts))
    return tuple(paths)


def read_event(raw: bytes | str | dict[str, object], *, allow_candidate: bool = False) -> DecodedEvent:
    """Decode without activating a producer, store, projection or external effect.

    Legacy positive numeric versions retain the existing parser's semantics.
    Only known operation names at integer schema version 2 can opt in.
    """
    if type(allow_candidate) is not bool:
        raise TypeError('allow_candidate must be a boolean')
    if isinstance(raw, (str, bytes)):
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise EventReadError('malformed_json') from None
    else:
        value = raw
    if not isinstance(value, dict):
        raise EventReadError('invalid_envelope')
    name = value.get('event_type')
    if not isinstance(name, str):
        raise EventReadError('invalid_envelope', (('event_type',),))
    if name in _LEGACY_NAMES:
        try:
            event = (EventEnvelope.model_validate_json(raw) if isinstance(raw, (str, bytes))
                     else EventEnvelope.model_validate(raw))
        except ValidationError as error:
            raise EventReadError('invalid_envelope', _issues(error)) from None
        return DecodedEvent('legacy', event)
    if name not in OPERATION_EVENT_TYPES:
        raise EventReadError('unknown_event_type', (('event_type',),))
    if not allow_candidate:
        raise EventReadError('candidate_disabled')
    version = value.get('schema_version')
    if type(version) is not int or version != 2:
        raise EventReadError('unsupported_version', (('schema_version',),))
    try:
        event = _validate_operation_event(value)
    except ValidationError as error:
        paths = _issues(error)
        code = 'invalid_payload' if paths and all(p and p[0] == 'payload' for p in paths) else 'invalid_envelope'
        raise EventReadError(code, paths) from None
    return DecodedEvent('operation_v2', event)


def encode_event(decoded: DecodedEvent) -> bytes:
    """Serialize the enclosed contract; never upgrade or translate it."""
    if not isinstance(decoded, DecodedEvent):
        raise TypeError('DecodedEvent required')
    return decoded.event.model_dump_json().encode('utf-8')
