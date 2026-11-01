"""Fixed self-test commands must never become a general command runner."""
import json
import sys
from pathlib import Path

import pytest

from bridge.repository import RepositoryPolicy, RepositoryTools


def tools(root, **profile):
    return RepositoryTools(RepositoryPolicy(enabled=True, access='read-write',
        roots={'bridge': str(root)}, write_prefixes={'bridge': ['tests/']},
        check_kinds={'bridge': 'bridge-selftest'},
        selftest_profiles={'bridge': {'python': sys.executable, **profile}}),
        lambda: True, root / 'logs')


def test_backend_commands_are_fixed_and_selectors_are_bounded(tmp_path):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_example.py').write_text('def test_ok(): pass')
    runner = tools(tmp_path)
    command = runner.build_command('bridge', 'bridge-selftest', 'backend-unit',
        'tests/test_example.py::test_ok')
    assert command[:4] == [sys.executable, '-B', '-m', 'pytest']
    assert 'tests/test_example.py::test_ok' in command
    assert '--live-models' not in command
    for goal, selected in [('shell', ''), ('backend-unit', '--live-models'),
        ('backend-unit', '../outside.py'), ('backend-unit', 'tests/a.py;whoami'),
        ('backend-unit', 'tests/a.py -p evil'), ('dashboard-typecheck', 'tests/test_example.py')]:
        with pytest.raises((PermissionError, ValueError)):
            runner.build_command('bridge', 'bridge-selftest', goal, selected)


def test_integration_refuses_missing_test_database_before_launch(tmp_path):
    with pytest.raises((PermissionError, ValueError), match='test database'):
        tools(tmp_path).run_checks('bridge', 'bridge-selftest', 'backend-integration')


def test_unit_receipt_reports_skips_and_never_injects_database_or_provider_secrets(tmp_path, monkeypatch):
    folder = tmp_path / 'tests'
    folder.mkdir()
    (folder / 'test_example.py').write_text('''import os, pytest
def test_clean():
    assert not any(k in os.environ for k in ('BRIDGE_MONITOR_DSN', 'OPENAI_API_KEY', 'BRIDGE_MONITOR_TEST_DSN', 'ARTIFACTORY_TOKEN'))
    assert 'home-' in os.environ['USERPROFILE']
@pytest.mark.skip(reason='fixture skip')
def test_skipped(): pass
''')
    monkeypatch.setenv('BRIDGE_MONITOR_DSN', 'private')
    monkeypatch.setenv('BRIDGE_MONITOR_TEST_DSN', 'private-test')
    monkeypatch.setenv('OPENAI_API_KEY', 'private-key')
    monkeypatch.setenv('ARTIFACTORY_TOKEN', 'private-artifactory')
    result = tools(tmp_path).run_checks('bridge', 'bridge-selftest', 'backend-unit', 'tests/test_example.py')
    assert result['exit_code'] == 0
    assert result['test_counts'] == {'tests': 2, 'failures': 0, 'errors': 0, 'skipped': 1}
    assert result['verification'] == 'partial'
    assert Path(tmp_path / 'logs' / (result['run_id'] + '.json')).is_file()


@pytest.mark.parametrize('dsn', ['dbname=production host=127.0.0.1',
    'dbname=bridge_monitor_test host=remote.example', 'not a dsn',
    'dbname=bridge_monitor_test host=127.0.0.1 password=do-not-log',
    'dbname=bridge_monitor_test host=127.0.0.1 sslpassword=do-not-log'])
def test_database_settings_cannot_target_operational_or_remote_database(tmp_path, dsn):
    secret = tmp_path / 'test-db.json'
    secret.write_text(json.dumps({'dsn': dsn}))
    runner = tools(tmp_path, test_dsn_file=str(secret))
    with pytest.raises((PermissionError, ValueError), match='test database'):
        runner.run_checks('bridge', 'bridge-selftest', 'backend-integration')


def test_dashboard_uses_pinned_node_and_checkout_local_script(tmp_path):
    script = tmp_path / 'dashboard/node_modules/typescript/bin/tsc'
    script.parent.mkdir(parents=True)
    script.write_text('not executed')
    runner = tools(tmp_path, node=sys.executable)
    assert runner.build_command('bridge', 'bridge-selftest', 'dashboard-typecheck') == [
        sys.executable, str(script), '--noEmit']


def test_read_only_task_cannot_keep_selftest_profiles(tmp_path):
    from types import SimpleNamespace
    configured = tools(tmp_path).policy
    grant = configured.for_task(SimpleNamespace(scope=['repository-execution'], context={
        'repository_ids': 'bridge', 'project_root': str(tmp_path), 'repository_access': 'read-only'}))
    assert not grant.selftest_profiles


def test_dashboard_receipt_counts_skips_instead_of_claiming_full_verification(tmp_path):
    script = tmp_path / 'dashboard/node_modules/@playwright/test/cli.js'
    script.parent.mkdir(parents=True)
    script.write_text('import os,json\nfrom pathlib import Path\nPath(os.environ["PLAYWRIGHT_JSON_OUTPUT_NAME"]).write_text(json.dumps({"stats":{"expected":1,"unexpected":0,"flaky":0,"skipped":1}}))')
    secret = tmp_path / 'db.json'
    secret.write_text(json.dumps({'dsn':'host=127.0.0.1 dbname=bridge_monitor_test_fixture'}))
    result = tools(tmp_path, node=sys.executable, test_dsn_file=str(secret)).run_checks(
        'bridge', 'bridge-selftest', 'dashboard-e2e')
    assert result['test_counts'] == {'tests':2, 'failures':0, 'errors':0, 'skipped':1}
    assert result['verification'] == 'partial'


def test_operator_browser_channel_is_scoped_to_the_test_environment():
    from bridge.selftests import SelfTestProfile, environment
    configured = SelfTestProfile(python=sys.executable, browser_channel='msedge')
    assert environment(configured, 'dashboard-typecheck')['BRIDGE_MONITOR_PLAYWRIGHT_CHANNEL'] == 'msedge'
    with pytest.raises(ValueError):
        SelfTestProfile(python=sys.executable, browser_channel='arbitrary-browser')


def test_browser_selection_accepts_only_existing_specs_and_failed_counts_are_not_green(tmp_path):
    script = tmp_path / 'dashboard/node_modules/@playwright/test/cli.js'
    script.parent.mkdir(parents=True)
    script.write_text('import os,json\nfrom pathlib import Path\nPath(os.environ["PLAYWRIGHT_JSON_OUTPUT_NAME"]).write_text(json.dumps({"stats":{"expected":0,"unexpected":1,"flaky":0,"skipped":0}}))')
    spec = tmp_path / 'dashboard/tests/smoke.spec.ts'
    spec.parent.mkdir(parents=True)
    spec.write_text('// synthetic fixture')
    secret = tmp_path / 'db.json'
    secret.write_text(json.dumps({'dsn':'host=127.0.0.1 dbname=bridge_monitor_test_fixture'}))
    runner = tools(tmp_path, node=sys.executable, test_dsn_file=str(secret))
    command = runner.build_command('bridge', 'bridge-selftest', 'dashboard-e2e', 'smoke.spec.ts')
    assert 'tests/smoke.spec.ts' in command
    with pytest.raises(ValueError):
        runner.build_command('bridge', 'bridge-selftest', 'dashboard-e2e', '../outside.spec.ts')
    result = runner.run_checks('bridge', 'bridge-selftest', 'dashboard-e2e', 'smoke.spec.ts')
    assert result['exit_code'] == 0
    assert result['verification'] == 'failed'
