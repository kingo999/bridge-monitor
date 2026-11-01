"""DB-backed corrective fixtures: receipt identity, all-execution model attribution, parent outcomes."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from contracts.queries import CohortQuery
from service.queries.codex_claude import query_codex_claude
from tests.integration.test_dispositions import _seed_chain
from tests.integration.test_parent_acknowledgements import _ack, _paid_result

SHA = "b" * 64


def _clean(dsn):
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE cc_receipts,cc_reservations,cc_decisions,cc_records")


def _work(dsn, call):
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT c.work_item_id,w.task_id FROM calls c JOIN work_items w "
                            "USING(work_item_id) WHERE c.call_id=%s", (call,)).fetchone()


def _event(conn, work, task, call, kind, n, *, reported=None, payload=None, execution=None, requested=None):
    now = datetime.now(timezone.utc) - timedelta(minutes=2) + timedelta(milliseconds=n)
    conn.execute("INSERT INTO events(event_id,event_type,schema_version,producer_id,producer_incarnation,"
                 "producer_sequence,occurred_at,received_at,origin,observation_layer,capture_completeness,"
                 "task_id,work_item_id,call_id,model_execution_id,requested_model,reported_model,payload,payload_hash) "
                 "VALUES(%s,%s,1,%s,'first',1,%s,%s,'synthetic','test','complete',%s,%s,%s,%s,%s,%s,%s,%s)",
                 (str(uuid.uuid4()), kind, "p-" + str(uuid.uuid4()), now, now, task, work, call,
                  execution, requested, reported, Jsonb(payload or {}), "h" + str(n)))


def _row(dsn, work):
    # Unique category scopes group counts to this fixture's work item only.
    category = "eff-" + uuid.uuid4().hex[:12]
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE work_items SET category=%s WHERE work_item_id=%s", (category, work))
    report = query_codex_claude(dsn, CohortQuery(origin="all"), category=category)
    return next(r for r in report["rows"] if r["work_item_id"] == work), report


def _dispatch(dsn, work, task, claude):
    with psycopg.connect(dsn) as conn:
        _event(conn, work, task, claude, "call.dispatched", 1, payload={"destination": "claude"})


def test_identical_receipt_bytes_on_distinct_calls_both_contribute(runtime_dsn):
    _clean(runtime_dsn)
    claude, qwen, _ = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    work, task = _work(runtime_dsn, claude)
    _dispatch(runtime_dsn, work, task, claude)
    with psycopg.connect(runtime_dsn) as conn:
        for call, amount in ((claude, "0.05"), (qwen, "0.02")):
            conn.execute("INSERT INTO cc_reservations(call_id,work_item_id,workflow_id,profile,amount,state) "
                         "VALUES(%s,%s,%s,'haiku',0.02,'settled')", (call, work, task))
            conn.execute("INSERT INTO cc_receipts(receipt_id,call_id,amount,receipt_sha256,rate_card_version,"
                         "counters,observed_at) VALUES(%s,%s,%s,%s,'proposal-2026-10-25',%s,%s)",
                         ("r-" + call, call, amount, SHA, Jsonb({}), datetime.now(timezone.utc) - timedelta(minutes=1)))
    row, report = _row(runtime_dsn, work)
    assert Decimal(row["cost_usd_equivalent"]) == Decimal("0.07")
    assert row["receipt_hashes"] == [SHA, SHA]
    _clean(runtime_dsn)


def test_root_and_child_models_produce_mixed_unattributed_bucket(runtime_dsn):
    _clean(runtime_dsn)
    claude, qwen, _ = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    work, task = _work(runtime_dsn, claude)
    with psycopg.connect(runtime_dsn) as conn:
        _event(conn, work, task, claude, "call.dispatched", 1, payload={"destination": "claude"})
        _event(conn, work, task, claude, "model.reported", 2, reported="claude-test")
        _event(conn, work, task, qwen, "model.reported", 3, reported="qwen-test")
    row, report = _row(runtime_dsn, work)
    assert row["reported_model"] == "claude-test"          # legacy field unchanged
    assert row["reported_models"] == ["claude-test", "qwen-test"]
    assert (row["efficiency_model"], row["efficiency_effort"]) == ("mixed", "unattributed")
    groups = [g for g in report["efficiency"]["groups"]
              if (g["reported_model"], g["configured_effort"]) == ("mixed", "unattributed")]
    assert groups and sum(g["workflows"] for g in groups) >= 1


def test_child_without_reported_model_is_not_attributed_to_root_model(runtime_dsn):
    _clean(runtime_dsn)
    claude, qwen, _ = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    work, task = _work(runtime_dsn, claude)
    with psycopg.connect(runtime_dsn) as conn:
        _event(conn, work, task, claude, "call.dispatched", 1, payload={"destination": "claude"})
        _event(conn, work, task, claude, "model.reported", 2, reported="claude-test")
        _event(conn, work, task, qwen, "call.dispatched", 3, payload={"destination": "qwen"})
    row, _ = _row(runtime_dsn, work)
    assert (row["efficiency_model"], row["efficiency_effort"]) == ("unknown", "unattributed")


def _executions(dsn, executions):
    """One call, call-level dispatch without execution ID, then per-execution events."""
    _clean(dsn)
    claude, _, _ = _seed_chain(dsn, recipient_call_id="gpt")
    work, task = _work(dsn, claude)
    with psycopg.connect(dsn) as conn:
        _event(conn, work, task, claude, "call.dispatched", 1, payload={"destination": "claude"})
        n = 2
        for execution, kinds in executions:
            for kind, reported in kinds:
                _event(conn, work, task, claude, kind, n, reported=reported, execution=execution,
                       requested="claude-test" if kind == "model.selected" else None)
                n += 1
    row, _ = _row(dsn, work)
    return row


def test_second_execution_without_model_report_is_unknown_not_masked_by_call(runtime_dsn):
    a, b = "x-" + uuid.uuid4().hex, "x-" + uuid.uuid4().hex
    row = _executions(runtime_dsn, [
        (a, [("model.selected", None), ("model.reported", "claude-test")]),
        (b, [("model.selected", None), ("usage.reported", None)])])  # selected/usage only, never reported
    assert row["reported_models"] == ["claude-test"]
    assert (row["efficiency_model"], row["efficiency_effort"]) == ("unknown", "unattributed")


def test_two_executions_same_reported_model_form_single_bucket(runtime_dsn):
    a, b = "x-" + uuid.uuid4().hex, "x-" + uuid.uuid4().hex
    row = _executions(runtime_dsn, [
        (a, [("model.selected", None), ("model.reported", "claude-test")]),
        (b, [("model.selected", None), ("model.reported", "claude-test")])])
    assert (row["efficiency_model"], row["efficiency_effort"]) == ("claude-test", "unknown")


def test_repeated_observations_of_one_execution_keep_known_model(runtime_dsn):
    a = "x-" + uuid.uuid4().hex
    row = _executions(runtime_dsn, [
        (a, [("model.selected", None), ("model.reported", "claude-test"), ("usage.reported", None),
             ("model.reported", "claude-test"), ("usage.reported", None)])])
    assert (row["efficiency_model"], row["efficiency_effort"]) == ("claude-test", "unknown")


def test_mixed_models_across_executions_remain_mixed(runtime_dsn):
    a, b = "x-" + uuid.uuid4().hex, "x-" + uuid.uuid4().hex
    row = _executions(runtime_dsn, [
        (a, [("model.reported", "claude-test")]), (b, [("model.reported", "claude-other")])])
    assert (row["efficiency_model"], row["efficiency_effort"]) == ("mixed", "unattributed")


def _delivered(dsn):
    claude, _, bindings = _seed_chain(dsn, recipient_call_id="gpt")
    work, task = _work(dsn, claude)
    _dispatch(dsn, work, task, claude)
    return claude, work, _paid_result(dsn, claude, bindings[claude])


def test_delivered_without_acknowledgement_is_pending(runtime_dsn):
    _clean(runtime_dsn)
    _, work, _ = _delivered(runtime_dsn)
    row, _ = _row(runtime_dsn, work)
    assert row["parent_outcome"] == "pending"


def test_accepted_ack_counts_once_and_replay_does_not_double_count(runtime_dsn):
    _clean(runtime_dsn)
    claude, work, paid = _delivered(runtime_dsn)
    ack_id = str(uuid.uuid4())
    _ack(runtime_dsn, claude, paid, acknowledgement_id=ack_id)
    _ack(runtime_dsn, claude, paid, acknowledgement_id=ack_id, producer_id="retry-producer")
    row, report = _row(runtime_dsn, work)
    assert row["parent_outcome"] == "accepted" and row["parent_accepted"] is True
    group = next(g for g in report["efficiency"]["groups"] if g["outcomes"]["accepted"])
    assert group["outcomes"]["accepted"] == 1


def test_superseding_revision_replaces_accepted_and_counts_once(runtime_dsn):
    _clean(runtime_dsn)
    claude, work, paid = _delivered(runtime_dsn)
    first = str(uuid.uuid4())
    _ack(runtime_dsn, claude, paid, acknowledgement_id=first)
    _ack(runtime_dsn, claude, paid, judgment="revised", answer="GPT corrected 518",
         supersedes_id=first)
    row, report = _row(runtime_dsn, work)
    assert row["parent_outcome"] == "revised" and row["rework"] is True
    assert sum(g["outcomes"]["revised"] for g in report["efficiency"]["groups"]) >= 1
    assert row["parent_accepted"] is False
