"""Host-selected self-test recipes. No caller-supplied commands or environment."""
import json
from pathlib import Path
import re
from typing import Literal
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict


class SelfTestProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    python: str
    node: str = ''
    test_dsn_file: str = ''
    browser_channel: Literal['', 'chrome', 'msedge'] = ''


def executable(value):
    path = Path(value)
    if not value or not path.is_absolute() or not path.is_file():
        raise ValueError('self-test executable must be an existing absolute file')
    return str(path)


def command(tools, repository, goal, selected):
    profile = tools.policy.selftest_profiles.get(repository)
    if profile is None:
        raise PermissionError('self-test profile not granted')
    root = Path(tools.policy.roots[repository])
    if goal in ('backend-unit', 'backend-integration'):
        selectors = selected.split(',') if selected else (
            ['tests/contracts', 'tests/router', 'tests/adapters'] if goal == 'backend-unit'
            else ['tests/integration', 'tests/queries'])
        if len(selectors) > 32:
            raise ValueError('too many self-test selectors')
        for selector in selectors:
            if not re.fullmatch(r'tests/[A-Za-z0-9_/-]+(?:\.py)?(?:::[A-Za-z_][A-Za-z0-9_]*){0,2}', selector):
                raise ValueError('invalid self-test selector')
            target = tools.path(repository, selector.split('::')[0])
            if not target.exists():
                raise ValueError('self-test selector does not exist')
        return [executable(profile.python), '-B', '-m', 'pytest', '-q', '-ra', '--tb=short',
                '-p', 'no:cacheprovider', *selectors]
    scripts = {
        'dashboard-typecheck': ('typescript/bin/tsc', ['--noEmit']),
        'dashboard-build': ('next/dist/bin/next', ['build']),
        'dashboard-e2e': ('@playwright/test/cli.js', ['test', '--reporter=json']),
    }
    if goal not in scripts or selected and goal != 'dashboard-e2e':
        raise ValueError('self-test goal or selection not allowlisted')
    script, arguments = scripts[goal]
    if selected:
        specs = selected.split(',')
        if len(specs) > 16 or any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\.spec\.ts', name) for name in specs):
            raise ValueError('invalid dashboard self-test selector')
        for name in specs:
            if not tools.path(repository, 'dashboard/tests/' + name).is_file():
                raise ValueError('dashboard self-test selector does not exist')
        arguments = ['test', *['tests/' + name for name in specs], '--reporter=json']
    # Dependencies must be provisioned by the operator. Never install or fetch here.
    target = root / 'dashboard' / 'node_modules' / script
    if not target.is_file():
        raise ValueError('self-test dashboard dependencies are not provisioned')
    return [executable(profile.node), str(target), *arguments]


def environment(profile, goal):
    env = {'PYTHONDONTWRITEBYTECODE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
           'NEXT_TELEMETRY_DISABLED': '1', 'CI': '1',
           'BRIDGE_MONITOR_TEST_PYTHON': executable(profile.python)}
    if profile.browser_channel:
        env['BRIDGE_MONITOR_PLAYWRIGHT_CHANNEL'] = profile.browser_channel
    if goal not in ('backend-integration', 'dashboard-e2e'):
        return env
    try:
        from psycopg.conninfo import conninfo_to_dict
        settings = Path(profile.test_dsn_file)
        if not profile.test_dsn_file or not settings.is_absolute() or not settings.is_file():
            raise ValueError()
        value = json.loads(settings.read_text(encoding='utf-8'))['dsn']
        info = conninfo_to_dict(value)
        if (not re.fullmatch(r'bridge_monitor_test(?:_[A-Za-z0-9_]+)?', info.get('dbname', ''))
                or info.get('host') not in ('127.0.0.1', 'localhost', '::1')
                or info.get('password') or info.get('sslpassword')
                or any(key in info for key in ('service', 'hostaddr', 'options'))):
            raise ValueError()
    except Exception:
        # Do not echo a DSN, password, file contents or parser exception.
        raise PermissionError('dedicated local test database settings unavailable or invalid') from None
    env['BRIDGE_MONITOR_TEST_DSN'] = value
    return env


def test_counts(path):
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        return None
    try:
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == 'testsuite' else root.findall('testsuite')
        return {key: sum(int(s.get(key, '0')) for s in suites)
                for key in ('tests', 'failures', 'errors', 'skipped')}
    except (ET.ParseError, ValueError, OSError):
        return None


def browser_counts(path):
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        return None
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
        stats = report['stats']
        values = {k:stats[k] for k in ('expected', 'unexpected', 'flaky', 'skipped')}
        if any(type(v) is not int or v < 0 for v in values.values()):
            return None
        return {'tests':sum(values.values()), 'failures':values['unexpected'],
                'errors':len(report.get('errors', [])), 'skipped':values['skipped']}
    except (KeyError, TypeError, ValueError, OSError):
        return None
