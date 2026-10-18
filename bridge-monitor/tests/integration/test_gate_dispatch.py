import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from bridge.guidance.registry import register_pack
from service.ingest.example_reviews import record_example_review
from bridge.adapters.codex_ingress import CodexIngress
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from bridge.runtime.ownership import claim_dispatch
from bridge.runtime.policy_reload import reload_policy
from bridge.runtime.policy_startup import active_policy
from contracts.policy import validate_policy
from psycopg.types.json import Jsonb
from tests.integration.test_dispositions import _seed_chain
from tests.integration.test_admission import _call
from tests.integration.test_ownership import _setup
from tests.adapters.test_ingress import Collector


@pytest.mark.parametrize("restriction,expected", [
    ("disabled", "guidance_withdrawn"), ("scope", "guidance_scope_withdrawn")])
def test_pack_withdrawn_after_queue_admission_blocks_local_dispatch(runtime_dsn, monkeypatch,
                                                                     restriction, expected):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "guidance-test-secret")
    owner, child, _ = _seed_chain(dsn, dispatch_qwen=False)
    registered = register_pack(dsn, pack_id="repo-rules", version_label="v1",
        items=[{"item_id": "rule-1", "kind": "rule", "body": b"Rule", "required": True}],
        actor_id="reviewer-1", auth_token="guidance-test-secret")
    caps = {"claude-test": {"context_tokens": 10000}}
    proposed = active_policy(dsn).model_dump(exclude={"version", "source_hash"})
    proposed["guidance"] = {"packs": [{"pack_version_id": registered.pack_version_id,
        "sha256": registered.sha256, "enabled": True, "categories": ["analysis"],
        "scopes": ["read"]}], "required_categories": ["analysis"], "max_bytes": 4096}
    approved = validate_policy(proposed, caps).snapshot.source_hash
    assert reload_policy(dsn, proposed, caps, approved_hash=approved,
        operator_id="reviewer-1", auth_token="guidance-test-secret").status == "activated"
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,"
            "route,eligibility,reason,policy_version,guidance_manifest,gate_facts,decided_at) "
            "SELECT %s,work_item_id,call_id,'local','eligible','guidance_ready',%s,%s,%s,now() "
            "FROM runtime_jobs WHERE call_id=%s", (str(uuid.uuid4()), active_policy(dsn).version,
            Jsonb({"state": "supplied", "pack_version_id": registered.pack_version_id,
                   "pack_sha256": registered.sha256, "supplied": [{"item_id": "rule-1"}]}),
            Jsonb({"guidance_gate": "pass"}), child))
    if restriction == "disabled":
        proposed["guidance"]["packs"][0]["enabled"] = False
    else:
        proposed["guidance"]["packs"][0]["scopes"] = ["write"]
    withdrawn = validate_policy(proposed, caps).snapshot.source_hash
    assert reload_policy(dsn, proposed, caps, approved_hash=withdrawn,
        operator_id="reviewer-1", auth_token="guidance-test-secret").status == "activated"
    assert claim_dispatch(dsn, pool="qwen", incarnation_id=f"inc-{child}") is None
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone()[0] == "queued"
        assert conn.execute("SELECT reason FROM delegation_decisions WHERE child_call_id=%s "
                            "ORDER BY decided_at DESC LIMIT 1", (child,)).fetchone()[0] == expected


def test_guidance_decision_is_durable_before_queued_child_can_be_claimed(runtime_dsn, tmp_path):
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "pin-spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"pin-{uuid.uuid4()}", incarnation="boot-1")
    ingress = CodexIngress(dsn, emitter, policy, version)
    call = f"call-{uuid.uuid4()}"
    manifest = {"state": "supplied", "version": 1,
                "pack_version_id": "registered-pack", "pack_sha256": "a" * 64,
                "supplied": [{"item_id": "rule-1", "kind": "rule"}]}
    decision = ingress.submit(_call(f"root-{uuid.uuid4()}", call), prompt="Original",
        effective_prompt="Original\nGuidance", pool="qwen", reserve_tokens=100,
        local_cutoff_at=datetime.now(timezone.utc) + timedelta(seconds=20),
        guidance_manifest=manifest, gate_facts={"guidance_gate": "pass"},
        guidance_decision_id=f"decision-{uuid.uuid4()}")
    assert decision.state == "queued"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT guidance_manifest FROM delegation_decisions "
                            "WHERE child_call_id=%s AND route='local'", (call,)).fetchone()[0] == manifest
        assert conn.execute("SELECT decision_stage FROM delegation_decisions "
                            "WHERE child_call_id=%s AND route='local'", (call,)).fetchone()[0] == "claude_child"


