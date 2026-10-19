"""Malformed historical metadata must not poison an entire reconciliation page."""

import pytest

from bridge.classifier.hook_capture import prepare_capture
from tests.classifier.test_hook_capture import NOW, settings, snapshot


@pytest.mark.parametrize('gate_facts', [None, [], 'not an object', 42])
def test_non_object_decision_facts_are_a_mismatch_not_a_page_failure(tmp_path, gate_facts):
    from bridge.classifier.route_links import validate_link
    cap = prepare_capture(snapshot(), settings(tmp_path))
    b = cap.facts_provenance['route_work_binding']
    row = dict(task_id=b['task_id'],work_item_id=b['work_item_id'],
        request_sha256=b['task_sha256'],source_sha256=b['baseline_sha256'],facts_provenance=cap.facts_provenance)
    decision = dict(decision_id=b['decision_id'],child_call_id=b['paid_call_id'],
        work_item_id=b['work_item_id'],call_work_item_id=b['work_item_id'],
        task_id=b['task_id'],call_task_id=b['task_id'],decision_stage='parent_router',
        execution_mode='production',route='paid',gate_facts=gate_facts,decided_at=NOW)
    assert validate_link(row,decision)['actual_route_source']['status'] == 'binding_mismatch'


def test_pre_database_hash_error_is_known_not_committed(tmp_path, monkeypatch):
    from bridge.classifier import store, hook_store
    cfg=settings(tmp_path)
    cap=prepare_capture(snapshot(),cfg)
    monkeypatch.setattr(store,'source_key',lambda *a: (_ for _ in ()).throw(ValueError('bad hash input')))
    monkeypatch.setattr(hook_store.psycopg,'connect',lambda *a,**kw: pytest.fail('no database attempt expected'))
    with pytest.raises(hook_store.CaptureDatabaseError):
        hook_store.capture_hook('unused',cap,cfg.shadow_config(),captured_at=NOW,now=NOW)
