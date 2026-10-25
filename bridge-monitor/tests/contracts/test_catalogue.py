from datetime import datetime, timedelta, timezone
import builtins
import socket
import subprocess

import pytest
from pydantic import ValidationError

from contracts.catalogue import CatalogueStatus, ModuleManifest, ModuleStatusEvidence, StatusObservation
from service.queries.catalogue import project_catalogue


NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
FIELDS = ('discovered', 'installed', 'probed', 'enabled', 'healthy', 'eligible')


def manifest(name='worker', version=1):
    return ModuleManifest(module_id=name, manifest_version=version, summary='Descriptive worker')


def observation(value=True, age=0):
    return StatusObservation(value=value, observed_at=NOW - timedelta(seconds=age), source='test-receipt')


def test_missing_evidence_is_unknown():
    row, = project_catalogue([manifest()], [], now=NOW)
    for field in FIELDS:
        status = getattr(row, field)
        assert status.value == 'unknown'
        assert status.source is None and status.observed_at is None and status.stale is None


def test_statuses_are_independent_and_provenance_survives():
    evidence = ModuleStatusEvidence(module_id='worker', manifest_version=1,
                                    healthy=observation(), enabled=observation(False))
    row, = project_catalogue([manifest()], [evidence], now=NOW)
    assert row.healthy.value is True and row.enabled.value is False
    assert row.eligible.value == 'unknown' and row.installed.value == 'unknown'
    assert row.healthy.source == 'test-receipt' and row.healthy.observed_at == NOW


def test_equal_statuses_are_valid_without_inference():
    evidence = ModuleStatusEvidence(module_id='worker', manifest_version=1,
                                    **{field: observation() for field in FIELDS})
    row, = project_catalogue([manifest()], [evidence], now=NOW)
    assert all(getattr(row, field).value is True for field in FIELDS)


@pytest.mark.parametrize('age,stale', [(59, False), (60, False), (61, True)])
def test_explicit_freshness_boundary_preserves_value(age, stale):
    evidence = ModuleStatusEvidence(module_id='worker', manifest_version=1, healthy=observation(age=age))
    row, = project_catalogue([manifest()], [evidence], now=NOW, max_age=timedelta(seconds=60))
    assert row.healthy.stale is stale and row.healthy.value is True
    assert evidence.healthy.observed_at == NOW - timedelta(seconds=age)


def test_no_freshness_rule_does_not_assume_one():
    evidence = ModuleStatusEvidence(module_id='worker', manifest_version=1, healthy=observation(age=999))
    row, = project_catalogue([manifest()], [evidence], now=NOW)
    assert row.healthy.stale is None


def test_deterministic_sorted_immutable_output():
    a, b = manifest('a'), manifest('b')
    result = project_catalogue([b, a], [], now=NOW)
    assert isinstance(result, tuple)
    assert result == project_catalogue([a, b], [], now=NOW)
    assert [row.manifest.module_id for row in result] == ['a', 'b']
    with pytest.raises(ValidationError):
        result[0].healthy.value = True


def test_duplicate_manifest_identity_rejected():
    with pytest.raises(ValueError, match='duplicate manifest'):
        project_catalogue([manifest(), manifest()], [], now=NOW)


def test_duplicate_evidence_rejected():
    evidence = ModuleStatusEvidence(module_id='worker', manifest_version=1)
    with pytest.raises(ValueError, match='duplicate evidence'):
        project_catalogue([manifest()], [evidence, evidence], now=NOW)


def test_evidence_must_match_exact_manifest_version():
    evidence = ModuleStatusEvidence(module_id='worker', manifest_version=2)
    with pytest.raises(ValueError, match='unknown manifest'):
        project_catalogue([manifest()], [evidence], now=NOW)


@pytest.mark.parametrize('field', ['command', 'dispatch', 'execute', 'policy', 'enabled'])
def test_manifest_rejects_runtime_fields(field):
    with pytest.raises(ValidationError):
        ModuleManifest(module_id='worker', manifest_version=1, summary='Worker', **{field: 'run'})


