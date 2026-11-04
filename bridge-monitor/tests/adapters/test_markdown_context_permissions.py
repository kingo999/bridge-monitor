"""Persistent Markdown context writes need an explicit fresh task grant.

The host list is only an upper bound: existing/serialized grants keep their
binding, read-only tasks never write, and only the selected project's exact
granted files may change. Roots are temporary fixtures; no real project is used.
"""
import hashlib
import json
import os
from pathlib import Path
import tomllib

import pytest

from bridge.repository import READ_TOOL_NAMES, RepositoryPolicy, RepositoryTools, tool_names_for_policy
from bridge.repository_worker import binding
from contracts.work import TaskPacket

CONFIG = Path(__file__).resolve().parents[2] / 'config' / 'runtime-capabilities.proposal.toml'
BM = 'bridge-monitor'
CONTEXT_FILES = ['AGENTS.md', '.ai-context/INDEX.md', '.ai-context/topic.md']
CONTEXT_HOST = ('AGENTS.md', '.ai-context/')


def configured(table):
    with CONFIG.open('rb') as source:
        return list(tomllib.load(source)['repository'][table][BM])


def fixture_roots(tmp_path):
    bm, project = tmp_path / 'bridge-monitor', tmp_path / 'project'
    for root in (bm, project):
        (root / 'docs').mkdir(parents=True)
    (bm / 'AGENTS.md').write_bytes(b'bridge sentinel\n')
    return bm, project


def host(bm, project, bridge_writes=None):
    """Actual configured bridge-monitor host lists; only the roots are fixtures."""
    return RepositoryPolicy(enabled=True, roots={BM: str(bm), 'project': str(project)},
        write_prefixes={BM: configured('write_prefixes') if bridge_writes is None else bridge_writes,
                        'project': ['AGENTS.md', '.ai-context/', 'docs/']},
        denied_prefixes={BM: configured('denied_prefixes'), 'project': ['.ai-context/private/']})


def packet(root, ids=BM, access='read-write', writes=None):
    context = {'repository_ids': ids, 'project_root': str(root), 'repository_access': access}
    if writes is not None:
        context['repository_write_prefixes'] = json.dumps(writes)
    return TaskPacket(task_id='task-1', work_item_id='work-1', category='repository',
        scope=['repository-execution'], context=context, token_budget=1000, call_budget=1)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_configured_host_admits_explicit_context_files_with_hash_guarded_updates(tmp_path):
    bm, project = fixture_roots(tmp_path)
    tools = RepositoryTools(host(bm, project).for_task(packet(bm, writes={BM: CONTEXT_FILES})), lambda: True)
    with pytest.raises(ValueError):  # a hash for a missing file is rejected before mkdir
        tools.write_file(BM, '.ai-context/INDEX.md', 'index\n', '0' * 64)
    assert not (bm / '.ai-context').exists()
    tools.write_file(BM, 'AGENTS.md', 'project rules\n', digest(bm / 'AGENTS.md'))
    tools.write_file(BM, '.ai-context/INDEX.md', '| Topic |\n')
    created = tools.write_file(BM, '.ai-context/topic.md', 'Decision: A\n')
    for stale in (None, '0' * 64):
        with pytest.raises(ValueError):
            tools.write_file(BM, '.ai-context/topic.md', 'duplicate\n', stale)
    current = tools.read_file(BM, '.ai-context/topic.md')['sha256']
    assert current == created['sha256']
    tools.replace_text(BM, '.ai-context/topic.md', 'Decision: A', 'Decision: B', current)
    with pytest.raises(ValueError):
        tools.replace_text(BM, '.ai-context/topic.md', 'Decision: B', 'Decision: C', current)
    assert (bm / '.ai-context/topic.md').read_bytes() == b'Decision: B\n'
    assert (bm / 'AGENTS.md').read_bytes() == b'project rules\n'
    assert sorted(p.name for p in (bm / '.ai-context').iterdir()) == ['INDEX.md', 'topic.md']
    assert not list(bm.rglob('.bridge-write-*'))


@pytest.mark.parametrize('prefixes', [None, [], ['.ai-context'], ['AGENTS.md/'], ['AGENTS.md.bak'],
    ['.ai-context-neighbor/'], ['.ai-context/../AGENTS.md'], ['../project/AGENTS.md']])
def test_missing_invalid_or_neighbor_context_grants_are_rejected(tmp_path, prefixes):
    bm, project = fixture_roots(tmp_path)
    writes = None if prefixes is None else {BM: prefixes}
    with pytest.raises(PermissionError):
        host(bm, project).for_task(packet(bm, writes=writes))


