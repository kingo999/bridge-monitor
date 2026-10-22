import hashlib
import importlib.metadata
import json
from datetime import datetime
from pathlib import Path

from contracts.operations import operation_event_json_schema

ROOT = Path(__file__).parents[2]
SCHEMA = ROOT / 'contracts/schemas/operation_lifecycle_v2.schema.json'
PROVENANCE = ROOT / 'tests/fixtures/operation_lifecycle/schema-provenance.json'
NAMES = {'operation.started', 'operation.completed', 'operation.failed', 'operation.cancelled', 'operation.unknown', 'operation.verification_recorded', 'operation.delivery_recorded'}


def test_schema_matches_public_adapter():
    saved = json.loads(SCHEMA.read_text())
    assert saved == operation_event_json_schema()
    assert saved['discriminator']['propertyName'] == 'event_type'
    assert set(saved['discriminator']['mapping']) == NAMES
    defs = saved['$defs']
    for reference in saved['discriminator']['mapping'].values():
        envelope = defs[reference.rsplit('/', 1)[1]]
        assert envelope['additionalProperties'] is False
        assert envelope['properties']['schema_version']['const'] == 2
        assert {'schema_version', 'event_type', 'operation_id', 'task_id', 'work_item_id', 'payload'} <= set(envelope['required'])
        assert envelope['properties']['event_id']['maxLength'] == 256
        assert envelope['properties']['operation_kind']['maxLength'] == 128
        assert envelope['properties']['observation_layer']['maxLength'] == 128
        payload = defs[envelope['properties']['payload']['$ref'].rsplit('/', 1)[1]]
        assert payload['additionalProperties'] is False
    required = {
        'StartedPayload': {'start_basis'}, 'CompletedPayload': set(),
        'FailedPayload': {'failure_code', 'retryable'},
        'CancelledPayload': {'cancelled_by', 'reason_code'},
        'UnknownPayload': {'unknown_reason', 'last_observed_at'},
        'VerificationRecordedPayload': {'verification_id', 'verifier_id', 'verdict', 'input_sha256', 'evidence_refs'},
        'DeliveryRecordedPayload': {'delivery_id', 'result_ref', 'delivery_status'},
    }
    for name, fields in required.items(): assert set(defs[name].get('required', [])) == fields
    assert defs['FailedPayload']['properties']['failure_code']['maxLength'] == 128
    assert defs['VerificationRecordedPayload']['properties']['evidence_refs']['maxItems'] == 32
    assert defs['VerificationRecordedPayload']['properties']['evidence_refs']['items']['maxLength'] == 256
    assert defs['VerificationRecordedPayload']['properties']['input_sha256']['pattern'] == '^[a-f0-9]{64}$'


def test_schema_provenance():
    record = json.loads(PROVENANCE.read_text())
    assert record['generator'] == 'contracts.operations.operation_event_json_schema'
    assert record['source_sha256'] == hashlib.sha256((ROOT / 'contracts/operations.py').read_bytes()).hexdigest()
    assert record['schema_sha256'] == hashlib.sha256(SCHEMA.read_bytes()).hexdigest()
    assert record['pydantic_version'] == importlib.metadata.version('pydantic')
    assert record['project_name'] == 'bridge-monitor'
    assert record['project_version'] == '0.1.0'
    assert datetime.fromisoformat(record['generated_at']).utcoffset() is not None
