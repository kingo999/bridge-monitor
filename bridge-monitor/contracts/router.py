"""Reviewed shadow-router policy, pre-solution case, and decision facts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bridge.code_fix_verifier import VerifierSettings


class RouterPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["disabled", "shadow"] = "disabled"
    categories: list[str] = Field(default_factory=lambda: ["single_file_python_fix"])
    scopes: list[str] = Field(default_factory=lambda: ["analysis"])
    output_contract: Literal["complete_python_module_v1"] = "complete_python_module_v1"
    prompt_version: str = Field(default="complete-module-v1", min_length=1)
    prompt_prefix: str = Field(default="Fix the stated bug in this single Python module. "
        "Return exactly one JSON object with one key, answer, whose string value is the complete "
        "replacement Python source. Do not return a patch, explanation, Markdown, or test output.\n\n",
        min_length=1)
    sample_percent: float = Field(default=100, ge=0, le=100)
    queue_deadline_seconds: int = Field(default=30, ge=1)
    execution_seconds: int = Field(default=120, ge=1)
    shadow_reserve_tokens: int = Field(default=2048, ge=1)
    max_context_tokens: int = Field(default=8192, ge=128)
    max_prompt_bytes: int = Field(default=16384, ge=1)
    max_source_bytes: int = Field(default=16384, ge=1)
    worker_poll_ms: int = Field(default=1000, ge=100, le=10000)
    retention_days: int = Field(default=30, ge=1)
    withdrawn_refs: list[str] = Field(default_factory=list)
    verifier: VerifierSettings | None = None

    @model_validator(mode="after")
    def require_verifier_in_shadow(self):
        if self.mode == "shadow" and self.verifier is None:
            raise ValueError("shadow router requires pinned verifier")
        return self


class RouterCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin_kind: Literal["natural", "controlled"]
    origin_ref: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_text: str = Field(min_length=1)
    task_arrived_at: datetime
    captured_at: datetime
    solution_available_at: datetime | None = None
    baseline_source: str = Field(min_length=1)
    tests: list[dict] = Field(min_length=1)
    output_contract: Literal["complete_python_module_v1"]
    verifier_settings_sha256: str | None = None
    baseline_validation_reason: str | None = None
    baseline_sha256: str | None = None
    tests_sha256: str | None = None

    @field_validator("task_arrived_at", "captured_at", "solution_available_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("case timestamps need a timezone")
        return value

    @model_validator(mode="after")
    def pre_solution_capture(self):
        if self.captured_at < self.task_arrived_at:
            raise ValueError("case captured before task arrival")
        if self.solution_available_at and self.solution_available_at <= self.captured_at:
            raise ValueError("case captured after solution available")
        return self


class CaseReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verifier_ref: str
    baseline_sha256: str
    tests_sha256: str
    verifier_image: str
    captured_at: datetime


class CaptureFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: RouterCase | None = None
    case_digest_valid: bool | None = None
    baseline_failed: bool | None = None
    capture_healthy: bool | None = None
    agent_ready: bool | None = None
    model_profile: str | None = None
    model_digest: str | None = None
    remaining_calls: int | None = None
    remaining_tokens: int | None = None
    context_tokens: int | None = None
    queue_slots: int | None = None
    verifier_active: bool | None = None


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligible: bool
    reason: str
    manifest: dict
