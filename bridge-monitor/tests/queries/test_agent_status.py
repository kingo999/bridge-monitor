from datetime import datetime, timezone

from service.queries.history import derive_agent_statuses


def test_agent_status_keeps_reachability_output_and_timeout_separate():
    t1 = datetime(2026, 10, 17, 19, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 17, 19, 1, tzinfo=timezone.utc)
    events = [
        {"call_id": "child-1", "event_type": "attempt.status", "occurred_at": t1,
         "payload": {"agent_state": "streaming", "agent_reachable": True}},
        {"call_id": "child-1", "event_type": "response.delta", "occurred_at": t1,
         "payload": {"chunk_sequence": 1}},
        {"call_id": "child-1", "event_type": "attempt.status", "occurred_at": t2,
         "payload": {"agent_state": "streaming", "agent_reachable": False}},
        {"call_id": "child-1", "event_type": "attempt.uncertain", "occurred_at": t2,
         "payload": {"reason": "idle_output_deadline"}},
    ]
    assert derive_agent_statuses(events) == [{
        "call_id": "child-1", "agent_state": "streaming",
        "agent_reachable": False, "last_model_output_at": t1,
        "timeout_reason": "idle_output_deadline"}]
