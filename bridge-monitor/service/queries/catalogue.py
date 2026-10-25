"""Opt-in, in-memory BM-05 projection. No runtime consumers are registered."""
from datetime import datetime, timedelta
from typing import Iterable

from pydantic import TypeAdapter

from contracts.catalogue import (
    CatalogueEntry, CatalogueStatus, ModuleManifest, ModuleStatusEvidence,
)
from contracts.operations import UtcTimestamp


_STATUSES = ('discovered', 'installed', 'probed', 'enabled', 'healthy', 'eligible')
_MANIFESTS = TypeAdapter(tuple[ModuleManifest, ...])
_EVIDENCE = TypeAdapter(tuple[ModuleStatusEvidence, ...])
_TIME = TypeAdapter(UtcTimestamp)


def project_catalogue(
    manifests: Iterable[ModuleManifest],
    evidence: Iterable[ModuleStatusEvidence],
    *,
    now: datetime,
    max_age: timedelta | None = None,
) -> tuple[CatalogueEntry, ...]:
    """Join exact versioned identities, retaining independent reported facts.

    Missing observations are unknown. Without max_age, freshness is unknown;
    with it, age strictly greater than max_age is stale. Future observations
    are rejected because this view cannot establish their temporal validity.
    Eligibility is an external observation, never a decision made here.
    """
    now = _TIME.validate_python(now)
    if max_age is not None and (
        not isinstance(max_age, timedelta) or max_age < timedelta(0)
    ):
        raise ValueError('max_age must be a nonnegative timedelta')
    manifests = _MANIFESTS.validate_python(tuple(manifests))
    evidence = _EVIDENCE.validate_python(tuple(evidence))
    indexed = {}
    for manifest in manifests:
        key = (manifest.module_id, manifest.manifest_version)
        if key in indexed:
            raise ValueError('duplicate manifest identity')
        indexed[key] = manifest
    observations = {}
    for item in evidence:
        key = (item.module_id, item.manifest_version)
        if key not in indexed:
            raise ValueError('evidence references unknown manifest identity')
        if key in observations:
            raise ValueError('duplicate evidence identity')
        observations[key] = item

    rows = []
    for key, manifest in sorted(indexed.items()):
        item = observations.get(key)
        statuses = {}
        for field in _STATUSES:
            observation = getattr(item, field) if item is not None else None
            if observation is None:
                statuses[field] = CatalogueStatus()
                continue
            age = now - observation.observed_at
            if age < timedelta(0):
                raise ValueError('future observation cannot be projected')
            statuses[field] = CatalogueStatus(
                value=observation.value,
                observed_at=observation.observed_at,
                source=observation.source,
                stale=None if max_age is None else age > max_age,
            )
        rows.append(CatalogueEntry(manifest=manifest, **statuses))
    return tuple(rows)
