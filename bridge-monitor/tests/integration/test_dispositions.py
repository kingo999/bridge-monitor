import hashlib
import uuid
from datetime import datetime, timezone, timedelta

import psycopg

from bridge.runtime.admission import admit_call
from bridge.runtime.completions import record_completion
from bridge.runtime.ownership import claim_dispatch, register_incarnation
from contracts.work import CallRequest, ExecutionBinding, TaskPacket
from tests.integration.test_admission import _policy
from tests.integration.test_ownership import _setup


def _seed_chain(dsn, *, dispatch_qwen=True):
    policy, version = _setup(dsn)
    root = f"root-{uuid.uuid4()}"
    work = f"work-{uuid.uuid4()}"
    claude, qwen = f"claude-{uuid.uuid4()}", f"qwen-{uuid.uuid4()}"
    task = TaskPacket(task_id=root, work_item_id=work, category="analysis",
                      scope=["read"], token_budget=1000, call_budget=2)
    now = datetime.now(timezone.utc)
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO tasks(task_id,origin,scope,created_at) VALUES(%s,'synthetic','{}',%s)", (root, now))
        conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,created_at) "
                     "VALUES(%s,%s,'synthetic',%s)", (work, root, now))
        conn.execute("INSERT INTO calls(call_id,work_item_id,requested_model,created_at) "
                     "VALUES(%s,%s,'claude-test',%s)", (claude, work, now))
        conn.execute("INSERT INTO calls(call_id,work_item_id,parent_call_id,parent_attempt_id,"
                     "requested_model,created_at) VALUES(%s,%s,%s,%s,'qwen-test',%s)",
                     (qwen, work, claude, f"{claude}:attempt:1", now))
        for call in (claude, qwen):
            conn.execute("INSERT INTO attempts(attempt_id,call_id,attempt_number,status) "
                         "VALUES(%s,%s,1,'started')", (f"{call}:attempt:1", call))
        conn.execute("INSERT INTO model_executions(model_execution_id,attempt_id,requested_model,reported_model) "
                     "VALUES(%s,%s,'claude-test','claude-test')",
                     (f"{claude}:model:1", f"{claude}:attempt:1"))
    for call, pool in ((claude, "claude"), (qwen, "qwen")):
        request = CallRequest(call_id=call, task=task, requested_model=f"{pool}-test",
                              parent_call_id=claude if pool == "qwen" else None)
        decision = admit_call(dsn, request, policy, policy_version=version, pool=pool,
                              reserve_tokens=100,
                              local_cutoff_at=now + timedelta(seconds=20) if pool == "qwen" else None)
        assert decision.state == "queued"
        register_incarnation(dsn, f"inc-{call}", f"worker-{call}")
        if pool != "qwen" or dispatch_qwen:
            lease = claim_dispatch(dsn, pool=pool, incarnation_id=f"inc-{call}")
            assert lease.call_id == call
    bindings = {call: ExecutionBinding(worker_instance_id=f"worker-{call}",
        producer_id=f"producer-{call}", producer_incarnation=f"inc-{call}",
        call_id=call, attempt_id=f"{call}:attempt:1") for call in (claude, qwen)}
    return claude, qwen, bindings


def _local_result(dsn, qwen, binding, *, delivered=True):
    body = b"local draft"
    envelope = {"call_id": qwen, "attempt_id": binding.attempt_id,
                "result": {"result_id": f"result-{qwen}", "content_id": f"content-{qwen}",
                           "delivered": delivered, "sha256": hashlib.sha256(body).hexdigest()}}
    return record_completion(dsn, __import__("contracts.outcomes", fromlist=["CompletionEnvelope"]).CompletionEnvelope.model_validate(envelope),
                             binding, answer_bytes=body, consumer_stage="claude")


def test_missing_disposition_does_not_erase_answer(runtime_dsn):
    dsn = runtime_dsn
    claude, qwen, bindings = _seed_chain(dsn)
    _local_result(dsn, qwen, bindings[qwen])
    from contracts.outcomes import CompletionEnvelope
    answer = CompletionEnvelope.model_validate({"call_id": claude, "attempt_id": bindings[claude].attempt_id,
        "result": {"result_id": f"result-{claude}", "content_id": f"content-{claude}", "delivered": True}})
    receipt = record_completion(dsn, answer, bindings[claude], answer_bytes=b"paid answer")
    assert receipt.answer.delivered and not receipt.accounting_complete
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM results WHERE result_id=%s", (f"result-{claude}",)).fetchone()[0] == 1


def test_explicit_acceptance_and_spoofed_actor_rejection(runtime_dsn):
    dsn = runtime_dsn
    claude, qwen, bindings = _seed_chain(dsn)
    local = _local_result(dsn, qwen, bindings[qwen])
    from contracts.outcomes import CompletionEnvelope
    base = {"call_id": claude, "attempt_id": bindings[claude].attempt_id,
        "result": {"result_id": f"result-{claude}", "content_id": f"content-{claude}", "delivered": True}}
    spoofed = CompletionEnvelope.model_validate({**base, "dispositions": [{
        "disposition_id": f"d-{uuid.uuid4()}", "result_id": local.answer.result_id,
        "actor_id": "forged", "stage": "claude", "judgment": "accepted",
        "input_sha256": local.answer.sha256, "output_result_id": f"result-{claude}"}]})
    first = record_completion(dsn, spoofed, bindings[claude], answer_bytes=b"paid answer")
    assert first.answer.delivered and not first.accounting_complete
    assert any("spoofing" in item for item in first.diagnostics)
    valid = CompletionEnvelope.model_validate({**base, "dispositions": [{
        "disposition_id": f"d-{uuid.uuid4()}", "result_id": local.answer.result_id,
        "actor_id": bindings[claude].worker_instance_id, "stage": "claude", "judgment": "accepted",
        "input_sha256": local.answer.sha256, "output_result_id": f"result-{claude}"}],
        "contributions": [{"from_result_id": local.answer.result_id,
                           "to_result_id": f"result-{claude}", "relation": "accepted_contribution"}]})
    second = record_completion(dsn, valid, bindings[claude], answer_bytes=b"paid answer")
    assert second.accounting_complete and not second.diagnostics


def test_undelivered_late_local_result_creates_no_obligation(runtime_dsn):
    dsn = runtime_dsn
    claude, qwen, bindings = _seed_chain(dsn)
    _local_result(dsn, qwen, bindings[qwen], delivered=False)
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_disposition_obligations "
                            "WHERE consumer_call_id=%s", (claude,)).fetchone()[0] == 0
