from typing import Any, Literal

from pydantic import BaseModel, Field


class GuidanceSelection(BaseModel):
    pack_version_id: str | None = None
    state: Literal["selected", "no_guidance", "unknown"]


class GuidanceManifest(BaseModel):
    version: int = 1
    state: Literal["supplied", "no_guidance", "unknown"]
    supplied_content_ids: list[str] = Field(default_factory=list)
    pack_version_id: str | None = None
    pack_sha256: str | None = None
    selection_source: Literal["configuration", "claude"] | None = None
    supplied: list[dict[str, Any]] = Field(default_factory=list)
    excluded: list[dict[str, Any]] = Field(default_factory=list)


class GateDecision(BaseModel):
    gate_id: str
    version: int = 1
    outcome: Literal["pass", "fail", "not_applicable"]
    facts: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class ExampleReview(BaseModel):
    example_id: str
    reviewer_id: str
    status: Literal["candidate", "approved", "rejected"]
    permitted_use: Literal["prompt_example", "training_candidate", "evaluation_only"]
