"""Evidence-bounded, one-work-item-per-slice dashboard distributions."""

from collections import Counter, defaultdict
import re

from contracts.queries import Distribution, DistributionSlice


FAMILIES = ("sol", "luna", "astra", "opus", "sonnet", "qwen", "gemma", "haiku")
FAMILY_RE = re.compile(r"(?:^|[^a-z])(sol|luna|astra|opus|sonnet|qwen|gemma|haiku)(?:$|[^a-z])")
LABELS = {
    "node": {"codex": "Codex", "claude": "Claude", "skynet": "Skynet", "gemma": "Gemma",
             "mixed": "Mixed", "unknown": "Unknown", "in_progress": "In progress"},
    "model": {**{name: name.title() for name in FAMILIES}, "mixed": "Mixed models",
              "other": "Other model", "unknown": "Unknown", "in_progress": "In progress"},
    "handling": {"qwen_full": "Qwen fully", "qwen_partial": "Qwen partial",
                 "claude_only": "Claude only", "codex_only": "Codex only",
                 "other": "Other", "unknown": "Unknown", "in_progress": "In progress"},
    "tier": {"local": "Local", "paid": "Paid", "mixed": "Paid + local",
             "unknown": "Unknown", "in_progress": "In progress"},
}
ORDER = {
    "node": ("codex", "claude", "skynet", "gemma", "mixed", "unknown", "in_progress"),
    "model": (*FAMILIES, "other", "mixed", "unknown", "in_progress"),
    "handling": ("qwen_full", "qwen_partial", "claude_only", "codex_only", "other", "unknown", "in_progress"),
    "tier": ("paid", "local", "mixed", "unknown", "in_progress"),
}


def model_family(name: str | None) -> str | None:
    if not name:
        return None
    match = FAMILY_RE.search(name.lower())
    return match.group(1) if match else "other"


def classify(path: str, outcome: str | None, pools: set[str],
             models: dict[str, set[str]]) -> dict[str, str]:
    """Classify retained work; absent evidence is explicit, never inferred from a route."""
    if not outcome or outcome == "running":
        return {key: "in_progress" for key in ORDER}
    if outcome == "unknown":
        return {key: "unknown" for key in ORDER}
    if path == "direct_codex":
        if outcome != "completed":
            return {key: "unknown" for key in ORDER}
        families = models.get("codex", set())
        return {"node": "codex", "model": _one_family(families),
                "handling": "codex_only", "tier": "paid"}

    qwen = "qwen" in pools
    claude = "claude" in pools
    if outcome == "local_retained":
        if not qwen:
            return {key: "unknown" for key in ORDER}
        return {"node": "skynet", "model": _one_family(models.get("qwen", set())),
                "handling": "qwen_full", "tier": "local"}
    if outcome == "paid_revision_with_contribution":
        if not (qwen and claude):
            return {key: "unknown" for key in ORDER}
        return {"node": "mixed", "model": _one_family(models.get("qwen", set()) | models.get("claude", set())),
                "handling": "qwen_partial", "tier": "mixed"}
    if outcome in ("paid_replaced", "paid_direct"):
        if not claude:
            return {key: "unknown" for key in ORDER}
        return {"node": "claude", "model": _one_family(models.get("claude", set())),
                "handling": "claude_only", "tier": "paid"}
    return {key: "unknown" for key in ORDER}


def _one_family(families: set[str]) -> str:
    if not families:
        return "unknown"
    return next(iter(families)) if len(families) == 1 else "mixed"


def _distribution(key: str, counts: Counter, total: int) -> Distribution:
    titles = {"node": "Work by final executor", "model": "Work by assigned model",
              "handling": "Work by handling", "tier": "Work by paid or local contribution"}
    bases = {"node": "Final retained work · one work item per slice",
             "model": "Retained path · reported model when available, otherwise requested model",
             "handling": "Final resolution and recorded worker pool",
             "tier": "Contribution to the final result, not billed cost"}
    return Distribution(title=titles[key], basis=bases[key], total=total,
                        slices=[DistributionSlice(key=name, label=LABELS[key][name],
                                    count=counts[name],
                                    percent=round(100 * counts[name] / total, 1) if total else None)
                                for name in ORDER[key]])


