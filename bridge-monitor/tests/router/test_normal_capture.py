"""Normal parent submissions are observed before dispatch without changing routing."""
import threading
import uuid
import time

import psycopg
import pytest

from tests.router.test_ingress import router_env
from tests.classifier.conftest import cdsn


def unit_capture(*, controlled=False, solution=False):
    from types import SimpleNamespace
    from datetime import datetime, timedelta, timezone
    from bridge.mcp_server import BridgeCoordinator
    now = datetime.now(timezone.utc)
    c = BridgeCoordinator.__new__(BridgeCoordinator)
    c.route_observer = Observer()
    c.policy = SimpleNamespace(router=SimpleNamespace(scopes=['analysis']))
    task = SimpleNamespace(task_id='t', work_item_id='w', category='single_file_python_fix',
        scope=['analysis'], context={'shadow_capture_origin':'controlled'} if controlled else {})
    case = SimpleNamespace(task_id='t', task_text='Fix square', baseline_source='def f(): return 1',
        origin_kind='natural', origin_ref='registered:natural', output_contract='complete_python_module_v1',
        captured_at=now-timedelta(seconds=5), tests=[{}],
        solution_available_at=now-timedelta(seconds=1) if solution else None)
    c._offer_route_capture(task,'Fix square',case,'a'*64,'complete_python_module_v1','call','decision','model')
    return c.route_observer.items


def test_known_solution_cannot_reenter_via_prompt_only_fallback():
    assert unit_capture(solution=True) == []


def test_controlled_marker_also_demotes_registered_natural_capture():
    assert unit_capture(controlled=True)[0].origin_kind == 'controlled'


def test_empty_verifier_reference_links_to_exact_empty_reference(tmp_path):
    from contracts.route_capture import BridgeCapture
    from bridge.classifier.hook_config import load_hook_settings
    from bridge.classifier.hook_capture import prepare_capture
    from bridge.classifier.route_links import validate_link
    from datetime import datetime, timezone
    p=tmp_path/'on.toml'
    p.write_text('[classifier_shadow]\nenabled=true\n[route_work_capture]\nenabled=true\nnormal_traffic_enabled=true\n')
    cfg=load_hook_settings(project_root=tmp_path,config_path=p)
    cap=prepare_capture(BridgeCapture('t','w','Explain','route_work','c','d','model',
        datetime.now(timezone.utc),verifier_ref='',output_contract='text'),cfg)
    b=cap.facts_provenance['route_work_binding']
    row=dict(task_id='t',work_item_id='w',request_sha256=b['task_sha256'],
        source_sha256=b['baseline_sha256'],facts_provenance=cap.facts_provenance)
    decision=dict(decision_id='d',child_call_id='c',work_item_id='w',call_work_item_id='w',
        task_id='t',call_task_id='t',requested_model='model',execution_mode='production',
        decision_stage='parent_router',gate_facts=dict(task_sha256=b['task_sha256'],
        verifier_ref='',output_contract='text'),route='paid',decided_at=datetime.now(timezone.utc))
    assert validate_link(row,decision)['actual_route']=='paid'


class Observer:
    normal_traffic_enabled = True
    def __init__(self): self.items = []
    def offer(self, item): self.items.append(item)


@pytest.mark.parametrize('method', ['route_work', 'delegate_claude'])
def test_normal_requests_are_captured_once_before_paid_execution(router_env, method):
    c, paid, task, case, receipt = router_env
    observer = Observer()
    c.route_observer = observer
    packet = task.model_copy(update={'category': 'operations'})
    original = paid.run
    def run(*a, **kw):
        assert len(observer.items) == 1
        return original(*a, **kw)
    paid.run = run
    kwargs = dict(requested_model='claude-test', reserve_tokens=100)
    if method == 'route_work': kwargs['output_contract'] = 'plain_text'
    response = getattr(c, method)(packet, 'Explain this operational issue.', **kwargs)
    assert response['state'] == 'completed' and paid.calls == 1
    assert len(observer.items) == 1
    item = observer.items[0]
    assert item.acceptable() and item.method == method
    assert item.origin_kind == 'natural' and item.prompt == 'Explain this operational issue.'
    assert item.paid_call_id == response['call_id']
    if method == 'route_work':
        getattr(c, method)(packet, 'Explain this operational issue.', **kwargs)
        assert paid.calls == 1 and len(observer.items) == 1


def test_generic_capture_remains_opt_in_and_offer_errors_do_not_change_response(router_env):
    c, paid, task, *_ = router_env
    observer = Observer()
    observer.normal_traffic_enabled = False
    c.route_observer = observer
    result = c.delegate_claude(task, 'Ordinary request', requested_model='claude-test', reserve_tokens=100)
    assert result['state'] == 'completed' and not observer.items
    observer.normal_traffic_enabled = True
    observer.offer = lambda item: (_ for _ in ()).throw(RuntimeError('sink failure'))
    packet = task.model_copy(update={'work_item_id': uuid.uuid4().hex})
    result = c.delegate_claude(packet, 'Another request', requested_model='claude-test', reserve_tokens=100)
    assert result['state'] == 'completed' and paid.calls == 2


