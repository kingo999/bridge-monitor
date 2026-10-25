"""BM-05 descriptive catalogue facts; none of these fields confers authority."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from contracts.operations import Identifier, UtcTimestamp


class _Fact(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')


class ModuleManifest(_Fact):
    module_id: Identifier
    manifest_version: Annotated[int, Field(strict=True, gt=0)]
    summary: Identifier


class StatusObservation(_Fact):
    value: StrictBool | Literal['unknown']
    observed_at: UtcTimestamp
    source: Identifier


class ModuleStatusEvidence(_Fact):
    module_id: Identifier
    manifest_version: Annotated[int, Field(strict=True, gt=0)]
    discovered: StatusObservation | None = None
    installed: StatusObservation | None = None
    probed: StatusObservation | None = None
    enabled: StatusObservation | None = None
    healthy: StatusObservation | None = None
    eligible: StatusObservation | None = None


class CatalogueStatus(_Fact):
    value: StrictBool | Literal['unknown'] = 'unknown'
    observed_at: UtcTimestamp | None = None
    source: Identifier | None = None
    stale: StrictBool | None = None

    @model_validator(mode='after')
    def _provenance(self):
        if (self.source is None) != (self.observed_at is None):
            raise ValueError('source and observed_at must be paired')
        if self.source is None and (self.value != 'unknown' or self.stale is not None):
            raise ValueError('reported status requires source and observed_at')
        return self


class CatalogueEntry(_Fact):
    manifest: ModuleManifest
    discovered: CatalogueStatus
    installed: CatalogueStatus
    probed: CatalogueStatus
    enabled: CatalogueStatus
    healthy: CatalogueStatus
    eligible: CatalogueStatus
