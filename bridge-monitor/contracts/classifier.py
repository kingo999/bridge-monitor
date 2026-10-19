"""Advisory classifier shadow contracts.

A recommendation is an observation about where a request *could* go. Nothing here
grants capacity, permission or execution authority.
"""

import hashlib
import json
from typing import Any, Literal

from pydantic import (BaseModel, ConfigDict, Field, StrictBool, StrictInt,
                      field_validator)

from bridge.classifier.rubric import CATEGORIES, MAX_REASON_CHARS, ROUTES

CONTRACT_VERSION = "classifier-shadow-v1"
FACTS_VERSION = "trusted-facts-v1"
POLICY_VERSION = "recommendation-policy-v1"
ROUTE_MAP_VERSION = "actual-route-map-v1"

Category = Literal["math", "data", "text", "code", "architecture", "operations",
                   "research", "ambiguous"]
Route = Literal["tools", "general_local", "code_local", "paid", "clarify"]
OriginKind = Literal["natural", "controlled", "synthetic", "replay"]
ObservationState = Literal["captured", "dispatching", "completed", "failed", "skipped",
                           "uncertain", "cancelled"]
assert set(CATEGORIES) == set(Category.__args__) and set(ROUTES) == set(Route.__args__)


class GenerationProfile(BaseModel):
    """Measured benchmark settings. Any change needs a new ``profile_id``."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: str = "gemma-router-profile-v1"
    num_ctx: int = Field(default=8192, ge=512)
    num_predict: int = Field(default=192, ge=16)
    temperature: float = 0.0
    think: bool = False
    top_k: int = 40
    top_p: float = 0.9
    repeat_penalty: float = 1.0
    presence_penalty: float = 0.0
    seed: int = 42

    def options(self, seed: int | None = None) -> dict[str, Any]:
        return {"num_ctx": self.num_ctx, "num_predict": self.num_predict,
                "temperature": self.temperature, "seed": self.seed if seed is None else seed,
                "top_k": self.top_k, "top_p": self.top_p,
                "repeat_penalty": self.repeat_penalty,
                "presence_penalty": self.presence_penalty}


class TrustedFacts(BaseModel):
    """Host-held facts. Never derived from request text or model output.

    ``None`` means unknown. Availability and permission fail closed when unknown;
    the remaining facts block a recommendation only when explicitly adverse.
    """

    model_config = ConfigDict(extra="forbid")
    general_available: StrictBool | None = None
    code_available: StrictBool | None = None
    paid_allowed: StrictBool | None = None
    tools_available: StrictBool | None = None
    context_complete: StrictBool | None = None
    tests_present: StrictBool | None = None
    security_sensitive: StrictBool | None = None
    external_tools_required: StrictBool | None = None
    scope: Literal["single_file", "multi_file", "repository", "cross_service"] | None = None
    verification: str | None = Field(default=None, max_length=1000)
    context: str | None = Field(default=None, max_length=1000)


class ClassifierInput(BaseModel):
    """Untrusted request/source kept apart from trusted facts."""

    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1)
    source: str = ""
    facts: dict[str, Any]

    @field_validator("facts")
    @classmethod
    def facts_are_valid(cls, value: dict[str, Any]) -> dict[str, Any]:
        TrustedFacts.model_validate(value)
        return value

    def trusted(self) -> TrustedFacts:
        return TrustedFacts.model_validate(self.facts)

    def user_message(self) -> str:
        """Byte-identical to the benchmark's per-case user payload."""
        return json.dumps({"request": self.request, "source": self.source,
                           "facts": self.facts}, ensure_ascii=False)

    def request_sha256(self) -> str:
        return hashlib.sha256(self.request.encode("utf-8")).hexdigest()

    def source_sha256(self) -> str:
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()

    def input_bytes(self) -> int:
        return len(self.user_message().encode("utf-8"))


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    route: Route
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)


class PolicyAssessment(BaseModel):
    """Whether a raw recommendation would be allowed. It never rewrites the label."""

    model_config = ConfigDict(extra="forbid")
    policy_version: str = POLICY_VERSION
    allowed: bool | None
    blocks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    authorizes_execution: Literal[False] = False


class ReferenceLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category | None = None
    preferred_route: Route
    acceptable_routes: list[Route]
    critical: bool = False


class ObservationCapture(BaseModel):
    """Everything fixed at capture time, before any answer exists."""

    model_config = ConfigDict(extra="forbid")
    origin_kind: OriginKind
    origin_ref: str = Field(min_length=1)
    replay_id: str | None = None
    task_id: str | None = None
    work_item_id: str | None = None
    input: ClassifierInput
    facts_provenance: dict[str, Any] = Field(default_factory=dict)
    max_input_bytes: StrictInt | None = None