def test_review_scope_narrowed_after_queue_prevents_example_dispatch(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "guidance-test-secret")
    owner, child, _ = _seed_chain(dsn, dispatch_qwen=False)
    source_result = f"source-{uuid.uuid4()}"
    with psycopg.connect(dsn) as conn:
        work_id = conn.execute("SELECT work_item_id FROM runtime_jobs WHERE call_id=%s",
                               (child,)).fetchone()[0]
        conn.execute("INSERT INTO results(result_id,call_id) VALUES(%s,%s)", (source_result, owner))
    registered = register_pack(dsn, pack_id=f"examples-{uuid.uuid4()}", version_label="v1",
        items=[{"item_id": "example-1", "kind": "example", "body": b"Reviewed example",
                "source_work_item_id": work_id, "source_result_id": source_result}],
        actor_id="reviewer-1", auth_token="guidance-test-secret")
    first = record_example_review(dsn, registered.pack_version_id, "example-1",
        reviewer_id="reviewer-1", auth_token="guidance-test-secret", status="approved",
        permitted_use="prompt_example", exposure="unknown", scope=["read"],
        evidence={"validation": {"check": "manual", "outcome": "pass"}})
    caps = {"claude-test": {"context_tokens": 10000}}
    proposed = active_policy(dsn).model_dump(exclude={"version", "source_hash"})
    proposed["guidance"] = {"packs": [{"pack_version_id": registered.pack_version_id,
        "sha256": registered.sha256, "enabled": True, "categories": ["analysis"],
        "scopes": ["read", "write"]}], "required_categories": [], "max_bytes": 4096}
    approved = validate_policy(proposed, caps).snapshot.source_hash
    assert reload_policy(dsn, proposed, caps, approved_hash=approved,
        operator_id="reviewer-1", auth_token="guidance-test-secret").status == "activated"
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,"
            "route,eligibility,policy_version,guidance_manifest,gate_facts,decided_at) "
            "VALUES(%s,%s,%s,'local','eligible',%s,%s,'{}',now())",
            (str(uuid.uuid4()), work_id, child, active_policy(dsn).version,
             Jsonb({"state": "supplied", "pack_version_id": registered.pack_version_id,
                    "pack_sha256": registered.sha256,
                    "supplied": [{"item_id": "example-1", "kind": "example",
                                  "review_id": first.review_id}]})))
    record_example_review(dsn, registered.pack_version_id, "example-1",
        reviewer_id="reviewer-2", auth_token="guidance-test-secret", status="approved",
        permitted_use="prompt_example", exposure="unknown", scope=["write"],
        evidence={"validation": {"check": "manual", "outcome": "pass"}},
        supersedes_id=first.review_id)
    assert claim_dispatch(dsn, pool="qwen", incarnation_id=f"inc-{child}") is None
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT reason FROM delegation_decisions WHERE child_call_id=%s "
                            "ORDER BY decided_at DESC,decision_id DESC LIMIT 1",
                            (child,)).fetchone()[0] == "example_scope_withdrawn"


def test_successful_dispatch_records_current_policy_and_review_ids(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "guidance-test-secret")
    owner, child, _ = _seed_chain(dsn, dispatch_qwen=False)
    with psycopg.connect(dsn) as conn:
        work_id = conn.execute("SELECT work_item_id FROM runtime_jobs WHERE call_id=%s",
                               (child,)).fetchone()[0]
        source_result = f"source-{uuid.uuid4()}"
        conn.execute("INSERT INTO results(result_id,call_id) VALUES(%s,%s)", (source_result, owner))
    registered = register_pack(dsn, pack_id=f"audit-{uuid.uuid4()}", version_label="v1",
        items=[{"item_id": "example-1", "kind": "example", "body": b"Reviewed example",
                "source_work_item_id": work_id, "source_result_id": source_result}],
        actor_id="reviewer-1", auth_token="guidance-test-secret")
    review = record_example_review(dsn, registered.pack_version_id, "example-1",
        reviewer_id="reviewer-1", auth_token="guidance-test-secret", status="approved",
        permitted_use="prompt_example", exposure="unknown", scope=["read"],
        evidence={"validation": {"check": "manual", "outcome": "pass"}})
    caps = {"claude-test": {"context_tokens": 10000}}
    proposed = active_policy(dsn).model_dump(exclude={"version", "source_hash"})
    proposed["guidance"] = {"packs": [{"pack_version_id": registered.pack_version_id,
        "sha256": registered.sha256, "enabled": True, "categories": ["analysis"],
        "scopes": ["read"]}], "required_categories": [], "max_bytes": 4096}
    digest = validate_policy(proposed, caps).snapshot.source_hash
    current = reload_policy(dsn, proposed, caps, approved_hash=digest,
        operator_id="reviewer-1", auth_token="guidance-test-secret")
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,"
            "route,eligibility,policy_version,guidance_manifest,gate_facts,decided_at) "
            "VALUES(%s,%s,%s,'local','eligible',%s,%s,'{}',now())",
            (str(uuid.uuid4()), work_id, child, current.version,
             Jsonb({"state": "supplied", "pack_version_id": registered.pack_version_id,
                    "pack_sha256": registered.sha256,
                    "supplied": [{"item_id": "example-1", "kind": "example",
                                  "review_id": review.review_id}]})))
    assert claim_dispatch(dsn, pool="qwen", incarnation_id=f"inc-{child}") is not None
    with psycopg.connect(dsn) as conn:
        audit = conn.execute("SELECT policy_version,gate_facts FROM delegation_decisions "
            "WHERE child_call_id=%s AND reason='dispatch_recheck_passed'", (child,)).fetchone()
    assert audit[0] == current.version
    assert audit[1]["current_policy_version"] == current.version
    assert audit[1]["example_review_ids"] == [review.review_id]
