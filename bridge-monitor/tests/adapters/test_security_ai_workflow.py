import json
import os
import sys
from pathlib import Path

import pytest

from bridge.repository import RepositoryPolicy, RepositoryTools


CAMPAIGN = '2026-10-29-installation-test'


def make_tools(tmp_path, **extra):
    root = tmp_path / 'repo'
    root.mkdir(exist_ok=True)
    return RepositoryTools(RepositoryPolicy(enabled=True, access='read-write', roots={'test': str(root)},
        security_ai_campaigns={'test': [CAMPAIGN]}, **extra), lambda: True, tmp_path/'logs')


def test_workflow_is_opt_in_and_never_opens_raw_pt_access(tmp_path):
    root = tmp_path/'repo'
    root.mkdir()
    tools = RepositoryTools(RepositoryPolicy(enabled=True, roots={'test': str(root)}), lambda: True, tmp_path/'logs')
    with pytest.raises(PermissionError):
        tools.run_framework('test', CAMPAIGN, 'state')
    with pytest.raises(PermissionError):
        make_tools(tmp_path).read_file('test', 'pt/report.ptrac.json')


@pytest.mark.parametrize('action', ['approve', 'fix', 'commit', 'push', 'shell', 'receive'])
def test_workflow_rejects_execution_authority_and_generic_actions(tmp_path, action):
    with pytest.raises((PermissionError, ValueError)):
        make_tools(tmp_path).run_framework('test', CAMPAIGN, action)


@pytest.mark.parametrize('campaign', ['../escape', '/absolute', 'x/../../y', 'other-campaign', 'x:stream'])
def test_exact_campaign_binding_is_required(tmp_path, campaign):
    with pytest.raises((PermissionError, ValueError)):
        make_tools(tmp_path).run_framework('test', campaign, 'state')


def test_workflow_requires_live_lease_before_any_request_is_written(tmp_path):
    tools = make_tools(tmp_path)
    tools.authorize = lambda: False
    with pytest.raises(PermissionError):
        tools.run_framework('test', CAMPAIGN, 'state')
    assert not (tmp_path/'logs').exists()


@pytest.mark.parametrize('payload', [{'command': 'arbitrary'}, {'path': '../secret'}, {'approval': True}])
def test_read_actions_reject_extra_payload(tmp_path, payload):
    with pytest.raises(ValueError):
        make_tools(tmp_path).run_framework('test', CAMPAIGN, 'state', payload)


def test_assessment_cannot_change_approval_or_invent_runtime_evidence(tmp_path):
    tools = make_tools(tmp_path)
    payload = {'expected_record_sha256': 'a'*64, 'findings': [], 'items': [], 'authorization': {'mode': 'auto'}}
    with pytest.raises(ValueError):
        tools.run_framework('test', CAMPAIGN, 'assess', payload)
    payload.pop('authorization')
    payload['findings'] = [{'pt_id': 'PT-2026-09:BIN-082', 'runtime_tests_passed': True}]
    with pytest.raises(ValueError):
        tools.run_framework('test', CAMPAIGN, 'assess', payload)


def test_fixed_runner_uses_contained_execution_and_no_shell_payload(tmp_path, monkeypatch):
    tools = make_tools(tmp_path)
    seen = {}
    def run(repository, command):
        seen.update(repository=repository, command=command)
        request = json.loads(Path(command[command.index('--request')+1]).read_text())
        assert request == {'campaign': CAMPAIGN, 'action': 'state', 'payload': {}}
        return {'exit_code': 0, 'run_id': 'test'}
    monkeypatch.setattr(tools, '_run_command', run, raising=False)
    result = tools.run_framework('test', CAMPAIGN, 'state')
    assert result['exit_code'] == 0
    assert seen['repository'] == 'test'
    assert Path(seen['command'][2]).name == 'security_ai_workflow.py'
    assert seen['command'][1] == '-B'
    assert '--root' in seen['command']


def test_ungranted_repository_cannot_use_another_roots_campaign(tmp_path):
    with pytest.raises(PermissionError):
        make_tools(tmp_path).run_framework('ungranted', CAMPAIGN, 'state')


def test_native_publication_keeps_authority_cleared_and_rejects_stale_replay():
    """Explicit synthetic fixture only; never reads a real campaign or report."""
    source = os.environ.get('BRIDGE_SECURITY_AI_FIXTURE_ROOT')
    if not source:
        pytest.skip('Set explicit framework fixture root to run native writer integration')
    sys.path.insert(0, str(Path(source)/'security-ai/tools'))
    from test_minimized_handover import InventoryTransferFixture
    import campaign_execution as execution
    from bridge.security_ai_workflow import run
    fixture = InventoryTransferFixture()
    try:
        fixture.setUp()
        execution.adopt(fixture.campaign_dir,
            execution.adoption_preview(fixture.campaign_dir, root=fixture.sender), root=fixture.sender)
        campaign = fixture.campaign_dir.name
        def invoke(action, payload=None):
            return run(fixture.sender, {'campaign': campaign, 'action': action, 'payload': payload or {}})
        before = invoke('state')
        payload = {'expected_record_sha256': before['record_sha256'],
            'findings': [{'pt_id': row['pt_id'], 'classification': 'not-present', 'final_status': 'not-present',
                          'verification_pending': False, 'reason_codes': ['previously-remediated'],
                          'evidence_availability': 'digest-only'} for row in before['findings']],
            'items': [{'id': row['id'], 'readiness': 'not-present', 'reason_codes': ['previously-remediated']}
                      for row in before['planning']['items']]}
        assessed = invoke('assess', payload)
        assert assessed['authorization'] == {'mode': 'inventory'}
        assert assessed['planning']['approval'] == {'status': 'pending', 'approved_item_ids': []}
        assert all(not row['execution_eligible'] for row in assessed['planning']['items'])
        with pytest.raises(ValueError, match='changed'):
            invoke('assess', payload)
        planned = invoke('base-plan', {'expected_record_sha256': assessed['record_sha256'],
                                      'groups': assessed['planning']['groups']})
        assert planned['eligible_for_general_approval'] == []
        assert invoke('verify')['state'] == 'verified'
        assert invoke('state')['authorization'] == {'mode': 'inventory'}
    finally:
        fixture.doCleanups()
