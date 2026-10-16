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


class MetricValue(BaseModel):
    numerator: int
    denominator: int
    percent: float | None


class MetricSet(BaseModel):
    definition_version: str
    cohort: CohortQuery
    as_of: datetime
    unit: str = "work_items/results"
    metrics: dict[str, MetricValue]
    coverage: dict[str, MetricValue] = Field(default_factory=dict)


class PublicationCursor(BaseModel):
    position: int = Field(ge=0)


class DashboardSnapshot(BaseModel):
    cursor: PublicationCursor
    as_of: datetime
    status: DashboardStatus
    work_items: list[dict] = Field(default_factory=list)
