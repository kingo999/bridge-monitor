import hashlib
import importlib
import uuid

import psycopg
import pytest

from bridge.runtime.completions import record_completion
from bridge.runtime.admission import admit_call
from bridge.runtime.ownership import claim_dispatch, register_incarnation
from contracts.outcomes import CompletionEnvelope
from contracts.work import CallRequest, ExecutionBinding, TaskPacket
from tests.integration.test_admission import _policy
from tests.integration.test_dispositions import _seed_chain, _local_result


def _paid_result(dsn, claude, binding, *, delivered=True, body="Claude says 518"):
    receipt = record_completion(dsn, CompletionEnvelope.model_validate({
        "call_id": claude, "attempt_id": binding.attempt_id,
        "result": {"result_id": f"result-{claude}", "content_id": f"content-{claude}",
                   "delivered": delivered}}), binding, answer_bytes=body.encode("utf-8"))
    return receipt


def test_delivered_claude_result_creates_one_gpt_obligation(runtime_dsn):
    claude, _, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    paid = _paid_result(runtime_dsn, claude, bindings[claude])
    with psycopg.connect(runtime_dsn) as conn:
        parent = conn.execute("SELECT parent_call_id FROM calls WHERE call_id=%s",
                              (claude,)).fetchone()[0]
        rows = conn.execute("SELECT result_id,consumer_call_id,stage,content_sha256 "
                            "FROM runtime_disposition_obligations WHERE result_id=%s",
                            (paid.answer.result_id,)).fetchall()
    assert rows == [(paid.answer.result_id, parent, "gpt",
                     hashlib.sha256(b"Claude says 518").hexdigest())]


def test_undelivered_claude_result_has_no_gpt_obligation(runtime_dsn):
    claude, _, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    paid = _paid_result(runtime_dsn, claude, bindings[claude], delivered=False)
    with psycopg.connect(runtime_dsn) as conn:
        count = conn.execute("SELECT count(*) FROM runtime_disposition_obligations "
                             "WHERE result_id=%s AND stage='gpt'",
                             (paid.answer.result_id,)).fetchone()[0]
    assert count == 0


def _ack(dsn, claude, paid, *, judgment="accepted", answer="GPT retained 518",
         acknowledgement_id=None, supersedes_id=None, input_sha256=None,
         producer_id="test-parent-producer"):
    module = importlib.import_module("bridge.runtime.parent_acknowledgements")
    contract = importlib.import_module("contracts.outcomes").ParentAcknowledgement
    report = contract.model_validate({
        "acknowledgement_id": acknowledgement_id or str(uuid.uuid4()),
        "result_id": paid.answer.result_id,
        "input_sha256": input_sha256 or paid.answer.sha256,
        "judgment": judgment, "reason": "parent assessment",
        "answer_text": answer, "supersedes_id": supersedes_id})
    return module.record_parent_acknowledgement(dsn, call_id=claude,
        producer_id=producer_id, report=report)


def test_parent_acceptance_retains_exact_output_and_replays_once(runtime_dsn):
    claude, _, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    paid = _paid_result(runtime_dsn, claude, bindings[claude])
    report_id = str(uuid.uuid4())
    first = _ack(runtime_dsn, claude, paid, answer="Résumé: 518 ✨",
                 acknowledgement_id=report_id)
    replay = _ack(runtime_dsn, claude, paid, answer="Résumé: 518 ✨",
                  acknowledgement_id=report_id, producer_id="retry-producer")
    assert first.created and not replay.created
    assert first.output_result_id == replay.output_result_id
    with psycopg.connect(runtime_dsn) as conn:
        row = conn.execute("SELECT d.actor_id,d.stage,d.judgment,d.input_sha256,d.output_result_id,"
                           "d.details,r.call_id,r.delivered_at,c.raw_bytes,c.sha256,c.capture_layer "
                           "FROM dispositions d JOIN results r ON r.result_id=d.output_result_id "
                           "JOIN content_objects c ON c.content_id=r.raw_content_id "
                           "WHERE d.disposition_id=%s", (report_id,)).fetchone()
        links = conn.execute("SELECT relation FROM result_links WHERE from_result_id=%s "
                             "AND to_result_id=%s", (paid.answer.result_id,
                                                     first.output_result_id)).fetchall()
    assert row[0].startswith("gpt-parent:gpt:")
    assert row[1:4] == ("gpt", "accepted", paid.answer.sha256)
    assert row[4] == first.output_result_id
    assert row[5]["provenance"] == "parent_mcp_report"
    assert row[6].startswith("gpt:") and row[7] is None
    assert row[8] == "Résumé: 518 ✨".encode("utf-8")
    assert row[9] == hashlib.sha256(row[8]).hexdigest()
    assert row[10] == "parent_mcp_submission"
    assert links == [("accepted_contribution",)]


