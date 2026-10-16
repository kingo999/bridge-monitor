import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.types.json import Jsonb

from bridge.guidance.registry import register_pack
from bridge.guidance.selection import resolve_guidance
from service.ingest.example_reviews import record_example_review
from tests.integration.test_admission import _call
from tests.integration.test_dispositions import _local_result, _seed_chain
from tests.integration.test_guidance import _guided_policy
from tests.integration.test_metrics import seeded
from contracts.queries import CohortQuery
from contracts.events import EventEnvelope, EventType
from service.queries.history import list_work_items, read_work_item_detail
from service.queries.metrics import query_metrics
from service.ingest.store import ingest_batch


def _source(dsn):
    owner, child, bindings = _seed_chain(dsn)
    result = _local_result(dsn, child, bindings[child])
    with psycopg.connect(dsn) as conn:
        work = conn.execute("SELECT work_item_id FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone()[0]
    return work, result.answer.result_id


def _example_pack(dsn, work, result):
    return register_pack(dsn, pack_id=f"examples-{uuid.uuid4()}", version_label="v1",
        items=[{"item_id": "example-1", "kind": "example", "body": b"A reviewed answer",
                "required": False, "source_work_item_id": work, "source_result_id": result}],
        actor_id="reviewer-1", auth_token="review-secret")


def _dispatch_event(conn, work, call_id, *, when=None):
    stamp = when or conn.execute("SELECT now()").fetchone()[0]
    conn.execute("""INSERT INTO events(event_id,event_type,schema_version,producer_id,
        producer_incarnation,producer_sequence,occurred_at,received_at,origin,
        observation_layer,capture_completeness,work_item_id,call_id,payload,payload_hash)
        VALUES(%s,'call.dispatched',1,%s,'example-test',1,%s,%s,'synthetic',
        'test','complete',%s,%s,%s,'test-hash')""",
        (str(uuid.uuid4()), f"example-{uuid.uuid4()}", stamp, stamp, work, call_id,
         Jsonb({"destination": "skynet"})))


def test_candidate_is_excluded_until_separate_approved_prompt_review(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "review-secret")
    work, result = _source(dsn)
    pack = _example_pack(dsn, work, result)
    candidate = record_example_review(dsn, pack.pack_version_id, "example-1",
        reviewer_id="reviewer-1", auth_token="review-secret", status="candidate",
        permitted_use="prompt_example", exposure="unknown", scope=["read"], evidence={})
    policy = _guided_policy(pack.pack_version_id, pack.sha256)
    task = _call(f"root-{uuid.uuid4()}", f"call-{uuid.uuid4()}").task
    before = resolve_guidance(dsn, policy, task, "Check this")
    assert before.manifest["excluded"][0]["reason"] == "example_not_approved_for_prompt"
    approved = record_example_review(dsn, pack.pack_version_id, "example-1",
        reviewer_id="reviewer-2", auth_token="review-secret", status="approved",
        permitted_use="prompt_example", exposure="unknown", scope=["read"],
        evidence={"validation": {"check": "manual", "outcome": "pass"}},
        supersedes_id=candidate.review_id)
    after = resolve_guidance(dsn, policy, task, "Check this")
    assert after.manifest["supplied"][0]["review_id"] == approved.review_id
    assert "A reviewed answer" in after.effective_prompt
    policy.guidance.packs[0].scopes = ["read", "write"]
    write_task = task.model_copy(update={"scope": ["write"]})
    scoped = resolve_guidance(dsn, policy, write_task, "Check this")
    assert scoped.manifest["excluded"][0]["reason"] == "example_scope_mismatch"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM example_reviews WHERE example_id='example-1'").fetchone()[0] == 2


def test_exposed_example_cannot_be_labelled_unseen_evaluation(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "review-secret")
    work, result = _source(dsn)
    pack = _example_pack(dsn, work, result)
    with psycopg.connect(dsn) as conn:
        call_id = conn.execute("SELECT call_id FROM results WHERE result_id=%s", (result,)).fetchone()[0]
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,route,eligibility,"
            "child_call_id,guidance_manifest,gate_facts,decided_at) "
            "VALUES(%s,%s,'local','eligible',%s,%s,'{}',now())",
            (str(uuid.uuid4()), work, call_id, Jsonb({"state": "supplied", "pack_version_id": pack.pack_version_id,
                "supplied": [{"item_id": "example-1", "source_result_id": result}]})))
        _dispatch_event(conn, work, call_id, when=conn.execute("SELECT now() + interval '1 second'").fetchone()[0])
    with pytest.raises(ValueError, match="exposed"):
        record_example_review(dsn, pack.pack_version_id, "example-1",
            reviewer_id="reviewer-1", auth_token="review-secret", status="approved",
            permitted_use="evaluation_only", exposure="unseen", scope=["read"],
            evidence={"validation": {"check": "manual", "outcome": "pass"}})
    review = record_example_review(dsn, pack.pack_version_id, "example-1",
        reviewer_id="reviewer-1", auth_token="review-secret", status="approved",
        permitted_use="evaluation_only", exposure="exposed", scope=["read"],
        evidence={"validation": {"check": "manual", "outcome": "pass"}})
    assert review.exposure == "exposed"
    policy = _guided_policy(pack.pack_version_id, pack.sha256)
    task = _call(f"root-{uuid.uuid4()}", f"call-{uuid.uuid4()}").task
    assert resolve_guidance(dsn, policy, task, "Check this").manifest["supplied"] == []


