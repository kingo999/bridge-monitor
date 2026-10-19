"""Deterministic checks of a raw recommendation against trusted facts.

The assessment says whether the *recommended* destination would be permitted. It never
replaces the raw label, so an unsafe recommendation stays a model failure. Nothing in this
module dispatches, reserves capacity, calls a model or grants a capability.
"""

from contracts.classifier import (POLICY_VERSION, PolicyAssessment, Recommendation,
                                  ROUTE_MAP_VERSION, TrustedFacts)

# Existing production decision routes -> classifier route vocabulary. The only local
# worker in this project is the Skynet Qwen coding worker.
ACTUAL_ROUTE_MAP = {"paid": "paid", "local": "code_local"}
BROAD_SCOPES = {"multi_file", "repository", "cross_service"}


def map_actual_route(project_route: str | None) -> str:
    """``unknown`` unless the project's own route maps explicitly (``pending`` stays unknown)."""
    return ACTUAL_ROUTE_MAP.get(project_route or "", "unknown")


def _require(blocks: list[str], value: bool | None, *, unknown: str, denied: str) -> None:
    if value is None:
        blocks.append(unknown)
    elif value is False:
        blocks.append(denied)


def assess_recommendation(rec: Recommendation | None, facts: TrustedFacts) -> PolicyAssessment:
    if rec is None:
        return PolicyAssessment(allowed=None, blocks=["no_valid_recommendation"])
    blocks: list[str] = []
    warnings: list[str] = []
    route = rec.route
    if route != "clarify":
        if facts.context_complete is False:
            blocks.append("context_incomplete")
        elif facts.context_complete is None:
            warnings.append("context_completeness_unknown")
    if route == "tools":
        if facts.tools_available is False:
            blocks.append("tools_unavailable")
        elif facts.tools_available is None:
            warnings.append("tools_availability_unknown")
    elif route == "general_local":
        _require(blocks, facts.general_available, unknown="general_worker_unknown",
                 denied="general_worker_unavailable")
        if facts.external_tools_required:
            blocks.append("external_tools_required")
    elif route == "code_local":
        _require(blocks, facts.code_available, unknown="code_worker_unknown",
                 denied="code_worker_unavailable")
        if facts.external_tools_required:
            blocks.append("external_tools_required")
        if facts.scope in BROAD_SCOPES:
            blocks.append("scope_exceeds_code_worker")
        if facts.security_sensitive:
            blocks.append("security_sensitive")
        if facts.tests_present is False:
            blocks.append("trusted_tests_absent")
        elif facts.tests_present is None:
            warnings.append("trusted_tests_unknown")
        if facts.scope is None:
            warnings.append("scope_unknown")
    elif route == "paid":
        _require(blocks, facts.paid_allowed, unknown="paid_permission_unknown",
                 denied="paid_not_permitted")
    return PolicyAssessment(allowed=not blocks, blocks=blocks, warnings=warnings,
                            policy_version=POLICY_VERSION)


__all__ = ["ACTUAL_ROUTE_MAP", "ROUTE_MAP_VERSION", "assess_recommendation", "map_actual_route"]
