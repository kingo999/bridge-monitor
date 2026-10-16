from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DispositionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disposition_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    stage: Literal["claude", "gpt", "human"]
    judgment: Literal["accepted", "revised", "replaced", "unused"]
    reason: str | None = None
    evidence_content_id: str | None = None
    supersedes_id: str | None = None
    part_id: str | None = None
    input_sha256: str | None = None
    output_result_id: str | None = None
    reason_code: str | None = None
    substantive_change: str | None = None
    repair_effort: Literal["none", "minor", "substantial", "complete_replacement"] | None = None
    failure_category: Literal["incorrect_behavior", "repository_convention_violated",
                              "invented_api", "scope_violation", "invalid_output_format",
                              "insufficient_context", "infrastructure_failure"] | None = None
    guidance_ids: list[str] = Field(default_factory=list)
    correction_execution_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_effort(self):
        if self.judgment == "revised" and self.repair_effort == "complete_replacement":
            raise ValueError("complete replacement cannot be a revision")
        if self.judgment == "replaced" and self.repair_effort in ("none", "minor"):
            raise ValueError("paid replacement needs substantial or complete replacement effort")
        if self.judgment == "accepted" and self.repair_effort not in (None, "none"):
            raise ValueError("accepted substance cannot have repair effort")
        return self


class ResultReceipt(BaseModel):
    result_id: str
    content_id: str | None
    raw_content_id: str | None = None
    delivered: bool
    sha256: str | None = None


class ContributionLink(BaseModel):
    from_result_id: str
    to_result_id: str
    relation: Literal["accepted_contribution", "revised_contribution", "replacement",
                      "synthesis", "correction"]


class CompletionEnvelope(BaseModel):
    call_id: str
    attempt_id: str
    result: ResultReceipt
    dispositions: list[DispositionRecord] = Field(default_factory=list)
    contributions: list[ContributionLink] = Field(default_factory=list)
    fallback_child_call_id: str | None = None
    work_resolution: str | None = None
    accounting_complete: bool = False
