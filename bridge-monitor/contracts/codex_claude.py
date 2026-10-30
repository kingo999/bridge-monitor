"""Inactive-by-default, versioned Codex/Claude policy and reproducible facts."""
from decimal import Decimal
from typing import Annotated, Literal
from datetime import datetime, timedelta
from pydantic import field_validator
from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class ClaudeProfile(StrictModel):
    model_id: str = Field(pattern=r"^claude-(haiku|sonnet|opus)-[0-9]+(?:-[0-9]+)*$")
    enabled: bool = False
    effort: Literal["low", "medium", "high"] = "medium"
    reference_input_tokens: int = Field(default=40000, gt=0)
    reference_output_tokens: int = Field(default=8000, gt=0)
    reference_usd: Money = Decimal("0.30")
    deadline_seconds: int = Field(default=180, gt=20)
    provider_seconds: int = Field(default=160, gt=0)
    queue_seconds: int = Field(default=10, ge=0)
    delivery_seconds: int = Field(default=10, ge=0)
    input_per_million: Money = Decimal("2")
    output_per_million: Money = Decimal("10")
    cache_write_per_million: Money = Decimal("4")
    rate_card_version: str = "proposal-2026-10-25"

    @model_validator(mode="after")
    def consistent_bounds(self):
        if self.provider_seconds + self.queue_seconds + self.delivery_seconds > self.deadline_seconds:
            raise ValueError("queue/provider/delivery allowances exceed deadline")
        return self

def starter_profiles():
    return {
        "haiku": ClaudeProfile(model_id="claude-haiku-5-5", effort="low",
            reference_input_tokens=20000, reference_output_tokens=2000, reference_usd="0.02",
            deadline_seconds=60, provider_seconds=40, input_per_million="0.10",
            output_per_million="0.50", cache_write_per_million="0.20"),
        "sonnet": ClaudeProfile(model_id="claude-sonnet-5-5"),
        "opus": ClaudeProfile(model_id="claude-opus-5-5", reference_input_tokens=60000,
            reference_output_tokens=12000, reference_usd="1.00", deadline_seconds=480,
            provider_seconds=460, input_per_million="4", output_per_million="20",
            cache_write_per_million="8"),
    }

class CodexClaudePolicy(StrictModel):
    enabled: bool = False
    spending_mode: Literal["statistics_only"] = "statistics_only"
    profiles: dict[str, ClaudeProfile] = Field(default_factory=starter_profiles)
    reference_workflow_usd: Money = Decimal("1.25")
    reference_rolling_7d_usd: Money = Decimal("15")
    opus_reference_rolling_24h_usd: Money = Decimal("1")
    max_active_workers: int = Field(default=1, ge=1)
    daily_notice_usd: Money = Decimal("10")
    subscription_notice_percent: int = Field(default=80, ge=1, le=100)
    timezone: Literal["Europe/Prague"] = "Europe/Prague"

    @model_validator(mode="after")
    def profile_names(self):
        if set(self.profiles) != {"haiku", "sonnet", "opus"}:
            raise ValueError("exactly the three approved family slots are required")
        for name, profile in self.profiles.items():
            if not profile.model_id.startswith(f"claude-{name}-"):
                raise ValueError("profile family and pinned model differ")
        return self

class CodexClaudeTaskFacts(StrictModel):
    category: str = Field(min_length=1)
    complexity: Literal["low", "ordinary", "complex", "unknown"] = "unknown"
    risk: Literal["low", "ordinary", "high", "unknown"] = "unknown"
    complexity_provenance: Literal["caller_reported"] = "caller_reported"
    project_root: str | None = Field(default=None, max_length=1024)
    rationale: str = Field(default="", max_length=1000)

class HostRoutingFacts(StrictModel):
    authorized: bool = False
    context_available: bool = False
    supplied_context_only: bool = False
    needs_repository: bool = False
    needs_tools: bool = False
    repository_execution_available: bool = False
    repository_binding: str | None = None
    durable_logging_available: bool = True
    profile_models: list[str] = Field(default_factory=list)

class SubscriptionObservation(StrictModel):
    observation_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=100)
    window_id: str = Field(min_length=1, max_length=200)
    consumed_percent: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    window_start: datetime
    resets_at: datetime
    observed_at: datetime
    valid_until: datetime
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("window_start", "resets_at", "observed_at", "valid_until")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware timestamp required")
        return value

    @model_validator(mode="after")
    def valid_window(self):
        if not self.window_start <= self.observed_at < self.valid_until <= self.resets_at:
            raise ValueError("invalid subscription observation window")
        if self.valid_until - self.observed_at > timedelta(minutes=15):
            raise ValueError("subscription freshness may not exceed 15 minutes")
        return self

class CodexClaudeDecision(StrictModel):
    action: Literal["claude", "keep_owner", "blocked"]
    reason: str
    selected_profile: str | None = None
    requested_override: str
    evaluator_version: str = "codex-claude-v1"
    policy_digest: str
    policy_snapshot: dict
    facts: dict
    host_facts: dict
    candidates: list[dict]
