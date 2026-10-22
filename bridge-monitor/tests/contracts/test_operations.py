"""The candidate is an actual product contract; these fixtures grant no effects."""
import copy
from collections import deque
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.operations import (
    _validate_operation_event, canonical_candidate_bytes,
    candidate_event_hash, operation_event_json_schema, VerificationRecordedPayload,
)

FIXTURES = Path(__file__).parents[1] / 'fixtures' / 'operation_lifecycle'
EXAMPLES = json.loads((FIXTURES / 'candidate-v2.json').read_text())


def example(kind='started'):
    return copy.deepcopy(next(e for e in EXAMPLES if e['event_type'] == 'operation.' + kind))


@pytest.mark.parametrize('raw', EXAMPLES, ids=lambda e: e['event_type'])
def test_all_seven_operation_variants(raw):
    event = _validate_operation_event(raw)
    assert event.event_type == raw['event_type']
    assert event.payload.model_dump(mode='json') == raw['payload']
    assert event.schema_version == 2
    assert event.model_dump(mode='json')['operation_id'] == 'operation-1'


@pytest.mark.parametrize(('kind', 'field', 'values'), [
    ('started', 'start_basis', ['observed', 'inferred']),
    ('cancelled', 'cancelled_by', ['user', 'parent', 'worker', 'system']),
    ('unknown', 'unknown_reason', ['lost_contact', 'process_exit', 'deadline_exceeded', 'capture_gap', 'unconfirmed_effect']),
    ('verification_recorded', 'verdict', ['passed', 'failed', 'inconclusive']),
    ('delivery_recorded', 'delivery_status', ['delivered', 'failed', 'not_attempted']),
])
def test_all_payload_literals(kind, field, values):
    for value in values:
        raw = example(kind); raw['payload'][field] = value
        assert getattr(_validate_operation_event(raw).payload, field) == value
    raw['payload'][field] = 'future-value'
    with pytest.raises(ValidationError): _validate_operation_event(raw)


@pytest.mark.parametrize(('field', 'values'), [
    ('origin', ['observed', 'imported', 'synthetic']),
    ('capture_completeness', ['complete', 'partial', 'unavailable', 'removed', 'unknown']),
])
def test_origin_and_capture_literals(field, values):
    for value in values:
        raw = example(); raw[field] = value
        assert getattr(_validate_operation_event(raw), field) == value
    raw[field] = 'future-value'
    with pytest.raises(ValidationError): _validate_operation_event(raw)


@pytest.mark.parametrize('field', ['schema_version', 'event_type', 'event_id', 'producer_id', 'producer_incarnation', 'producer_sequence', 'occurred_at', 'origin', 'observation_layer', 'capture_completeness', 'operation_id', 'operation_kind', 'task_id', 'work_item_id', 'payload'])
def test_required_fields(field):
    raw = example(); del raw[field]
    with pytest.raises(ValidationError): _validate_operation_event(raw)


@pytest.mark.parametrize('field', ['event_id', 'producer_id', 'producer_incarnation', 'operation_id', 'task_id', 'work_item_id', 'session_id', 'call_id', 'attempt_id', 'decision_id', 'parent_operation_id', 'model_execution_id', 'worker_instance_id', 'parent_event_id', 'requested_model', 'reported_model', 'config_version'])
def test_identifier_bounds(field):
    raw = example('completed'); raw[field] = 'x' * 256
    assert getattr(_validate_operation_event(raw), field) == 'x' * 256
    for value in ['x' * 257, ' ', '\t', '', 123]:
        raw[field] = value
        with pytest.raises(ValidationError): _validate_operation_event(raw)
    raw[field] = ' fixture '
    assert getattr(_validate_operation_event(raw), field) == ' fixture '


@pytest.mark.parametrize('field', ['operation_kind', 'observation_layer'])
def test_short_identifier_bounds(field):
    raw = example(); raw[field] = 'x' * 128
    assert getattr(_validate_operation_event(raw), field) == 'x' * 128
    raw[field] += 'x'
    with pytest.raises(ValidationError): _validate_operation_event(raw)


@pytest.mark.parametrize(('field', 'values'), [
    ('producer_sequence', [0, -1, True, 1.5, '1']),
    ('schema_version', [True, 2.0, '2', 1, 3]),
])
def test_strict_integers(field, values):
    for value in values:
        raw = example(); raw[field] = value
        with pytest.raises(ValidationError): _validate_operation_event(raw)


@pytest.mark.parametrize('value', ['2026-10-22T00:00:00', '2026-10-22T02:00:00+02:00', 1792627200, '1792627200', datetime(2026, 10, 22)])
def test_timestamp_rejection(value):
    for field in ['occurred_at', 'received_at']:
        raw = example(); raw[field] = value
        with pytest.raises(ValidationError): _validate_operation_event(raw)
    raw = example('unknown'); raw['payload']['last_observed_at'] = value
    with pytest.raises(ValidationError): _validate_operation_event(raw)