def query_distributions(conn, work_ids: set[str], execution_facts: dict, as_of) -> dict[str, Distribution]:
    """Use the exact metrics cohort and a single resolution per work item."""
    total = len(work_ids)
    counts = {key: Counter() for key in ORDER}
    if not total:
        return {key: _distribution(key, counts[key], 0) for key in ORDER}
    ids = list(work_ids)
    resolutions = dict(conn.execute("""SELECT DISTINCT ON (work_item_id) work_item_id,outcome
        FROM work_resolutions WHERE work_item_id=ANY(%s) AND recorded_at<=%s
        ORDER BY work_item_id,recorded_at DESC,resolution_id DESC""", (ids, as_of)))
    pools = defaultdict(set)
    call_pools = {}
    for work_id, call_id, pool in conn.execute("""SELECT DISTINCT c.work_item_id,c.call_id,j.pool
        FROM runtime_jobs j JOIN calls c ON c.call_id=j.call_id
        WHERE c.work_item_id=ANY(%s) AND c.execution_mode='production' AND j.admitted_at<=%s""", (ids, as_of)):
        pools[work_id].add(pool)
        call_pools[call_id] = pool
    for work_id, call_id, destination in conn.execute("""SELECT e.work_item_id,e.call_id,e.payload->>'destination'
        FROM events e JOIN calls c ON c.call_id=e.call_id
        WHERE e.work_item_id=ANY(%s) AND c.execution_mode='production'
          AND e.event_type='call.dispatched' AND e.occurred_at<=%s
          AND COALESCE(e.payload->>'execution_mode','production')='production'""", (ids, as_of)):
        pool = {"skynet": "qwen", "claude": "claude", "gemma": "gemma"}.get(destination)
        if pool:
            pools[work_id].add(pool)
            call_pools[call_id] = pool
    models = defaultdict(lambda: defaultdict(set))
    for work_id, call_id, execution_id, kind, requested, reported in conn.execute("""
        SELECT c.work_item_id,c.call_id,e.model_execution_id,e.event_type,e.requested_model,e.reported_model
        FROM events e JOIN calls c ON c.call_id=e.call_id
        WHERE c.work_item_id=ANY(%s) AND c.execution_mode='production'
          AND e.event_type IN ('model.selected','model.reported') AND e.occurred_at<=%s
          AND e.model_execution_id IS NOT NULL""", (ids, as_of)):
        name = reported if kind == "model.reported" else requested
        pool = call_pools.get(call_id)
        if name and pool:
            models[work_id][pool].add((execution_id, kind, model_family(name)))
    for work_id, model in conn.execute("""SELECT e.work_item_id,e.payload->'usage'->>'runtime_model'
        FROM events e WHERE e.work_item_id=ANY(%s) AND e.event_type='direct_task.recorded'
          AND e.occurred_at<=%s AND e.payload->'usage'->>'runtime_model' IS NOT NULL""", (ids, as_of)):
        models[work_id]["codex"].add(("direct", "model.reported", model_family(model)))
    for work_id in ids:
        facts = execution_facts[work_id]
        by_pool = {}
        for pool, values in models[work_id].items():
            reported_ids = {execution_id for execution_id, kind, _ in values if kind == "model.reported"}
            by_pool[pool] = {family for execution_id, kind, family in values
                             if kind == "model.reported" or execution_id not in reported_ids}
        if facts["execution_path"] == "direct_codex":
            report = facts.get("direct_execution") or {}
            family = model_family(report.get("requested_model"))
            if family and not by_pool.get("codex"):
                by_pool["codex"] = {family}
        assignment = classify(facts["execution_path"], resolutions.get(work_id), pools[work_id], by_pool)
        for key, value in assignment.items():
            counts[key][value] += 1
    return {key: _distribution(key, counts[key], total) for key in ORDER}