def test_latest_review_requires_explicit_supersession(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "review-secret")
    work, result = _source(dsn)
    pack = _example_pack(dsn, work, result)
    first = record_example_review(dsn, pack.pack_version_id, "example-1",
        reviewer_id="reviewer-1", auth_token="review-secret", status="candidate",
        permitted_use="prompt_example", exposure="unknown", scope=["read"], evidence={})
    with pytest.raises(ValueError, match="supersede"):
        record_example_review(dsn, pack.pack_version_id, "example-1",
            reviewer_id="reviewer-2", auth_token="review-secret", status="rejected",
            permitted_use="prompt_example", exposure="unknown", scope=["read"], evidence={})
    assert first.review_id
    with pytest.raises(ValueError, match="validation"):
        record_example_review(dsn, pack.pack_version_id, "example-1",
            reviewer_id="reviewer-2", auth_token="review-secret", status="approved",
            permitted_use="prompt_example", exposure="unknown", scope=["read"],
            evidence={"validation": "self-asserted"}, supersedes_id=first.review_id)


def test_actual_retry_manifests_group_one_work_item_as_mixed(seeded):
    dsn = seeded
    with psycopg.connect(dsn) as conn:
        call_id, dispatched_at = conn.execute("SELECT call_id,occurred_at FROM events "
            "WHERE work_item_id='demo-work-061' AND event_type='call.dispatched'").fetchone()
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,"
            "route,eligibility,guidance_manifest,decided_at) "
            "VALUES(%s,'demo-work-061',%s,'local','eligible',%s,%s)",
            (str(uuid.uuid4()), call_id, Jsonb({"state": "supplied", "pack_version_id": "pack-A"}),
             dispatched_at - timedelta(milliseconds=1)))
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,"
            "route,eligibility,guidance_manifest,decided_at) "
            "VALUES(%s,'demo-work-061',%s,'local','eligible',%s,%s)",
            (str(uuid.uuid4()), call_id, Jsonb({"state": "supplied", "pack_version_id": "pack-B"}),
             dispatched_at + timedelta(seconds=1)))
        conn.execute("""INSERT INTO events(event_id,event_type,schema_version,producer_id,
            producer_incarnation,producer_sequence,occurred_at,received_at,origin,
            observation_layer,capture_completeness,task_id,work_item_id,call_id,payload,payload_hash)
            SELECT %s,event_type,schema_version,%s,'retry',1,%s,%s,origin,observation_layer,
            capture_completeness,task_id,work_item_id,call_id,payload,payload_hash FROM events
            WHERE work_item_id='demo-work-061' AND event_type='call.dispatched'""",
            (str(uuid.uuid4()), f"retry-{uuid.uuid4()}", dispatched_at + timedelta(seconds=2),
             dispatched_at + timedelta(seconds=2)))
    mixed = CohortQuery(origin="synthetic", guidance_group="mixed")
    report = query_metrics(dsn, mixed)
    assert report.metrics["routed"].numerator == report.metrics["routed"].denominator == 1
    assert report.guidance_groups == {"mixed": 1}
    page = list_work_items(dsn, mixed)
    assert [row["work_item_id"] for row in page] == ["demo-work-061"]
    assert page[0]["guidance_group"] == "mixed"
    first_pack = query_metrics(dsn, CohortQuery(origin="synthetic", guidance_group="pack-A"))
    assert first_pack.metrics["routed"].denominator == 0
    assert first_pack.metrics["confirmed_acceptance"].denominator == 1
    second_pack = query_metrics(dsn, CohortQuery(origin="synthetic", guidance_group="pack-B"))
    assert second_pack.metrics["confirmed_acceptance"].denominator == 0


