import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from contracts.router import RouterPolicy


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
    queue_poll_ms: int = Field(default=200, ge=10, le=5000)
    context_wait_seconds: int = Field(gt=0)
    fallback_allowance_tokens: int = Field(gt=0)
    fallback_allowance_seconds: int = Field(default=60, gt=0)
    client_timeout_seconds: int = Field(gt=0)
    delivery_margin_seconds: int = Field(gt=0)
    payload_limit_bytes: int = Field(gt=0)
    spool_limit_bytes: int = Field(gt=0)
    spool_overflow_policy: Literal["reject_new_work", "allow_with_gaps"] = "reject_new_work"
    degraded_admission: Literal["reject_new_work", "allow_with_gaps"] = "reject_new_work"
    retention_days: int = Field(gt=0)
    postgres_dsn_ref: str
    qwen_endpoint: str
    qwen_model_profile: str = ""
    qwen_model_digest: str = ""
    qwen_output_tokens: int = Field(default=2048, gt=0)
    qwen_keep_alive: str = "10m"
    qwen_temperature: float = Field(default=0.0, ge=0, le=2)
    qwen_agent_endpoint: str = ""
    qwen_agent_token_ref: str = ""
    qwen_ack_seconds: float = Field(default=5, gt=0)
    qwen_poll_seconds: float = Field(default=2, gt=0)
    qwen_poll_timeout_seconds: float = Field(default=3, gt=0)
    qwen_poll_failures: int = Field(default=2, ge=1)
    qwen_first_output_seconds: float = Field(default=45, gt=0)
    qwen_idle_output_seconds: float = Field(default=20, gt=0)

    @model_validator(mode="after")
    def check_refs(self):
        if not self.postgres_dsn_ref.startswith("env:"):
            raise ValueError("database secret must be an environment reference")
        if not self.qwen_endpoint.startswith(("http://", "https://")):
            raise ValueError("explicit Qwen endpoint required")
        if self.qwen_model_profile and not re.fullmatch(r"[0-9a-f]{64}", self.qwen_model_digest):
            raise ValueError("local model profile requires a pinned SHA-256 digest")
        if self.qwen_agent_endpoint:
            if not self.qwen_agent_endpoint.startswith(("http://", "https://")):
                raise ValueError("agent endpoint must be HTTP(S)")
            if not self.qwen_agent_token_ref.startswith("env:") or len(self.qwen_agent_token_ref) <= 4:
                raise ValueError("agent token must be an environment reference")
            if not self.qwen_model_profile:
                raise ValueError("agent route requires pinned model")
        elif self.qwen_agent_token_ref:
            raise ValueError("agent token reference requires agent endpoint")
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


class ApprovedGuidancePack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pack_version_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    enabled: bool = True
    categories: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)


class GuidancePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packs: list[ApprovedGuidancePack] = Field(default_factory=list)
    required_categories: list[str] = Field(default_factory=list)
    max_bytes: int = Field(default=0, ge=0)


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limits: Limits = Field(default_factory=Limits)
    deployment: Deployment
    fallback: Fallback
    review: Review
    guidance: GuidancePolicy = Field(default_factory=GuidancePolicy)
    router: RouterPolicy = Field(default_factory=RouterPolicy)

    @model_validator(mode="after")
    def check_agent_deadlines(self):
        if self.deployment.qwen_agent_endpoint:
            if max(self.deployment.qwen_first_output_seconds,
                   self.deployment.qwen_idle_output_seconds) >= self.limits.qwen_execution_seconds:
                raise ValueError("agent progress deadlines must fit Qwen execution limit")
        if self.router.mode == "shadow":
            if not (self.deployment.qwen_agent_endpoint and
                    self.deployment.qwen_model_profile and self.deployment.qwen_model_digest):
                raise ValueError("shadow router requires pinned Skynet agent model")
            if self.router.execution_seconds > self.limits.qwen_execution_seconds:
                raise ValueError("router execution limit exceeds Qwen execution limit")
        return self


class PolicySnapshot(BaseModel):
    version: str
    source_hash: str
    limits: Limits
    deployment: Deployment
    fallback: Fallback
    review: Review
    guidance: GuidancePolicy = Field(default_factory=GuidancePolicy)
    router: RouterPolicy = Field(default_factory=RouterPolicy)


class PolicyValidation(BaseModel):
    snapshot: PolicySnapshot | None
    errors: list[str] = Field(default_factory=list)


def validate_policy(proposed: dict[str, Any], capabilities: dict[str, dict]) -> PolicyValidation:
    try:
        parsed = PolicyInput.model_validate(proposed)
        profile = capabilities.get(parsed.fallback.paid_model_profile)
        if not profile or not isinstance(profile.get("context_tokens"), int) or profile["context_tokens"] <= 0:
            raise ValueError("fallback profile has no validated context capability")
        # Preserve historical policy hashes when an older proposal has no router section.
        excluded = {"review", "router"} if "router" not in proposed else {"review"}
        canonical = json.dumps(parsed.model_dump(exclude=excluded), sort_keys=True,
                               separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return PolicyValidation(snapshot=PolicySnapshot(version=digest[:16], source_hash=digest,
                                                       **parsed.model_dump()))
    except (ValidationError, ValueError) as exc:
        return PolicyValidation(snapshot=None, errors=[str(exc)])
