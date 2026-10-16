import uuid

import psycopg

from bridge.adapters.codex_ingress import CodexIngress
from bridge.adapters.base import AdapterResult
from bridge.adapters.qwen_runtime import ModelPreflight
from bridge.guidance.registry import register_pack
from bridge.guidance.selection import resolve_guidance
from bridge.mcp_server import BridgeCoordinator
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from contracts.policy import PolicyInput
from contracts.events import EventType
from contracts.work import TaskPacket
from bridge.runtime.policy_startup import activate_policy
from bridge.runtime.policy_reload import reload_policy
from bridge.runtime.policy_startup import active_policy
from contracts.policy import validate_policy
from tests.adapters.test_ingress import Collector
from tests.integration.test_admission import _call, _policy
from tests.integration.test_ownership import _setup


def _guided_policy(pack_id, digest, *, required=False):
    proposed = _policy().model_dump()
    proposed["guidance"] = {"packs": [{"pack_version_id": pack_id, "sha256": digest,
        "enabled": True, "categories": ["analysis"], "scopes": ["read"]}],
        "required_categories": ["analysis"] if required else [], "max_bytes": 4096}
    return PolicyInput.model_validate(proposed)


def test_registered_snapshot_is_immutable_and_unreviewed_example_is_excluded(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "guidance-test-secret")
    rule = bytearray(b"Use the repository's safe API.")
    registered = register_pack(dsn, pack_id="repo-rules", version_label="v1",
        items=[{"item_id": "rule-1", "kind": "rule", "body": rule, "required": True},
               {"item_id": "example-1", "kind": "example", "body": b"Prior answer", "required": False}],
        actor_id="reviewer-1", auth_token="guidance-test-secret")
    rule[:] = b"Use a different API instead."
    policy = _guided_policy(registered.pack_version_id, registered.sha256)
    resolution = resolve_guidance(dsn, policy, _call(f"root-{uuid.uuid4()}",
        f"call-{uuid.uuid4()}").task, "Check this method")
    assert resolution.state == "ready"
    assert "Use the repository's safe API." in resolution.effective_prompt
    assert "different API" not in resolution.effective_prompt
    assert resolution.manifest["state"] == "supplied"
    assert [item["item_id"] for item in resolution.manifest["supplied"]] == ["rule-1"]
    assert resolution.manifest["excluded"][0]["reason"] == "example_unreviewed"
    with psycopg.connect(dsn) as conn:
        body = conn.execute("SELECT raw_bytes FROM content_objects WHERE content_id=%s",
                            (resolution.manifest["supplied"][0]["content_id"],)).fetchone()[0]
    assert bytes(body) == b"Use the repository's safe API."


def test_required_guidance_and_scope_gate_do_not_silently_pass(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "guidance-test-secret")
    registered = register_pack(dsn, pack_id="repo-rules", version_label="v1",
        items=[{"item_id": "rule-1", "kind": "rule", "body": b"Rule", "required": True}],
        actor_id="reviewer-1", auth_token="guidance-test-secret")
    task = _call(f"root-{uuid.uuid4()}", f"call-{uuid.uuid4()}").task
    no_pack = _guided_policy(registered.pack_version_id, registered.sha256, required=True)
    no_pack.guidance.packs = []
    empty = resolve_guidance(dsn, no_pack, task, "Check this")
    assert empty.state == "blocked" and empty.reason == "required_guidance_missing"
    out_of_scope = _guided_policy(registered.pack_version_id, registered.sha256)
    out_of_scope.guidance.packs[0].scopes = ["write"]
    mismatch = resolve_guidance(dsn, out_of_scope, task, "Check this")
    assert mismatch.state == "ready"
    assert mismatch.manifest["state"] == "no_guidance"
    assert mismatch.manifest["excluded"][0]["reason"] == "scope_mismatch"


def test_transformed_prompt_retains_original_and_exact_effective_bytes(runtime_dsn, tmp_path):
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "guidance-spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"guidance-{uuid.uuid4()}", incarnation="boot-1")
    ingress = CodexIngress(dsn, emitter, policy, version)
    call = f"call-{uuid.uuid4()}"
    decision = ingress.submit(_call(f"root-{uuid.uuid4()}", call), prompt="Original request",
        effective_prompt="Original request\n\n[Registered guidance]\n[rule-1] Safe API",
        pool="claude", reserve_tokens=100)
    assert decision.state == "queued"
    with psycopg.connect(dsn) as conn:
        content = conn.execute("SELECT original_content_id,effective_content_id FROM messages "
                               "WHERE call_id=%s AND role='user'", (call,)).fetchone()
        dispatched_ref = conn.execute("SELECT prompt_content_id FROM runtime_jobs WHERE call_id=%s",
                                      (call,)).fetchone()[0]
        assert content[0] != content[1]
        assert dispatched_ref == content[1]
        bodies = [bytes(conn.execute("SELECT raw_bytes FROM content_objects WHERE content_id=%s",
                                     (item,)).fetchone()[0]) for item in content]
    assert bodies == [b"Original request",
                      b"Original request\n\n[Registered guidance]\n[rule-1] Safe API"]


