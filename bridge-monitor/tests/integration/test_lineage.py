import psycopg

from contracts.outcomes import CompletionEnvelope
from bridge.runtime.completions import record_completion
from service.ingest.lineage import final_survival
from tests.integration.test_dispositions import _seed_chain, _local_result


def test_claude_acceptance_does_not_prove_gpt_final_survival(runtime_dsn):
    dsn = runtime_dsn
    claude, qwen, bindings = _seed_chain(dsn)
    local = _local_result(dsn, qwen, bindings[qwen])
    envelope = CompletionEnvelope.model_validate({"call_id": claude,
        "attempt_id": bindings[claude].attempt_id,
        "result": {"result_id": f"result-{claude}", "content_id": f"content-{claude}", "delivered": True},
        "dispositions": [{"disposition_id": f"disp-{claude}", "result_id": local.answer.result_id,
                          "actor_id": bindings[claude].worker_instance_id, "stage": "claude",
                          "judgment": "accepted", "input_sha256": local.answer.sha256,
                          "output_result_id": f"result-{claude}"}],
        "contributions": [{"from_result_id": local.answer.result_id,
                           "to_result_id": f"result-{claude}", "relation": "accepted_contribution"}]})
    assert record_completion(dsn, envelope, bindings[claude], answer_bytes=b"paid answer").accounting_complete
    with psycopg.connect(dsn) as conn:
        assert final_survival(conn, local.answer.result_id) == "unknown"


def test_cyclic_lineage_is_rejected_without_losing_answer(runtime_dsn):
    dsn = runtime_dsn
    claude, qwen, bindings = _seed_chain(dsn)
    local = _local_result(dsn, qwen, bindings[qwen])
    envelope = CompletionEnvelope.model_validate({"call_id": claude,
        "attempt_id": bindings[claude].attempt_id,
        "result": {"result_id": f"result-{claude}", "content_id": f"content-{claude}", "delivered": True},
        "contributions": [{"from_result_id": local.answer.result_id,
                           "to_result_id": f"result-{claude}", "relation": "accepted_contribution"},
                          {"from_result_id": f"result-{claude}",
                           "to_result_id": local.answer.result_id, "relation": "synthesis"}]})
    receipt = record_completion(dsn, envelope, bindings[claude], answer_bytes=b"answer")
    assert receipt.answer.delivered and not receipt.accounting_complete
    assert any("cyclic" in item for item in receipt.diagnostics)
