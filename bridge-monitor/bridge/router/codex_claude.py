"""Deterministic admission policy inside the existing router; no inference."""
import hashlib
import json
from contracts.codex_claude import (CodexClaudePolicy, CodexClaudeTaskFacts,
                                    HostRoutingFacts, CodexClaudeDecision)

def evaluate_codex_claude(policy: CodexClaudePolicy, facts: CodexClaudeTaskFacts,
                          host: HostRoutingFacts, override: str = "auto") -> CodexClaudeDecision:
    snapshot = policy.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    candidates = []
    for name, profile in sorted(policy.profiles.items()):
        reason = ("profile_disabled" if not profile.enabled else
                  "execution_profile_unavailable" if profile.model_id not in host.profile_models else None)
        candidates.append(dict(profile=name, model_id=profile.model_id, eligible=reason is None,
                               rejection_reason=reason))
    def result(action, reason, selected=None):
        return CodexClaudeDecision(action=action, reason=reason, selected_profile=selected,
            requested_override=override, policy_digest=digest, policy_snapshot=snapshot,
            facts=facts.model_dump(), host_facts=host.model_dump(), candidates=candidates)
    if not policy.enabled:
        return result("blocked", "workflow_disabled")
    if not host.authorized:
        return result("blocked", "scope_not_authorized")
    if not host.context_available:
        return result("blocked", "context_unavailable")
    if not host.durable_logging_available:
        return result("blocked", "durable_logging_unavailable")
    if override not in {"auto", "codex", "haiku", "sonnet", "opus"}:
        return result("blocked", "invalid_override")
    # Overrides cannot create missing tools, context or authority.
    if host.needs_repository or host.needs_tools or not host.supplied_context_only:
        return result("keep_owner", "owner_tools_required")
    if override == "codex":
        return result("keep_owner", "caller_keep_owner")
    preferred = (override if override != "auto" else
        "opus" if facts.complexity == "complex" else
        "haiku" if facts.complexity == "low" and facts.risk == "low" else "sonnet")
    candidate = next(item for item in candidates if item["profile"] == preferred)
    if not candidate["eligible"]:
        return result("blocked", candidate["rejection_reason"])
    return result("claude", "caller_override" if override != "auto" else "complexity_rule", preferred)

def prepare_codex_claude_route(policy, task, prompt, adapter):
    """Caller context reports complexity, but never establishes host capabilities."""
    facts = CodexClaudeTaskFacts(category=task.category,
        complexity=task.context.get("complexity", "unknown"),
        risk=task.context.get("risk", "unknown"),
        project_root=task.context.get("project_root"),
        rationale=task.context.get("routing_rationale", "")[:1000])
    host = HostRoutingFacts(authorized=bool(task.scope) and set(task.scope) <= {"analysis"},
        context_available=bool(prompt.strip()), supplied_context_only=not bool(task.context.get("source_id")),
        needs_repository=bool(task.context.get("source_id")),
        profile_models=list(getattr(adapter, "profile_models", ())))
    return evaluate_codex_claude(policy, facts, host, task.context.get("route_override", "auto"))