def test_utc_mapping_and_json_parity():
    raw = example(); obj = _validate_operation_event(raw)
    assert obj == _validate_operation_event(json.dumps(raw))
    raw['occurred_at'] = datetime(2026, 10, 22, tzinfo=timezone.utc)
    assert _validate_operation_event(raw) == obj


def test_correlation():
    assert _validate_operation_event(example()).call_id is None
    for field in ['attempt_id', 'model_execution_id']:
        raw = example(); raw[field] = 'dependent'
        with pytest.raises(ValidationError): _validate_operation_event(raw)


def test_payload_validation():
    for where in ['envelope', 'payload']:
        raw = example(); (raw if where == 'envelope' else raw['payload'])['extra'] = True
        with pytest.raises(ValidationError): _validate_operation_event(raw)
    raw = example(); raw['payload'] = example('failed')['payload']
    with pytest.raises(ValidationError): _validate_operation_event(raw)
    for value in [0, 1, 'false', None]:
        raw = example('failed'); raw['payload']['retryable'] = value
        with pytest.raises(ValidationError): _validate_operation_event(raw)
    for value in ['a' * 63, 'A' * 64, 'g' * 64, 123]:
        raw = example('verification_recorded'); raw['payload']['input_sha256'] = value
        with pytest.raises(ValidationError): _validate_operation_event(raw)
    for kind, field, limit in [('failed', 'failure_code', 128), ('cancelled', 'reason_code', 128), ('verification_recorded', 'verification_id', 256), ('verification_recorded', 'verifier_id', 256), ('delivery_recorded', 'delivery_id', 256), ('delivery_recorded', 'result_ref', 256), ('completed', 'result_ref', 256)]:
        raw = example(kind); raw['payload'][field] = 'x' * limit
        _validate_operation_event(raw)
        for value in ['', ' ', 'x' * (limit + 1), 42]:
            raw['payload'][field] = value
            with pytest.raises(ValidationError): _validate_operation_event(raw)
    raw = example('verification_recorded'); raw['payload']['evidence_refs'] = ['evidence'] * 32
    _validate_operation_event(raw)
    for value in [['evidence'] * 33, [' '], ['x' * 257], [123]]:
        raw['payload']['evidence_refs'] = value
        with pytest.raises(ValidationError): _validate_operation_event(raw)


def test_candidate_immutability():
    raw = example('verification_recorded'); refs = ['fixture']; raw['payload']['evidence_refs'] = refs
    event = _validate_operation_event(raw)
    refs.append('later')
    assert event.payload.evidence_refs == ('fixture',)
    with pytest.raises(ValidationError): event.operation_id = 'mutated'
    with pytest.raises(ValidationError): event.payload.verdict = 'passed'


def test_candidate_identity():
    raw = example(); event = _validate_operation_event(raw)
    expected = b'bridge-monitor.operation-lifecycle.v2\n' + json.dumps(event.model_dump(mode='json', exclude={'received_at'}), sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    assert canonical_candidate_bytes(event) == expected
    assert b'\\u03b2' in expected
    assert candidate_event_hash(event) == hashlib.sha256(expected).hexdigest()
    raw['received_at'] = '2026-10-22T00:01:00Z'
    assert candidate_event_hash(_validate_operation_event(raw)) == candidate_event_hash(event)
    assert candidate_event_hash(_validate_operation_event(json.dumps(raw, indent=4))) == candidate_event_hash(event)
    raw['operation_kind'] = 'changed'
    assert candidate_event_hash(_validate_operation_event(raw)) != candidate_event_hash(event)


def test_schema_public_function():
    assert len(operation_event_json_schema()['discriminator']['mapping']) == 7


@pytest.mark.parametrize('container', [set, frozenset, deque, iter])
def test_evidence_rejects_non_array_containers(container):
    raw = example('verification_recorded')
    raw['payload']['evidence_refs'] = container(['first', 'second'])
    with pytest.raises(ValidationError): _validate_operation_event(raw)


def test_prebuilt_payload_is_revalidated_and_copied():
    raw = example('verification_recorded')
    values = raw['payload']; refs = ['initial']; values['evidence_refs'] = refs
    raw['payload'] = VerificationRecordedPayload.model_construct(**values)
    event = _validate_operation_event(raw)
    before = candidate_event_hash(event)
    refs.append('mutated')
    assert event.payload.evidence_refs == ('initial',)
    assert candidate_event_hash(event) == before
    values['verdict'] = 'invalid'
    raw['payload'] = VerificationRecordedPayload.model_construct(**values)
    with pytest.raises(ValidationError): _validate_operation_event(raw)