def test_repair_filters_use_recorded_effective_assessment(seeded):
    dsn = seeded
    event = EventEnvelope(event_id=f"repair-{uuid.uuid4()}",
        event_type=EventType.DISPOSITION_RECORDED, schema_version=1,
        producer_id=f"repair-{uuid.uuid4()}", producer_incarnation="test",
        producer_sequence=1, occurred_at=datetime.now(timezone.utc), origin="synthetic",
        observation_layer="human", capture_completeness="complete",
        task_id="demo-task-001", work_item_id="demo-work-061", call_id="demo-call-061",
        payload={"disposition_id": f"repair-{uuid.uuid4()}", "result_id": "demo-result-061",
                 "actor_id": "claude-seed", "stage": "claude", "judgment": "revised",
                 "supersedes_id": "disp-demo-work-061", "repair_effort": "minor",
                 "failure_category": "incorrect_behavior", "guidance_ids": [],
                 "correction_execution_ids": ["claude-fix-1"]})
    ingest_batch(dsn, event.producer_id, [event], [])
    query = CohortQuery(origin="synthetic", failure_category="incorrect_behavior",
                        repair_effort="minor")
    assert query_metrics(dsn, query).metrics["routed"].denominator == 1
    page = list_work_items(dsn, query)
    assert [row["work_item_id"] for row in page] == ["demo-work-061"]
    assert page[0]["repair_efforts"] == ["minor"]


def test_unrelated_same_root_is_not_a_variant_but_linked_result_is(seeded, monkeypatch):
    dsn = seeded
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "review-secret")
    pack = _example_pack(dsn, "demo-work-061", "demo-result-061")
    with psycopg.connect(dsn) as conn:
        dispatched_at = conn.execute("SELECT occurred_at FROM events WHERE "
            "work_item_id='demo-work-062' AND event_type='call.dispatched'").fetchone()[0]
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,"
            "route,eligibility,guidance_manifest,decided_at) "
            "VALUES(%s,'demo-work-062','demo-call-062','local','eligible',%s,%s)",
            (str(uuid.uuid4()), Jsonb({"state": "supplied", "pack_version_id": "other-pack",
              "supplied": [{"item_id": "different-example", "source_work_item_id": "demo-work-062",
                            "source_result_id": "demo-result-062"}]}),
             dispatched_at - timedelta(milliseconds=1)))
    candidate = record_example_review(dsn, pack.pack_version_id, "example-1",
        reviewer_id="human-1", auth_token="review-secret", status="candidate",
        permitted_use="evaluation_only", exposure="unseen", scope=["read"], evidence={})
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO result_links(from_result_id,to_result_id,relation) "
                     "VALUES('demo-result-061','demo-result-062','correction')")
    with pytest.raises(ValueError, match="exposed"):
        record_example_review(dsn, pack.pack_version_id, "example-1",
            reviewer_id="human-1", auth_token="review-secret", status="approved",
            permitted_use="evaluation_only", exposure="unseen", scope=["read"],
            evidence={"validation": {"check": "manual", "outcome": "pass"}},
            supersedes_id=candidate.review_id)
    approved = record_example_review(dsn, pack.pack_version_id, "example-1",
        reviewer_id="human-1", auth_token="review-secret", status="approved",
        permitted_use="evaluation_only", exposure="exposed", scope=["read"],
        evidence={"validation": {"check": "manual", "outcome": "pass"}},
        supersedes_id=candidate.review_id)
    assert approved.exposure == "exposed"