def test_existing_grants_rebind_identically_and_gain_no_context_authority(tmp_path):
    bm, project = fixture_roots(tmp_path)
    base = [p for p in configured('write_prefixes') if p not in CONTEXT_HOST]
    old_host = host(bm, project, base)
    new_host = host(bm, project, [*base, *CONTEXT_HOST])
    stored = [packet(bm, writes={BM: ['docs/']}), packet(bm, access='read-only'),
              packet(project, 'project', writes={'project': ['docs/']})]
    for task in stored:
        frozen = old_host.for_task(task)
        before = binding(frozen)
        rebound = new_host.for_task(TaskPacket.model_validate_json(task.model_dump_json()))
        assert binding(rebound) == before == binding(frozen)
    sentinel = (bm / 'AGENTS.md').read_bytes()
    docs = RepositoryTools(new_host.for_task(packet(bm, writes={BM: ['docs/']})), lambda: True)
    docs.write_file(BM, 'docs/allowed.md', 'ok\n')
    for path, expected in (('AGENTS.md', digest(bm / 'AGENTS.md')), ('.ai-context/INDEX.md', None)):
        with pytest.raises(PermissionError, match='task file scope'):
            docs.write_file(BM, path, 'widened\n', expected)
    narrow = RepositoryTools(new_host.for_task(packet(bm, writes={BM: ['.ai-context/INDEX.md']})), lambda: True)
    narrow.write_file(BM, '.ai-context/INDEX.md', 'index\n')
    for path in ('AGENTS.md', 'AGENTS.md.bak', '.ai-context/other.md', '.ai-context-neighbor/x.md', 'docs/x.md'):
        with pytest.raises(PermissionError, match='task file scope'):
            narrow.write_file(BM, path, 'widened\n')
    assert (bm / 'AGENTS.md').read_bytes() == sentinel
    assert [p.name for p in (bm / '.ai-context').iterdir()] == ['INDEX.md']


@pytest.mark.parametrize('ids', [BM, 'project'])
def test_read_only_task_cannot_write_context_even_with_supplied_prefixes(tmp_path, ids):
    bm, project = fixture_roots(tmp_path)
    root = bm if ids == BM else project
    (root / 'AGENTS.md').write_bytes(b'sentinel\n')
    before = (root / 'AGENTS.md').read_bytes()
    grant = host(bm, project).for_task(packet(root, ids, 'read-only', {ids: ['AGENTS.md', '.ai-context/']}))
    assert tuple(tool_names_for_policy(grant)) == READ_TOOL_NAMES
    tools = RepositoryTools(grant, lambda: True)
    current = digest(root / 'AGENTS.md')
    for operation in (lambda: tools.write_file(ids, '.ai-context/INDEX.md', 'x\n'),
                      lambda: tools.write_file(ids, 'AGENTS.md', 'x\n', current),
                      lambda: tools.replace_text(ids, 'AGENTS.md', 'sentinel', 'x', current)):
        with pytest.raises(PermissionError, match='read-only'):
            operation()
    assert not (root / '.ai-context').exists()
    assert (root / 'AGENTS.md').read_bytes() == before


def test_selected_project_grant_writes_only_that_project_context(tmp_path):
    bm, project = fixture_roots(tmp_path)
    sentinel = (bm / 'AGENTS.md').read_bytes()
    grant = host(bm, project).for_task(packet(project, 'project',
        writes={'project': ['AGENTS.md', '.ai-context/INDEX.md']}))
    assert set(grant.roots) == {'project'}
    tools = RepositoryTools(grant, lambda: True)
    tools.write_file('project', 'AGENTS.md', 'project rules\n')
    tools.write_file('project', '.ai-context/INDEX.md', 'project index\n')
    assert (project / '.ai-context/INDEX.md').read_bytes() == b'project index\n'
    for repository, path in ((BM, '.ai-context/INDEX.md'), (BM, 'AGENTS.md'),
                             ('project', '../bridge-monitor/AGENTS.md'),
                             ('project', '..\\bridge-monitor\\.ai-context\\INDEX.md')):
        with pytest.raises(PermissionError):
            tools.write_file(repository, path, 'escaped\n')
    assert (bm / 'AGENTS.md').read_bytes() == sentinel
    assert not (bm / '.ai-context').exists()


@pytest.mark.parametrize('ids,root_name,writes', [
    ('project', 'bridge-monitor', {'project': ['.ai-context/INDEX.md']}),
    (BM, 'project', {BM: ['.ai-context/INDEX.md']}),
    ('unknown', 'project', {'unknown': ['.ai-context/INDEX.md']}),
    ('project', 'outside', {'project': ['.ai-context/INDEX.md']}),
    ('project', 'project', {BM: ['.ai-context/INDEX.md']})])
def test_wrong_or_unselected_root_has_no_fallback(tmp_path, ids, root_name, writes):
    bm, project = fixture_roots(tmp_path)
    (tmp_path / 'outside').mkdir()
    with pytest.raises(PermissionError):
        host(bm, project).for_task(packet(tmp_path / root_name, ids, writes=writes))
    assert not (bm / '.ai-context').exists() and not (project / '.ai-context').exists()


def test_existing_path_guards_still_apply_inside_context_allowance(tmp_path):
    bm, project = fixture_roots(tmp_path)
    grant = host(bm, project).for_task(packet(project, 'project', writes={'project': ['.ai-context/']}))
    tools = RepositoryTools(grant, lambda: True)
    for path, reason in (('.ai-context/secrets/x.md', 'path denied'), ('.ai-context/.env.md', 'secret'),
                         ('.ai-context/key.pem', 'secret'), ('.ai-context/private/x.md', 'excluded'),
                         ('.ai-context/tool.exe', 'approved text'), ('.ai-context/../AGENTS.md', 'path denied')):
        with pytest.raises(PermissionError, match=reason):
            tools.write_file('project', path, 'x\n')
    (project / '.ai-context').mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_bytes(b'outside\n')
    os.link(outside, project / '.ai-context/linked.md')
    with pytest.raises(PermissionError, match='hard-linked'):
        tools.write_file('project', '.ai-context/linked.md', 'x\n', digest(outside))
    revoked = RepositoryTools(grant, lambda: False)
    with pytest.raises(PermissionError, match='revoked'):
        revoked.write_file('project', '.ai-context/INDEX.md', 'x\n')
    assert outside.read_bytes() == b'outside\n'
    assert [p.name for p in (project / '.ai-context').iterdir()] == ['linked.md']
