"""Caller-reported execution metadata; token counts require a separate CLI receipt."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DirectExecutionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    record_id: str = Field(min_length=1, max_length=200)
    execution_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    project_root: str = Field(min_length=1, max_length=1024)
    summary: str = Field(min_length=1, max_length=1000)
    status: Literal["running", "completed", "failed", "cancelled", "unknown"]
    bypass_reason: Literal["user_instruction", "approved_fallback", "bridge_unavailable",
                         "bridge_unsuitable", "management", "trivial_operation", "other"]
    reason: str = Field(default="", max_length=2000)
    requested_model: str | None = Field(default=None, min_length=1, max_length=200)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("observed_at requires a timezone")
        return value.astimezone(timezone.utc)


class CodexTurnReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=200)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["turn"] = "turn"
    provenance: Literal["codex_cli_receipt"] = "codex_cli_receipt"
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_output_tokens: int | None = Field(default=None, ge=0)


class DirectRecordedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report: DirectExecutionReport
    usage: CodexTurnReceipt | None = None
    source_turn_id: str | None = Field(default=None, min_length=1, max_length=200)
