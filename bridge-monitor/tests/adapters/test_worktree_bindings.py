"""A separate checkout needs a host grant, not merely shared Git history."""
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from bridge.repository import RepositoryPolicy


def fixture(tmp_path):
    origin = tmp_path / 'origin'
    origin.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(origin), *args], check=True, capture_output=True)
    git('init', '--quiet')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--quiet', '--allow-empty', '-m', 'fixture')
    checkout = tmp_path / 'checkout'
    git('worktree', 'add', '--quiet', '--detach', str(checkout))
    for root in (origin, checkout):
        (root / 'project').mkdir()
    return origin / 'project', checkout / 'project'


def policy(origin, checkout, **extra):
    return RepositoryPolicy(enabled=True, roots={'source':str(origin), 'task':str(checkout)},
        write_prefixes={'source':['src/'], 'task':['src/', 'outside/']},
        denied_prefixes={'source':['src/private/']},
        check_kinds={'source':'bridge-selftest'},
        selftest_profiles={'source':{'python':sys.executable}},
        worktree_origins={'task':str(origin)},
        worktree_expirations={'task':datetime.now(timezone.utc) + timedelta(hours=1)}, **extra)


def task(checkout, prefixes):
    return SimpleNamespace(scope=['repository-execution'], context={
        'repository_ids':'task', 'project_root':str(checkout), 'repository_access':'read-write',
        'repository_write_prefixes':json.dumps({'task':prefixes})})


def test_verified_worktree_inherits_origin_limits_and_ownership(tmp_path):
    from bridge.repository_worker import lock_ids, binding, create_config
    from contracts.work import DispatchLease
    origin, checkout = fixture(tmp_path)
    configured = policy(origin, checkout)
    grant = configured.for_task(task(checkout, ['src/']))
    assert grant.check_kinds == {'task':'bridge-selftest'}
    assert grant.selftest_profiles['task'].python == sys.executable
    assert 'src/private/' in grant.denied_prefixes['task']
    assert lock_ids(grant) == lock_ids(configured.for_task(SimpleNamespace(
        scope=['repository-execution'], context={'repository_ids':'source',
        'project_root':str(origin), 'repository_access':'read-only'})))
    # The serialized lease must independently validate inside the restricted server.
    assert RepositoryPolicy.model_validate(grant.model_dump()).worktree_origins == grant.worktree_origins
    assert binding(grant)
    lease = DispatchLease(lease_id='fixture',call_id='call',owner_incarnation='owner',fencing_token=1,policy_version='p')
    assert create_config(grant, lease, tmp_path).is_file()
    with pytest.raises(PermissionError):
        configured.for_task(task(checkout, ['outside/']))


def test_expired_and_unrelated_worktrees_fail_closed(tmp_path):
    origin, checkout = fixture(tmp_path)
    configured = policy(origin, checkout)
    configured.worktree_expirations['task'] = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises((ValueError, PermissionError), match='expired'):
        configured.for_task(task(checkout, ['src/']))
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    with pytest.raises((ValueError, PermissionError), match='worktree'):
        policy(origin, unrelated)


def test_worktree_cannot_bind_a_different_project_subdirectory(tmp_path):
    origin, checkout = fixture(tmp_path)
    sibling = checkout.parent / 'other'
    sibling.mkdir()
    with pytest.raises((ValueError, PermissionError), match='worktree'):
        policy(origin, sibling)


@pytest.mark.parametrize('representation', ['datetime', 'iso', 'timestamp'])
def test_expired_registration_does_not_disable_other_roots_at_startup(tmp_path, representation):
    origin, checkout = fixture(tmp_path)
    data = policy(origin, checkout).model_dump()
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    data['worktree_expirations']['task'] = (expired.isoformat() if representation == 'iso'
        else expired.timestamp() if representation == 'timestamp' else expired)
    reloaded = RepositoryPolicy.model_validate(data)
    assert reloaded.roots == {'source':str(origin)}
    assert 'task' not in reloaded.write_prefixes


def test_descendant_scope_uses_worktree_relative_prefixes(tmp_path):
    origin, checkout = fixture(tmp_path)
    configured = policy(origin, checkout)
    configured.write_prefixes['task'] = ['src/safe/']
    for base in (origin, checkout):
        (base/'src/safe').mkdir(parents=True)
        (base/'src/other').mkdir()
    assert configured.for_task(task(checkout/'src/safe', ['new/']))
    with pytest.raises(PermissionError):
        configured.for_task(task(checkout/'src/other', ['src/safe/']))


def test_worktree_verification_failure_is_a_revocation_with_receipt(tmp_path, monkeypatch):
    import json
    from bridge.repository import RepositoryTools
    import bridge.worktrees
    origin, checkout = fixture(tmp_path)
    grant = policy(origin, checkout).for_task(task(checkout, ['src/']))
    runner = RepositoryTools(grant, lambda:True, tmp_path/'logs')
    original = bridge.worktrees.verify
    calls = [0]
    def verify(*args, **kwargs):
        calls[0] += 1
        if calls[0] > 2:
            raise ValueError('synthetic git failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(bridge.worktrees, 'verify', verify)
    monkeypatch.setattr(runner, 'build_command', lambda *a: [sys.executable,'-c','import time; time.sleep(15)'])
    with pytest.raises(PermissionError):
        runner.run_checks('task', 'maven', 'version')
    receipts = list((tmp_path/'logs').glob('*.json'))
    assert receipts
    assert json.loads(receipts[0].read_text())['revoked'] is True
