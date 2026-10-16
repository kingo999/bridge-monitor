import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claude_global_active: int = Field(default=2, ge=1)
    claude_per_root_active: int = Field(default=2, ge=1)
    claude_waiting: int = Field(default=8, ge=0)
    qwen_active: int = Field(default=1, ge=1)
    qwen_waiting: int = Field(default=4, ge=0)
    local_outstanding_per_claude: int = Field(default=1, ge=1)
    claude_execution_seconds: int = Field(default=900, ge=1)
    qwen_execution_seconds: int = Field(default=180, ge=1)

    @model_validator(mode="after")
    def check_consistency(self):
        if self.claude_per_root_active > self.claude_global_active:
            raise ValueError("per-root paid limit exceeds global limit")
        return self


class Deployment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queue_deadline_seconds: int = Field(gt=0)
    context_wait_seconds: int = Field(gt=0)
    fallback_allowance_tokens: int = Field(gt=0)
    client_timeout_seconds: int = Field(gt=0)
    delivery_margin_seconds: int = Field(gt=0)
    payload_limit_bytes: int = Field(gt=0)
    spool_limit_bytes: int = Field(gt=0)
    retention_days: int = Field(gt=0)
    postgres_dsn_ref: str
    qwen_endpoint: str

    @model_validator(mode="after")
    def check_refs(self):
        if not self.postgres_dsn_ref.startswith("env:"):
            raise ValueError("database secret must be an environment reference")
        if not self.qwen_endpoint.startswith(("http://", "https://")):
            raise ValueError("explicit Qwen endpoint required")
        if self.client_timeout_seconds <= self.delivery_margin_seconds:
            raise ValueError("delivery margin must fit client timeout")
        return self


class Fallback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paid_model_profile: str


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = Field(min_length=1)
    approval_reference: str = Field(min_length=1)


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limits: Limits = Field(default_factory=Limits)
    deployment: Deployment
    fallback: Fallback
    review: Review


class PolicySnapshot(BaseModel):
    version: str
    source_hash: str
    limits: Limits
    deployment: Deployment
    fallback: Fallback
    review: Review


class PolicyValidation(BaseModel):
    snapshot: PolicySnapshot | None
    errors: list[str] = Field(default_factory=list)


def validate_policy(proposed: dict[str, Any], capabilities: dict[str, dict]) -> PolicyValidation:
    try:
        parsed = PolicyInput.model_validate(proposed)
        profile = capabilities.get(parsed.fallback.paid_model_profile)
        if not profile or not isinstance(profile.get("context_tokens"), int) or profile["context_tokens"] <= 0:
            raise ValueError("fallback profile has no validated context capability")
        canonical = json.dumps(parsed.model_dump(exclude={"review"}), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return PolicyValidation(snapshot=PolicySnapshot(version=digest[:16], source_hash=digest,
                                                       **parsed.model_dump()))
    except (ValidationError, ValueError) as exc:
        return PolicyValidation(snapshot=None, errors=[str(exc)])