def test_controlled_tag_cannot_invent_trusted_facts(router_env, tmp_path):
    from bridge.classifier.hook_capture import prepare_capture
    from bridge.classifier.hook_config import load_hook_settings
    c, _, task, *_ = router_env
    observer = Observer()
    c.route_observer = observer
    path = tmp_path/'generic.toml'
    path.write_text('[classifier_shadow]\nenabled=true\n[route_work_capture]\nenabled=true\nnormal_traffic_enabled=true\n')
    settings = load_hook_settings(project_root=tmp_path, config_path=path)
    packet = task.model_copy(update={'call_budget': 0, 'token_budget': 0,
        'context': {'shadow_capture_origin': 'controlled', 'code_available': 'true'}})
    c.delegate_claude(packet, 'A controlled text probe', requested_model='claude-test', reserve_tokens=0)
    cap = prepare_capture(observer.items[0], settings)
    assert cap.origin_kind == 'controlled' and cap.input.source == ''
    for key in ('context_complete', 'tests_present', 'scope', 'code_available'):
        assert cap.input.facts[key] is None
    assert cap.facts_provenance['capture_method'] == 'bridge_ingress'


@pytest.mark.parametrize('method', ['route_work', 'delegate_claude'])
def test_background_capture_classifies_and_links_exact_normal_call(router_env, cdsn, tmp_path, method, monkeypatch):
    from bridge.classifier.hook_config import load_hook_settings
    from bridge.classifier.observer import RouteCaptureObserver
    from bridge.classifier.runner import ShadowRunner
    from bridge.classifier import route_links
    from tests.classifier.fakes import FakeOllama
    from tests.classifier.test_route_links import row
    from service.queries.classifier import query_classifier_cohort, ClassifierCohortFilter
    c, paid, task, *_ = router_env
    path = tmp_path/'normal.toml'
    path.write_text('[classifier_shadow]\nenabled=true\n[route_work_capture]\nenabled=true\nnormal_traffic_enabled=true\n')
    settings = load_hook_settings(project_root=tmp_path, config_path=path)
    c.route_observer = RouteCaptureObserver(cdsn, settings)
    packet = task.model_copy(update={'category': 'operations',
        'context': {'shadow_capture_origin': 'controlled'}})
    kwargs = dict(requested_model='claude-test', reserve_tokens=100)
    if method == 'route_work': kwargs['output_contract'] = 'plain_text'
    try:
        response = getattr(c, method)(packet, 'Explain normal capture isolation.', **kwargs)
        deadline = time.monotonic()+3
        while c.route_observer.counters['captured'] != 1 and time.monotonic() < deadline:
            time.sleep(.02)
        assert c.route_observer.counters['captured'] == 1
    finally: c.close()
    with psycopg.connect(cdsn) as conn:
        obs = conn.execute('SELECT observation_id FROM classifier_observations').fetchone()[0]
    fake = FakeOllama()
    original = route_links.immediate_link
    monkeypatch.setattr(route_links, 'immediate_link', lambda *a: (_ for _ in ()).throw(psycopg.errors.QueryCanceled()))
    with ShadowRunner(cdsn, settings.shadow_config(), fake.classifier()) as runner:
        assert runner.process_one().state == 'completed'
    saved = row(cdsn, obs)
    assert saved['actual_route'] is None and saved['actual_route_source']['status'] == 'lookup_failed'
    assert saved['response_content_id'] and saved['attempts'] == 1
    monkeypatch.setattr(route_links, 'immediate_link', original)
    link = original(cdsn, saved)
    assert link['actual_route'] == 'paid'
    assert link['actual_route_source']['checked_identity']['paid_call_id'] == response['call_id']
    assert route_links.reconcile_page(cdsn)['linked'] == 1
    assert row(cdsn, obs)['actual_route'] == 'paid' and fake.chat_calls == 1
    method_filter = 'route_work' if method == 'route_work' else 'bridge_ingress'
    report = query_classifier_cohort(cdsn, ClassifierCohortFilter(capture_method=method_filter))
    assert report['counts']['completed'] == report['actual_route']['known'] == 1
    assert row(cdsn, obs)['origin_kind'] == 'controlled'
    # A binding to another requested model must never acquire this call's route.
    import copy
    from bridge.classifier.hook_capture import binding_hash
    wrong = copy.deepcopy(saved)
    b = wrong['facts_provenance']['route_work_binding']
    b['requested_model'] = 'another-model'
    b['sha256'] = binding_hash(b)
    assert 'actual_route' not in original(cdsn, wrong)