def test_registered_rule_reaches_qwen_and_its_manifest(runtime_dsn, tmp_path, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "guidance-test-secret")
    registered = register_pack(dsn, pack_id="repo-rules", version_label="v1",
        items=[{"item_id": "rule-1", "kind": "rule", "body": b"Use the safe API.", "required": True}],
        actor_id="reviewer-1", auth_token="guidance-test-secret")
    base = _policy().model_dump()
    base["deployment"].update(qwen_model_profile="qwen-test", qwen_model_digest="a" * 64,
                              qwen_output_tokens=20)
    base["guidance"] = _guided_policy(registered.pack_version_id, registered.sha256).guidance.model_dump()
    caps = {"claude-test": {"context_tokens": 10000}}
    version = activate_policy(dsn, base, caps,
        approved_hash=validate_policy(base, caps).snapshot.source_hash,
        operator_id="reviewer-1").version
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "guided-spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"guided-{uuid.uuid4()}", incarnation="boot-1")
    captured = []

    class Qwen:
        model = "qwen-test"
        expected_digest = "a" * 64
        def preflight(self):
            return ModelPreflight(True, "model_profile_verified", "a" * 64)
        def run(self, lease, prompt, sink, task=None, deadline_at=None):
            captured.append(prompt)
            sink.emit(EventType.MODEL_SELECTED, call_id=lease.call_id,
                attempt_id=f"{lease.call_id}:attempt:1",
                model_execution_id=f"{lease.call_id}:model:1", requested_model="qwen-test")
            sink.emit(EventType.MODEL_REPORTED, call_id=lease.call_id,
                attempt_id=f"{lease.call_id}:attempt:1",
                model_execution_id=f"{lease.call_id}:model:1", reported_model="qwen-test")
            return AdapterResult(state="completed", answer="safe", reported_model="qwen-test")

    class Claude:
        def run(self, lease, task, prompt, sink):
            sink.emit(EventType.MODEL_SELECTED, call_id=lease.call_id,
                attempt_id=f"{lease.call_id}:attempt:1",
                model_execution_id=f"{lease.call_id}:model:1", requested_model="claude-test")
            sink.emit(EventType.MODEL_REPORTED, call_id=lease.call_id,
                attempt_id=f"{lease.call_id}:attempt:1",
                model_execution_id=f"{lease.call_id}:model:1", reported_model="claude-test")
            child = BridgeCoordinator(dsn, PolicyInput.model_validate(base), version, emitter,
                claude_adapter=None, qwen_adapter=Qwen(), parent_call_id=lease.call_id,
                parent_lease_id=lease.lease_id,
                local_capability={"context_tokens": 1000, "byte_upper_bound_validated": True,
                                  "client_timeout_verified": True},
                paid_capability={"context_tokens": 10000, "client_timeout_verified": True,
                                 "reported_models": ["claude-test"]})
            result = child.delegate_local_analysis("Check this method")
            assert result["state"] == "completed"
            return AdapterResult(state="completed", answer="done", reported_model="claude-test")

    parent = BridgeCoordinator(dsn, PolicyInput.model_validate(base), version, emitter,
        claude_adapter=Claude())
    task = TaskPacket(task_id=f"task-{uuid.uuid4()}", work_item_id=f"work-{uuid.uuid4()}",
                      category="analysis", scope=["read"], token_budget=1000, call_budget=2)
    assert parent.delegate_claude(task, "Check this method", requested_model="claude-test",
                                  reserve_tokens=100)["state"] == "completed"
    assert captured and "Use the safe API." in captured[0]
    with psycopg.connect(dsn) as conn:
        manifest = conn.execute("SELECT guidance_manifest FROM delegation_decisions "
                                "WHERE route='local' ORDER BY decided_at DESC LIMIT 1").fetchone()[0]
    assert manifest["pack_version_id"] == registered.pack_version_id
    assert manifest["supplied"][0]["item_id"] == "rule-1"


def test_unregistered_guidance_reference_cannot_be_activated(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "guidance-test-secret")
    _, version = _setup(dsn)
    proposed = active_policy(dsn).model_dump(exclude={"version", "source_hash"})
    proposed["guidance"] = _guided_policy("missing-pack", "a" * 64).guidance.model_dump()
    caps = {"claude-test": {"context_tokens": 10000}}
    digest = validate_policy(proposed, caps).snapshot.source_hash
    outcome = reload_policy(dsn, proposed, caps, approved_hash=digest,
        operator_id="reviewer-1", auth_token="guidance-test-secret")
    assert outcome.status == "rejected"
    assert active_policy(dsn).version == version


def test_policy_reload_between_guidance_resolution_and_admission_rejects_stale_selection(
        runtime_dsn, tmp_path, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "guidance-test-secret")
    policy, old_version = _setup(dsn)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "reload-race.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"race-{uuid.uuid4()}", incarnation="boot-1")
    ingress = CodexIngress(dsn, emitter, policy, old_version)
    proposed = active_policy(dsn).model_dump(exclude={"version", "source_hash"})
    proposed["guidance"]["required_categories"] = ["analysis"]
    caps = {"claude-test": {"context_tokens": 10000}}
    digest = validate_policy(proposed, caps).snapshot.source_hash
    assert reload_policy(dsn, proposed, caps, approved_hash=digest,
        operator_id="reviewer-1", auth_token="guidance-test-secret").status == "activated"
    call_id = f"call-{uuid.uuid4()}"
    decision = ingress.submit(_call(f"root-{uuid.uuid4()}", call_id), prompt="Check this",
        pool="qwen", reserve_tokens=100, expected_policy_version=old_version,
        guidance_manifest={"state": "no_guidance", "supplied": []},
        guidance_decision_id=str(uuid.uuid4()))
    assert decision.state == "rejected" and decision.reason == "policy_changed"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_jobs WHERE call_id=%s",
                            (call_id,)).fetchone()[0] == 0
