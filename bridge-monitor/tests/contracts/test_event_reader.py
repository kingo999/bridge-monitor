import copy
import dataclasses
import importlib
import json
import sqlite3
import sys
import traceback
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from .candidate_helpers import candidate, golden, no_effects
from contracts.event_reader import DecodedEvent, EventReadError, encode_event, read_event
from contracts.events import EventEnvelope
from contracts.operations import candidate_event_hash
from service.ingest.store import _event_hash, ingest_batch
from bridge.telemetry.spool import DurableSpool
from bridge.telemetry.emitter import TelemetryEmitter


@pytest.mark.parametrize('pinned', golden(), ids=lambda g: str(g['input']['event_id']) + '-v' + str(g['input']['schema_version']))
def test_legacy_delegation_matches_goldens(pinned):
    result = read_event(pinned['input'])
    assert result.kind == 'legacy'
    assert type(result.event) is EventEnvelope
    assert result.event.model_dump(mode='json') == pinned['parsed']
    assert _event_hash(result.event) == pinned['sha256']
    assert json.dumps(result.event.model_dump(mode='json', exclude={'received_at'}), sort_keys=True, separators=(',', ':')) == pinned['canonical']
    if result.event.event_type.value == 'tool.completed':
        assert result.event.payload == {'boundary': 'content_block_stop'}


@pytest.mark.parametrize('kind', ['started', 'completed', 'failed', 'cancelled', 'unknown', 'verification_recorded', 'delivery_recorded'])
def test_reader_dispatch_matrix(kind):
    raw = candidate(kind)
    with pytest.raises(EventReadError) as error: read_event(raw)
    assert error.value.code == 'candidate_disabled'
    result = read_event(raw, allow_candidate=True)
    assert result.kind == 'operation_v2'
    assert result.event.event_type == raw['event_type']


@pytest.mark.parametrize('flag', [1, 0, 'false', None, [], {}])
def test_opt_in_is_a_boolean(flag):
    with pytest.raises(TypeError): read_event(candidate(), allow_candidate=flag)


@pytest.mark.parametrize(('mutate', 'code'), [
    (lambda e: e.update(schema_version=3), 'unsupported_version'),
    (lambda e: e.update(schema_version=2.0), 'unsupported_version'),
    (lambda e: e.update(schema_version='2'), 'unsupported_version'),
    (lambda e: e.update(schema_version=True), 'unsupported_version'),
    (lambda e: e.pop('schema_version'), 'unsupported_version'),
    (lambda e: e.update(event_type='operation.future'), 'unknown_event_type'),
    (lambda e: e.update(event_type=42), 'invalid_envelope'),
    (lambda e: e.pop('event_type'), 'invalid_envelope'),
    (lambda e: e.update(extra='extra'), 'invalid_envelope'),
    (lambda e: e.pop('task_id'), 'invalid_envelope'),
    (lambda e: e.update(payload={'start_basis':'unknown'}), 'invalid_payload'),
    (lambda e: e.update(payload={}), 'invalid_payload'),
    (lambda e: e.update(payload={'start_basis':'observed', 'extra':True}), 'invalid_payload'),
    (lambda e: e.update(attempt_id='unowned', payload={}), 'invalid_envelope'),
    (lambda e: e.update(producer_sequence=True, payload={}), 'invalid_envelope'),
])
def test_error_precedence_and_input_parity(mutate, code):
    raw = candidate(); mutate(raw)
    issues = []
    for value in [raw, json.dumps(raw), json.dumps(raw).encode()]:
        with pytest.raises(EventReadError) as error: read_event(value, allow_candidate=True)
        assert error.value.code == code
        issues.append(error.value.issues)
    assert issues[0] == issues[1] == issues[2]


@pytest.mark.parametrize(('raw', 'code'), [
    ('{', 'malformed_json'), (b'\xff', 'malformed_json'),
    ('[]', 'invalid_envelope'), ('null', 'invalid_envelope'),
    ([], 'invalid_envelope'), (42, 'invalid_envelope'),
])
def test_json_shape_errors(raw, code):
    with pytest.raises(EventReadError) as error: read_event(raw, allow_candidate=True)
    assert error.value.code == code


def test_disabled_precedes_candidate_shape_errors():
    raw = candidate(); raw['schema_version'] = 3; raw['payload'] = {}
    with pytest.raises(EventReadError) as error: read_event(raw)
    assert error.value.code == 'candidate_disabled'


def test_mapping_string_bytes_parity():
    for raw in [candidate(), golden()[0]['input']]:
        values = [raw, json.dumps(raw), json.dumps(raw).encode()]
        results = [read_event(v, allow_candidate=True) for v in values]
        assert all(r.kind == results[0].kind and r.event.model_dump(mode='json') == results[0].event.model_dump(mode='json') for r in results)
        raw = copy.deepcopy(raw); raw['occurred_at'] = datetime(2026, 10, 22, tzinfo=timezone.utc)
        assert read_event(raw, allow_candidate=True).event.model_dump(mode='json') == results[0].event.model_dump(mode='json')


