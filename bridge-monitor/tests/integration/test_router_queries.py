"""Router audit reports attempts, evidence coverage, and unknown cost honestly."""

import psycopg
import uuid
from psycopg.types.json import Jsonb
from fastapi.testclient import TestClient

from bridge.router.shadow_worker import run_one_shadow
from contracts.events import EventType
from service.queries.router import (RouterCohortFilter, query_shadow_cohort,
                                    read_router_audit)
from service.http.app import create_app
from tests.router.test_ingress import router_env
from tests.router.test_shadow_worker import Qwen, Verifier, _worker


def test_shadow_report_counts_every_parent_attempt_without_billed_savings(router_env, tmp_path):
    parent, paid, task, case, receipt = router_env
    routed = parent.route_work(task, case.task_text,
        requested_model="claude-test", reserve_tokens=100,
        output_contract="complete_python_module_v1", verifier_ref=receipt.verifier_ref)
    report = query_shadow_cohort(parent.dsn, RouterCohortFilter(
        origin="observed", work_item_id=task.work_item_id))
    assert report["attempted_tasks"] == 1
    assert report["queued_shadow"] == 1
    assert report["independent_evaluated"] == 0
    assert report["billed_savings"] is None
    assert report["conditional_avoided_paid_calls"] is None
    assert case.task_text not in str(report)
    assert case.baseline_source not in str(report)
    outcome = run_one_shadow(_worker(parent, tmp_path, Qwen()), Verifier())
    assert outcome["evaluation_state"] == "pass"
    report = query_shadow_cohort(parent.dsn, RouterCohortFilter(
        origin="observed", work_item_id=task.work_item_id))
    assert report["attempted_tasks"] == 1
    assert report["independent_evaluated"] == 1
    assert report["outcomes"]["pass"] == 1
    assert report["unknown_usage"]["qwen"] == 1
    audit = read_router_audit(parent.dsn, task.work_item_id)
    assert audit["shadow_call_id"] == routed["shadow_call_id"]
    assert audit["evaluation"]["state"] == "pass"
    assert audit["shadow_result"]["delivered_at"] is None
    assert audit["paid_result"]["delivered_at"] is not None
    assert audit["prompt_refs"]["original"] and audit["prompt_refs"]["shadow_effective"]
    assert audit["result_refs"]["shadow_raw"] and audit["result_refs"]["shadow_parsed"]
    assert parent.emitter.emit(EventType.DELEGATION_DECIDED,
        task_id=task.task_id, work_item_id=task.work_item_id,
        config_version=parent.policy_version,
        payload={"decision_id": str(uuid.uuid4()), "decision_stage": "claude_child",
                 "route": "paid", "eligibility": "unknown",
                 "reason": "worker_chose_paid_direct", "guidance_manifest": {},
                 "gate_facts": {}})
    audited = read_router_audit(parent.dsn, task.work_item_id)
    assert "claude_child" in [item["decision_stage"] for item in audited["decisions"]]
    assert case.task_text not in str(audit)
    assert case.baseline_source not in str(audit)


def test_paid_direct_attempt_is_in_denominator_with_missing_verifier_reason(router_env):
    parent, _, task, case, _ = router_env
    parent.route_work(task, case.task_text, requested_model="claude-test",
                      reserve_tokens=100, output_contract="complete_python_module_v1")
    report = query_shadow_cohort(parent.dsn, RouterCohortFilter(
        origin="observed", work_item_id=task.work_item_id))
    assert report["attempted_tasks"] == 1
    assert report["queued_shadow"] == 0
    assert report["reasons"]["verifier_missing"] == 1
    audit = read_router_audit(parent.dsn, task.work_item_id)
    assert audit["shadow_call_id"] is None
    assert audit["parent_decision"]["reason"] == "verifier_missing"


def test_failed_candidate_remains_audited_but_outside_potential_savings(router_env, tmp_path):
    parent, _, task, case, receipt = router_env
    parent.route_work(task, case.task_text, requested_model="claude-test",
        reserve_tokens=100, output_contract="complete_python_module_v1",
        verifier_ref=receipt.verifier_ref)
    run_one_shadow(_worker(parent, tmp_path, Qwen(answer='{"answer":"def square(n):\\n    return n + 1\\n"}')),
                   Verifier(state="fail", reason="tests_failed"))
    report = query_shadow_cohort(parent.dsn, RouterCohortFilter(
        origin="observed", work_item_id=task.work_item_id))
    assert report["outcomes"]["fail"] == 1
    assert report["reasons"]["tests_failed"] == 1
    assert report["conditional_avoided_paid_calls"] is None


def test_router_http_requires_operator_and_keeps_bodies_on_content_endpoint(router_env):
    parent, _, task, case, receipt = router_env
    parent.route_work(task, case.task_text, requested_model="claude-test",
        reserve_tokens=100, output_contract="complete_python_module_v1",
        verifier_ref=receipt.verifier_ref)
    client = TestClient(create_app(parent.dsn, producer_tokens={"seed": "producer-secret"},
                                   operator_token="operator-secret", payload_limit_bytes=1048576))
    audit_path = f"/api/v1/router/audit/{task.work_item_id}"
    assert client.get(audit_path).status_code == 401
    assert client.get("/api/v1/router/cohort").status_code == 401
    headers = {"Authorization": "Bearer operator-secret"}
    audit = client.get(audit_path, headers=headers)
    assert audit.status_code == 200
    assert case.task_text not in audit.text
    assert case.baseline_source not in audit.text
    assert client.get("/api/v1/router/cohort?work_item_id=" + task.work_item_id,
                      headers=headers).json()["attempted_tasks"] == 1
    assert client.get("/api/v1/router/cohort?origin=invalid", headers=headers).status_code == 422


def test_latest_evaluation_is_effective_and_prior_evidence_stays_visible(router_env, tmp_path):
    parent, _, task, case, receipt = router_env
    parent.route_work(task, case.task_text, requested_model="claude-test",
        reserve_tokens=100, output_contract="complete_python_module_v1",
        verifier_ref=receipt.verifier_ref)
    first = run_one_shadow(_worker(parent, tmp_path, Qwen()), Verifier())
    with psycopg.connect(parent.dsn) as conn:
        conn.execute("INSERT INTO evaluations(evaluation_id,result_id,evidence,recorded_at) "
            "VALUES(%s,%s,%s,now() + interval '1 second')",
            (str(uuid.uuid4()), first["result_id"], Jsonb({"state": "uncertain",
                "reason": "manual_evidence_disputed", "source": "operator_correction"})))
    audit = read_router_audit(parent.dsn, task.work_item_id)
    assert audit["evaluation"]["reason"] == "manual_evidence_disputed"
    assert len(audit["evaluation_history"]) == 2
    assert audit["evaluation_history"][0]["evidence"]["reason"] == "tests_passed"
    report = query_shadow_cohort(parent.dsn, RouterCohortFilter(
        origin="observed", work_item_id=task.work_item_id))
    assert report["outcomes"]["uncertain"] == 1
    assert report["outcomes"].get("pass", 0) == 0
