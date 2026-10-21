from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from contracts.events import DashboardStatus


class CohortQuery(BaseModel):
    created_from: datetime | None = None
    created_until: datetime | None = None
    as_of: datetime | None = None
    origin: Literal["observed", "imported", "synthetic", "all"] = "observed"
    consumer_stage: str = "claude"
    completed_only: bool = False
    guidance_group: str | None = None
    failure_category: str | None = None
    repair_effort: str | None = None
    example_reuse_status: str | None = None


class MetricValue(BaseModel):
    numerator: int
    denominator: int
    percent: float | None


class DistributionSlice(BaseModel):
    key: str
    label: str
    count: int
    percent: float | None


class Distribution(BaseModel):
    title: str
    basis: str
    total: int
    slices: list[DistributionSlice]


class MetricSet(BaseModel):
    definition_version: str
    cohort: CohortQuery
    as_of: datetime
    unit: str = "work_items/results"
    metrics: dict[str, MetricValue]
    coverage: dict[str, MetricValue] = Field(default_factory=dict)
    guidance_groups: dict[str, int] = Field(default_factory=dict)
    distributions: dict[str, Distribution] = Field(default_factory=dict)


class PublicationCursor(BaseModel):
    position: int = Field(ge=0)


class DashboardSnapshot(BaseModel):
    cursor: PublicationCursor
    as_of: datetime
    status: DashboardStatus
    work_items: list[dict] = Field(default_factory=list)
    worker_executions: list[dict] = Field(default_factory=list)
    capacity: dict[str, dict[str, int]] | None = None