@pytest.mark.parametrize('value', [1, 'true', None])
def test_observation_boolean_is_strict(value):
    with pytest.raises(ValidationError):
        observation(value)


def test_unknown_observation_can_retain_provenance():
    evidence = ModuleStatusEvidence(module_id='worker', manifest_version=1, probed=observation('unknown'))
    row, = project_catalogue([manifest()], [evidence], now=NOW)
    assert row.probed.value == 'unknown' and row.probed.source == 'test-receipt'


def test_rejects_naive_time_negative_freshness_and_future_evidence():
    with pytest.raises(ValueError):
        project_catalogue([], [], now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        project_catalogue([], [], now=NOW, max_age=timedelta(seconds=-1))
    evidence = ModuleStatusEvidence(module_id='worker', manifest_version=1, healthy=observation(age=-1))
    with pytest.raises(ValueError, match='future'):
        project_catalogue([manifest()], [evidence], now=NOW)


def test_projection_has_no_io_and_does_not_mutate_inputs(monkeypatch):
    manifests = [manifest()]
    evidence = [ModuleStatusEvidence(module_id='worker', manifest_version=1, healthy=observation())]
    before = (manifests[0].model_dump_json(), evidence[0].model_dump_json())

    def forbidden(*args, **kwargs):
        pytest.fail('projection attempted I/O or process execution')

    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    row, = project_catalogue(manifests, evidence, now=NOW)
    assert row.healthy.value is True
    assert before == (manifests[0].model_dump_json(), evidence[0].model_dump_json())


def test_projection_revalidates_constructed_instances():
    invalid = ModuleManifest.model_construct(module_id='', manifest_version=1, summary='Worker')
    with pytest.raises(ValidationError):
        project_catalogue([invalid], [], now=NOW)


def test_each_status_has_its_own_age_and_source():
    evidence = ModuleStatusEvidence(
        module_id='worker', manifest_version=1,
        healthy=observation(age=61),
        enabled=StatusObservation(value=False, observed_at=NOW, source='operator-receipt'),
    )
    row, = project_catalogue([manifest()], [evidence], now=NOW, max_age=timedelta(seconds=60))
    assert row.healthy.stale is True and row.enabled.stale is False
    assert row.healthy.source == 'test-receipt' and row.enabled.source == 'operator-receipt'


def test_versions_do_not_share_evidence():
    evidence = ModuleStatusEvidence(module_id='worker', manifest_version=1, healthy=observation())
    old, new = project_catalogue([manifest(version=2), manifest()], [evidence], now=NOW)
    assert old.healthy.value is True and new.healthy.value == 'unknown'


def test_empty_catalogue():
    assert project_catalogue([], [], now=NOW) == ()


@pytest.mark.parametrize('version', [True, 0, '1'])
def test_version_requires_positive_integer(version):
    with pytest.raises(ValidationError):
        manifest(version=version)


def test_nested_evidence_is_revalidated():
    invalid = StatusObservation.model_construct(value='yes', observed_at=NOW, source='test')
    evidence = ModuleStatusEvidence.model_construct(module_id='worker', manifest_version=1, healthy=invalid)
    with pytest.raises(ValidationError):
        project_catalogue([manifest()], [evidence], now=NOW)


@pytest.mark.parametrize('fields', [
    {'value': True}, {'value': False}, {'source': 'receipt'},
    {'observed_at': NOW}, {'stale': False},
    {'value': True, 'source': 'receipt'},
    {'value': False, 'observed_at': NOW},
])
def test_public_status_rejects_incomplete_provenance(fields):
    with pytest.raises(ValidationError):
        CatalogueStatus(**fields)


def test_public_unknown_status_can_be_observed_or_absent():
    assert CatalogueStatus().value == 'unknown'
    observed = CatalogueStatus(value='unknown', source='receipt', observed_at=NOW, stale=True)
    assert observed.source == 'receipt'