@pytest.mark.parametrize("judgment,answer,relation", [
    ("revised", "GPT corrected 518", "revised_contribution"),
    ("replaced", "GPT solved afresh", "replacement"),
    ("unused", None, None),
])
def test_parent_judgment_lineage(runtime_dsn, judgment, answer, relation):
    claude, _, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    paid = _paid_result(runtime_dsn, claude, bindings[claude])
    receipt = _ack(runtime_dsn, claude, paid, judgment=judgment, answer=answer)
    with psycopg.connect(runtime_dsn) as conn:
        links = conn.execute("SELECT relation FROM result_links WHERE from_result_id=%s",
                             (paid.answer.result_id,)).fetchall()
    assert links == ([(relation,)] if relation else [])
    assert bool(receipt.output_result_id) == bool(answer)


def test_unused_parent_answer_is_retained_without_claiming_output_contribution(runtime_dsn):
    claude, _, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    paid = _paid_result(runtime_dsn, claude, bindings[claude])
    receipt = _ack(runtime_dsn, claude, paid, judgment="unused", answer="Different topic")
    with psycopg.connect(runtime_dsn) as conn:
        output, details = conn.execute("SELECT output_result_id,details FROM dispositions WHERE "
                              "disposition_id=%s", (receipt.acknowledgement_id,)).fetchone()
    assert receipt.output_result_id is not None
    assert output is None
    assert details["parent_output_result_id"] == receipt.output_result_id
    assert not _ack(runtime_dsn, claude, paid, judgment="unused", answer="Different topic",
                    acknowledgement_id=receipt.acknowledgement_id).created
    with pytest.raises(ValueError, match="conflicts with different report"):
        _ack(runtime_dsn, claude, paid, judgment="unused", answer="Changed topic",
             acknowledgement_id=receipt.acknowledgement_id)


def test_parent_report_rejects_wrong_call_hash_and_conflicting_replay(runtime_dsn):
    claude, other, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    paid = _paid_result(runtime_dsn, claude, bindings[claude])
    local = _local_result(runtime_dsn, other, bindings[other])
    with pytest.raises((PermissionError, ValueError)):
        _ack(runtime_dsn, other, paid)
    with pytest.raises((PermissionError, ValueError)):
        _ack(runtime_dsn, claude, local)
    with pytest.raises((PermissionError, ValueError)):
        _ack(runtime_dsn, claude, paid, input_sha256="0" * 64)
    report_id = str(uuid.uuid4())
    _ack(runtime_dsn, claude, paid, acknowledgement_id=report_id)
    with pytest.raises(ValueError):
        _ack(runtime_dsn, claude, paid, acknowledgement_id=report_id,
             answer="different output")
    with psycopg.connect(runtime_dsn) as conn:
        count = conn.execute("SELECT count(*) FROM dispositions WHERE result_id=%s "
                             "AND stage='gpt'", (paid.answer.result_id,)).fetchone()[0]
    assert count == 1


