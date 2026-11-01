"""Deterministic admission policy inside the existing router; no inference."""
import hashlib
import json
from contracts.codex_claude import (CodexClaudePolicy, CodexClaudeTaskFacts,
                                    HostRoutingFacts, CodexClaudeDecision,
                                    TASK_PRESETS, TASK_PRESET_VERSION)

def evaluate_codex_claude(policy: CodexClaudePolicy, facts: CodexClaudeTaskFacts,
                          host: HostRoutingFacts, override: str = "auto",
                          task_preset=None) -> CodexClaudeDecision:
    snapshot = policy.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    candidates = []
    for name, profile in sorted(policy.profiles.items()):
        reason = ("profile_disabled" if not profile.enabled else
                  "execution_profile_unavailable" if profile.model_id not in host.profile_models else None)
        candidates.append(dict(profile=name, model_id=profile.model_id, eligible=reason is None,
                               rejection_reason=reason))
    supplied = task_preset is not None
    known_preset = supplied and isinstance(task_preset, str) and task_preset in TASK_PRESETS
    def result(action, reason, selected=None, preset_status=None):
        record = None
        if supplied:
            # Requested vs applied stay separate so reports never mislabel overridden work.
            record = {"requested": task_preset if isinstance(task_preset, str) else repr(task_preset)[:200],
                      "applied": task_preset if preset_status == "applied" else None,
                      "version": TASK_PRESET_VERSION,
                      "status": preset_status or ("blocked" if action == "blocked" else "not_applied"),
                      "reason": reason,
                      "profile": TASK_PRESETS[task_preset]["profile"] if known_preset else None}
        return CodexClaudeDecision(action=action, reason=reason, selected_profile=selected,
            requested_override=override, policy_digest=digest, policy_snapshot=snapshot,
            facts=facts.model_dump(), host_facts=host.model_dump(), candidates=candidates,
            task_preset=record)
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
    if supplied and not known_preset:
        return result("blocked", "invalid_task_preset")
    # Overrides cannot create missing tools, context or authority.
    if (host.needs_repository or host.needs_tools or not host.supplied_context_only) and not host.repository_execution_available:
        return result("keep_owner", "owner_tools_required")
    if override == "codex":
        return result("keep_owner", "caller_keep_owner", preset_status="overridden" if supplied else None)
    legacy = ("opus" if facts.complexity == "complex" else
              "haiku" if facts.complexity == "low" and facts.risk == "low" else "sonnet")
    use_preset = known_preset and override == "auto"
    if use_preset:
        definition = TASK_PRESETS[task_preset]
        required = definition["requires"]
        if any(getattr(facts, key) != value for key, value in required.items()):
            return result("blocked", "task_preset_incompatible")
        strength = {"haiku": 0, "sonnet": 1, "opus": 2}
        if strength[definition["profile"]] < strength[legacy]:
            # No quiet downgrade below the legacy requirement.
            return result("blocked", "task_preset_incompatible")
        preferred = definition["profile"]
    else:
        preferred = override if override != "auto" else legacy
    candidate = next(item for item in candidates if item["profile"] == preferred)
    if not candidate["eligible"]:
        return result("blocked", candidate["rejection_reason"])
    if use_preset:
        return result("claude", "task_preset", preferred, preset_status="applied")
    return result("claude", "caller_override" if override != "auto" else "complexity_rule", preferred,
                  preset_status="overridden" if supplied else None)

def prepare_codex_claude_route(policy, task, prompt, adapter):
    """Caller context reports complexity, but never establishes host capabilities."""
    facts = CodexClaudeTaskFacts(category=task.category,
        complexity=task.context.get("complexity", "unknown"),
        risk=task.context.get("risk", "unknown"),
        project_root=task.context.get("project_root"),
        rationale=task.context.get("routing_rationale", "")[:1000])
    repository_policy = None
    if task.scope == ["repository-execution"]:
        try:
            repository_policy = adapter.repository_policy.for_task(task)
        except (AttributeError, PermissionError, ValueError, OSError):
            pass
    from bridge.repository_worker import binding
    host = HostRoutingFacts(authorized=bool(repository_policy) or (bool(task.scope) and set(task.scope) <= {"analysis"}),
        repository_execution_available=repository_policy is not None,
        repository_binding=binding(repository_policy) if repository_policy else None,
        context_available=bool(prompt.strip()), supplied_context_only=not bool(task.context.get("source_id")),
        needs_repository=bool(task.context.get("source_id")),
        profile_models=list(getattr(adapter, "profile_models", ())))
    return evaluate_codex_claude(policy, facts, host, task.context.get("route_override", "auto"),
                                 task_preset=task.context.get("task_preset"))
