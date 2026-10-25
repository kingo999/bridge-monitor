"""Scoped observed counters for the new view, with explicit unsupported gaps."""
from collections import defaultdict
from datetime import timezone
from service.queries.usage_reconciliation import reconcile_execution_usage
COUNTERS=("input_tokens","output_tokens","cache_read_input_tokens","cache_creation_input_tokens")

def observed_counters(events, *, direct):
    if direct:
        turns={}
        ambiguous=False
        for event in events:
            if event["event_type"]!="direct_task.recorded":continue
            payload=event["payload"];usage=payload.get("usage")
            turn=payload.get("source_turn_id")
            if not usage or not turn or usage.get("scope")!="turn":continue
            counters={key:usage.get(key) for key in COUNTERS}
            previous=turns.get(turn)
            if previous is not None and previous!=counters:
                ambiguous=True
            turns[turn]=counters
        return {key:sum(values) if values and not ambiguous and all(type(v) is int for v in values) else None
            for key in COUNTERS for values in [[turn[key] for turn in turns.values()]]}
    groups=defaultdict(list)
    expected=set()
    for event in events:
        execution=event.get("model_execution_id")
        if not execution:continue
        if event["event_type"] in ("model.selected","model.reported"):expected.add(execution)
        if event["event_type"]!="usage.reported":continue
        expected.add(execution)
        p=event["payload"]
        groups[execution].append(dict(event_id=event["event_id"],payload_hash=event["payload_hash"],
            observation_id=p.get("observation_id"),model_execution_id=execution,
            occurred_at=event["occurred_at"].astimezone(timezone.utc),
            producer_id=event["producer_id"],producer_incarnation=event["producer_incarnation"],
            origin=event["origin"],capture_completeness=event["capture_completeness"],
            unresolved=bool(event["unresolved_references"]),scope=p.get("scope"),
            is_cumulative=p.get("is_cumulative"),provenance=p.get("provenance"),
            **{key:p.get(key) for key in COUNTERS}))
    reconciled=[reconcile_execution_usage(execution,groups[execution]) for execution in expected]
    return {key:sum(values) if values and all(type(v) is int for v in values) else None
        for key in COUNTERS for values in [[item["counters"][key] for item in reconciled]]}
