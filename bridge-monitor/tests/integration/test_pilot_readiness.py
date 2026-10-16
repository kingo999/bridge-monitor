from datetime import datetime, timedelta, timezone

from contracts.work import TaskPacket
from bridge.guidance.gates import check_readiness
from bridge.runtime.deadlines import stage_deadline


def _inputs():
    now = datetime.now(timezone.utc)
    return dict(task=TaskPacket(task_id="t", work_item_id="w", category="arithmetic",
                               scope=["analysis"], token_budget=1000, call_budget=2),
        prompt="Calculate the durations", classification_source="ingress_verified",
        parent_scope=["analysis"], required_context={"timestamps": "10:00,10:30"},
        measured_input_tokens=100, local_context_tokens=1000,
        local_reserved_output_tokens=100, paid_context_tokens=2000,
        paid_reserved_output_tokens=200, observed_owner_model="claude-opus-4",
        compatible_owner_models=["claude-opus-4"], now=now,
        parent_deadline_at=now + timedelta(seconds=120),
        client_deadline_at=now + timedelta(seconds=100),
        fallback_seconds=30, delivery_margin_seconds=5)


def test_no_guidance_ready_with_shared_cutoff():
    data = _inputs()
    result = check_readiness(**data)
    assert result.route == "local" and result.manifest == {
        "state": "no_guidance", "version": 1, "pack_ids": []}
    assert result.local_cutoff_at == data["now"] + timedelta(seconds=90)
    assert stage_deadline(started_at=data["now"] + timedelta(seconds=80),
                          stage_seconds=30, local_cutoff_at=result.local_cutoff_at) == result.local_cutoff_at


def test_unknown_category_context_budget_and_owner_use_paid_route():
    cases = [
        ({"classification_source": "model_claimed"}, "untrusted_or_unknown_category"),
        ({"parent_scope": ["other"]}, "out_of_scope"),
        ({"required_context": {"timestamps": ""}}, "required_context_missing"),
        ({"measured_input_tokens": None}, "context_measurement_unavailable"),
        ({"measured_input_tokens": 950}, "local_context_exceeded"),
        ({"paid_context_tokens": 200}, "fallback_context_exceeded"),
        ({"observed_owner_model": None}, "owner_model_incompatible"),
    ]
    for change, reason in cases:
        assert check_readiness(**(_inputs() | change)).reason == reason


def test_client_margin_or_parent_allowance_blocks_local():
    data = _inputs()
    data["client_deadline_at"] = data["now"] + timedelta(seconds=3)
    assert check_readiness(**data).reason == "fallback_time_unavailable"