def test_round_trip_preserves_contract():
    for raw in [candidate(k) for k in ['started','completed','failed','cancelled','unknown','verification_recorded','delivery_recorded']] + [g['input'] for g in golden()]:
        first = read_event(raw, allow_candidate=True)
        second = read_event(encode_event(first), allow_candidate=True)
        assert first.kind == second.kind
        assert first.event.model_dump(mode='json') == second.event.model_dump(mode='json')
        hasher = _event_hash if first.kind == 'legacy' else candidate_event_hash
        assert hasher(first.event) == hasher(second.event)


def test_error_redaction():
    marker = 'UNIQUE_PRIVATE_VALUE_123'
    values = [candidate(), candidate(), golden()[0]['input']]
    values[0]['event_id'] = {'private': marker}
    values[1]['payload']['start_basis'] = marker
    values[2] = {**values[2], 'producer_sequence': marker}
    for raw in values:
        try: read_event(raw, allow_candidate=True)
        except EventReadError as error:
            rendered = ''.join(traceback.format_exception(error))
            assert marker not in str(error) + repr(error) + repr(error.issues) + rendered
            assert set(vars(error)) <= {'code', 'issues'}
        else: pytest.fail('Expected sanitized rejection')


def test_wrapper_is_not_a_legacy_envelope(no_effects):
    decoded = read_event(candidate(), allow_candidate=True)
    for name in ['producer_id', 'event_id', 'payload', 'model_dump']:
        assert not hasattr(decoded, name)
    with pytest.raises(AttributeError): ingest_batch('must-not-connect', 'fixture', [decoded], [])
    with pytest.raises(dataclasses.FrozenInstanceError): decoded.kind = 'legacy'
    with pytest.raises(TypeError): DecodedEvent('legacy', decoded.event)
    with pytest.raises(TypeError): DecodedEvent('operation_v2', read_event(golden()[0]['input']).event)
    with pytest.raises(TypeError): DecodedEvent('invalid', decoded.event)


def test_parser_has_no_external_effects(no_effects):
    # Initial imports are already covered by the process-wide audit/DB guards.
    importlib.import_module('contracts.operations')
    importlib.import_module('contracts.event_reader')
    for raw in [candidate(), golden()[0]['input']]:
        decoded = read_event(raw, allow_candidate=True)
        assert read_event(encode_event(decoded), allow_candidate=True).kind == decoded.kind
    assert no_effects == []


def test_old_spool_preserves_candidate_and_tail_and_declared_recovery(tmp_path):
    spool = DurableSpool(tmp_path / 'candidate.sqlite', limit_bytes=200000)
    candidate_event = read_event(candidate(), allow_candidate=True).event
    legacy = read_event(golden()[0]['input']).event
    assert spool.append(candidate_event) and spool.append(legacy)
    class StubCollector:
        def __init__(self): self.calls = []
        def ingest_batch(self, *args): self.calls.append(args); return ['committed']
    collector = StubCollector()
    emitter = TelemetryEmitter(spool, collector, producer_id='fixture', incarnation='candidate')
    with pytest.raises(ValidationError): emitter.replay()
    assert spool.pending_count() == 2 and spool.known_gaps() == 0 and collector.calls == []
    recovered = []
    # Explicit fixture adapter: production first()/emitter are NOT changed.
    with spool.drain_lock():
        with sqlite3.connect(spool.path) as conn:
            rows = conn.execute('SELECT seq,event_json FROM entries ORDER BY seq').fetchall()
        for seq, raw in rows:
            decoded = read_event(raw, allow_candidate=True)
            assert collector.ingest_batch(decoded) == ['committed']
            recovered.append(decoded.kind)
            spool.acknowledge(seq)
    assert recovered == ['operation_v2', 'legacy'] and spool.pending_count() == 0
    assert len(collector.calls) == 2


@pytest.mark.parametrize('as_bytes', [False, True])
def test_deep_json_has_a_stable_rejection(as_bytes):
    depth = sys.getrecursionlimit() + 10000
    raw = '[' * depth + '0' + ']' * depth
    with pytest.raises(EventReadError) as error:
        read_event(raw.encode() if as_bytes else raw, allow_candidate=True)
    assert error.value.code == 'malformed_json'


def test_unknown_mapping_keys_are_not_diagnostic_values():
    for key in [9876543210123456, True, 'PRIVATE_UNKNOWN_KEY']:
        raw = candidate(); raw[key] = 'private'
        with pytest.raises(EventReadError) as error: read_event(raw, allow_candidate=True)
        assert str(key) not in repr(error.value.issues)
        assert error.value.issues == (('<extra>',),)
    raw = candidate('verification_recorded'); raw['payload']['evidence_refs'] = [' ']
    with pytest.raises(EventReadError) as error: read_event(raw, allow_candidate=True)
    assert error.value.issues == (('payload', 'evidence_refs', 0),)