def test_event_publication_failure_rolls_back_parent_report(runtime_dsn, monkeypatch):
    module = importlib.import_module("bridge.runtime.parent_acknowledgements")
    claude, _, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    paid = _paid_result(runtime_dsn, claude, bindings[claude])
    report_id = str(uuid.uuid4())

    def fail_publication(*args, **kwargs):
        raise RuntimeError("event transaction failed")

    with monkeypatch.context() as patch:
        patch.setattr(module, "ingest_batch", fail_publication)
        with pytest.raises(RuntimeError, match="event transaction failed"):
            _ack(runtime_dsn, claude, paid, acknowledgement_id=report_id)
    with psycopg.connect(runtime_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM dispositions WHERE disposition_id=%s",
                            (report_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM result_links WHERE from_result_id=%s",
                            (paid.answer.result_id,)).fetchone()[0] == 0
    assert _ack(runtime_dsn, claude, paid, acknowledgement_id=report_id).created


def test_parent_contract_rejects_actor_claim_and_missing_answer():
    from pydantic import ValidationError
    from contracts.outcomes import ParentAcknowledgement
    base = {"acknowledgement_id": "id", "result_id": "result",
            "input_sha256": "a" * 64, "judgment": "accepted",
            "answer_text": "retained"}
    with pytest.raises(ValidationError):
        ParentAcknowledgement.model_validate({**base, "actor_id": "claude-worker"})
    with pytest.raises(ValidationError):
        ParentAcknowledgement.model_validate({**base, "answer_text": None})


def test_changed_parent_judgment_requires_supersession(runtime_dsn):
    claude, _, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    paid = _paid_result(runtime_dsn, claude, bindings[claude])
    first = _ack(runtime_dsn, claude, paid, judgment="accepted")
    with pytest.raises(ValueError):
        _ack(runtime_dsn, claude, paid, judgment="revised", answer="corrected")
    second = _ack(runtime_dsn, claude, paid, judgment="revised", answer="corrected",
                  supersedes_id=first.acknowledgement_id)
    assert second.created
    with psycopg.connect(runtime_dsn) as conn:
        effective = conn.execute("SELECT disposition_id FROM dispositions d "
                                 "WHERE result_id=%s AND stage='gpt' AND NOT EXISTS "
                                 "(SELECT 1 FROM dispositions s WHERE s.supersedes_id=d.disposition_id)",
                                 (paid.answer.result_id,)).fetchall()
    assert effective == [(second.acknowledgement_id,)]


@pytest.mark.parametrize("new_judgment,new_answer,new_relation", [
    ("revised", "corrected", "revised_contribution"),
    ("unused", "solved separately", None),
])
def test_supersession_replaces_effective_parent_lineage(runtime_dsn,
        new_judgment, new_answer, new_relation):
    claude, _, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    paid = _paid_result(runtime_dsn, claude, bindings[claude])
    first = _ack(runtime_dsn, claude, paid, judgment="accepted", answer="kept")
    second = _ack(runtime_dsn, claude, paid, judgment=new_judgment,
                  answer=new_answer, supersedes_id=first.acknowledgement_id)
    with psycopg.connect(runtime_dsn) as conn:
        links = conn.execute("SELECT to_result_id,relation FROM result_links "
            "WHERE from_result_id=%s", (paid.answer.result_id,)).fetchall()
    assert links == ([(second.output_result_id, new_relation)] if new_relation else [])


def test_sibling_claude_results_have_separate_judgments_and_shared_parent_output(runtime_dsn):
    claude, _, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt", budget=3)
    paid = _paid_result(runtime_dsn, claude, bindings[claude])
    sibling = f"claude-{uuid.uuid4()}"
    with psycopg.connect(runtime_dsn) as conn:
        parent, work = conn.execute("SELECT parent_call_id,work_item_id FROM calls "
                                    "WHERE call_id=%s", (claude,)).fetchone()
        root = conn.execute("SELECT task_id FROM work_items WHERE work_item_id=%s",
                            (work,)).fetchone()[0]
        conn.execute("INSERT INTO calls(call_id,work_item_id,parent_call_id,requested_model,"
                     "created_at) VALUES(%s,%s,%s,'claude-test',now())",
                     (sibling, work, parent))
        conn.execute("INSERT INTO attempts(attempt_id,call_id,attempt_number,status) "
                     "VALUES(%s,%s,1,'started')", (f"{sibling}:attempt:1", sibling))
        version = conn.execute("SELECT version FROM runtime_active_policy "
                               "WHERE singleton=true").fetchone()[0]
    task = TaskPacket(task_id=root, work_item_id=work, category="analysis",
                      scope=["read"], token_budget=1000, call_budget=3)
    decision = admit_call(runtime_dsn, CallRequest(call_id=sibling, task=task,
        requested_model="claude-test", parent_call_id=parent), _policy(),
        policy_version=version, pool="claude", reserve_tokens=100)
    assert decision.state == "queued"
    register_incarnation(runtime_dsn, f"inc-{sibling}", f"worker-{sibling}")
    lease = claim_dispatch(runtime_dsn, pool="claude", incarnation_id=f"inc-{sibling}")
    assert lease.call_id == sibling
    sibling_binding = ExecutionBinding(worker_instance_id=f"worker-{sibling}",
        producer_id=f"producer-{sibling}", producer_incarnation=f"inc-{sibling}",
        call_id=sibling, attempt_id=f"{sibling}:attempt:1")
    other_paid = _paid_result(runtime_dsn, sibling, sibling_binding)
    one = _ack(runtime_dsn, claude, paid, answer="Shared GPT response")
    two = _ack(runtime_dsn, sibling, other_paid, answer="Shared GPT response")
    assert one.output_result_id == two.output_result_id
    with psycopg.connect(runtime_dsn) as conn:
        rows = conn.execute("SELECT result_id,consumer_call_id FROM "
            "runtime_disposition_obligations WHERE stage='gpt' AND result_id=ANY(%s)",
            ([paid.answer.result_id, other_paid.answer.result_id],)).fetchall()
    assert set(rows) == {(paid.answer.result_id, parent), (other_paid.answer.result_id, parent)}
