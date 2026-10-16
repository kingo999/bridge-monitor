from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class ResultReceipt(BaseModel):
    result_id: str
    content_id: str | None
    delivered: bool


class CompletionEnvelope(BaseModel):
    call_id: str
    attempt_id: str
    result: ResultReceipt
    dispositions: list[DispositionRecord] = Field(default_factory=list)
    accounting_complete: bool = False