def test_reuse_filter_uses_latest_human_review_without_rewriting_dispatch(seeded, monkeypatch):
    dsn = seeded
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "review-secret")
    pack = _example_pack(dsn, "demo-work-062", "demo-result-062")
    approved = record_example_review(dsn, pack.pack_version_id, "example-1",
        reviewer_id="human-1", auth_token="review-secret", status="approved",
        permitted_use="prompt_example", exposure="unknown", scope=["read"],
        evidence={"validation": {"check": "manual", "outcome": "pass"}})
    with psycopg.connect(dsn) as conn:
        dispatched_at = conn.execute("SELECT occurred_at FROM events WHERE "
            "work_item_id='demo-work-061' AND event_type='call.dispatched'").fetchone()[0]
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,"
            "route,eligibility,guidance_manifest,decided_at) "
            "VALUES(%s,'demo-work-061','demo-call-061','local','eligible',%s,%s)",
            (str(uuid.uuid4()), Jsonb({"state": "supplied", "pack_version_id": pack.pack_version_id,
              "supplied": [{"item_id": "example-1", "kind": "example", "review_id": approved.review_id}]}),
             dispatched_at - timedelta(milliseconds=1)))
    selected = CohortQuery(origin="synthetic", example_reuse_status="approved")
    assert query_metrics(dsn, selected).metrics["routed"].denominator == 1
    assert [item["work_item_id"] for item in list_work_items(dsn, selected)] == ["demo-work-061"]
    record_example_review(dsn, pack.pack_version_id, "example-1",
        reviewer_id="human-2", auth_token="review-secret", status="rejected",
        permitted_use="prompt_example", exposure="unknown", scope=["read"],
        evidence={"reason": "superseded"}, supersedes_id=approved.review_id)
    assert query_metrics(dsn, selected).metrics["routed"].denominator == 0
    rejected = CohortQuery(origin="synthetic", example_reuse_status="rejected")
    assert query_metrics(dsn, rejected).metrics["routed"].denominator == 1


def test_inspector_does_not_label_post_dispatch_selection_as_supplied(seeded):
    dsn = seeded
    decision_id = str(uuid.uuid4())
    with psycopg.connect(dsn) as conn:
        dispatched_at = conn.execute("SELECT occurred_at FROM events WHERE "
            "work_item_id='demo-work-061' AND event_type='call.dispatched'").fetchone()[0]
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,"
            "route,eligibility,guidance_manifest,decided_at) "
            "VALUES(%s,'demo-work-061','demo-call-061','local','eligible',%s,%s)",
            (decision_id, Jsonb({"state": "supplied", "pack_version_id": "late-pack",
                "supplied": [{"item_id": "late-rule", "kind": "rule"}]}),
             dispatched_at + timedelta(seconds=1)))
    detail = read_work_item_detail(dsn, "demo-work-061")
    assert decision_id not in detail["supplied_decision_ids"]


def test_registered_example_result_must_belong_to_declared_work_item(seeded, monkeypatch):
    dsn = seeded
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "review-secret")
    with pytest.raises(ValueError, match="source result must belong"):
        _example_pack(dsn, "demo-work-061", "demo-result-062")
