import hashlib
import json
from types import SimpleNamespace

import psycopg
import pytest

from bridge.mcp_server import BridgeCoordinator
from bridge.runtime.completions import record_completion
from tests.integration.test_dispositions import _seed_chain, _local_result


@pytest.mark.parametrize('effort,judgment,accounted', [
    ('minor', 'revised', True),
    (None, 'revised', True),
    ('low', 'revised', False),
    ('minor', 'accepted', False),
])
def test_repair_effort_is_strict_without_losing_the_delivered_answer(runtime_dsn,
                                                                  effort, judgment, accounted):
    dsn = runtime_dsn
    claude, qwen, bindings = _seed_chain(dsn)
    local = _local_result(dsn, qwen, bindings[qwen]).answer
    # Exercise the same claim-to-envelope path as the Claude adapter's final reply.
    service = SimpleNamespace(dsn=dsn, workers={'claude': bindings[claude].worker_instance_id})
    item = dict(result_id=local.result_id, input_sha256=local.sha256,
                judgment=judgment, reason='Checked and corrected a small error')
    if effort is not None:
        item['repair_effort'] = effort
    claim = dict(answer='Corrected answer', local_results=[item], work_resolution='resolved')
    raw = json.dumps(claim).encode()
    envelope, diagnostics = BridgeCoordinator._claimed_completion(service,
        SimpleNamespace(call_id=claude), bindings[claude].attempt_id,
        f'paid-result-{claude}', f'paid-content-{claude}', claim,
        raw_content_id=f'paid-raw-{claude}')
    receipt = record_completion(dsn, envelope, bindings[claude],
        answer_bytes=b'Corrected answer', raw_answer_bytes=raw, claim_diagnostics=diagnostics)
    assert receipt.answer.delivered and receipt.accounting_complete is accounted
    assert receipt.answer.sha256 == hashlib.sha256(b'Corrected answer').hexdigest()
    with psycopg.connect(dsn) as conn:
        row = conn.execute('SELECT raw.raw_bytes,delivered.raw_bytes FROM results r '
            'JOIN content_objects raw ON raw.content_id=r.raw_content_id '
            'JOIN content_objects delivered ON delivered.content_id=r.delivered_content_id '
            'WHERE r.result_id=%s', (receipt.answer.result_id,)).fetchone()
        assert row == (raw, b'Corrected answer')
        dispositions = conn.execute("SELECT details->>'repair_effort' FROM dispositions WHERE result_id=%s",
                                    (local.result_id,)).fetchall()
        assert dispositions == ([(effort,)] if accounted else [])
        assert conn.execute('SELECT count(*) FROM results WHERE call_id=%s', (claude,)).fetchone()[0] == 1
    if not accounted:
        assert any('repair_effort' in reason or 'repair effort' in reason for reason in receipt.diagnostics)
