"""Task permissions must narrow the host allowlist and fail closed."""
import json
from types import SimpleNamespace

import pytest

from bridge.repository import RepositoryPolicy, RepositoryTools


def host(root, **extra):
    return RepositoryPolicy(enabled=True, roots={'mri3':str(root)},
        write_prefixes={'mri3':['src/','docs/']}, check_kinds={'mri3':'maven'}, maven='mvn.cmd', **extra)


def task(root, access='read-only', **extra):
    return SimpleNamespace(scope=['repository-execution'], context={
        'repository_ids':'mri3', 'project_root':str(root),
        'repository_access':access, **extra})


@pytest.mark.parametrize('access', [None, '', 'Read-Only', 'read-write ', 'write', 'false'])
def test_missing_or_invalid_task_access_is_rejected(tmp_path, access):
    t = task(tmp_path)
    if access is None:
        del t.context['repository_access']
    else:
        t.context['repository_access'] = access
    with pytest.raises(PermissionError):
        host(tmp_path).for_task(t)


def test_read_only_grant_removes_mutations_even_with_host_write_permission(tmp_path):
    (tmp_path/'src').mkdir()
    (tmp_path/'src/Test.java').write_text('unchanged')
    grant = host(tmp_path).for_task(task(tmp_path))
    tools = RepositoryTools(grant, lambda:True, tmp_path/'logs')
    assert tools.read_file('mri3', 'src/Test.java')['text'] == '1: unchanged'
    assert not grant.write_prefixes
    assert not grant.sql_write_prefixes
    for operation in [lambda:tools.write_file('mri3','src/New.java','bad'),
                      lambda:tools.replace_text('mri3','src/Test.java','unchanged','bad','0'*64),
                      lambda:tools.run_checks('mri3','maven','version'),
                      lambda:tools.run_framework('mri3','test','state')]:
        with pytest.raises(PermissionError, match='read-only'):
            operation()
    assert not (tmp_path/'logs').exists()
    assert (tmp_path/'src/Test.java').read_text() == 'unchanged'


def test_write_grant_is_explicit_and_component_bounded(tmp_path):
    grant = host(tmp_path).for_task(task(tmp_path, 'read-write',
        repository_write_prefixes=json.dumps({'mri3':['src/safe/']})))
    tools = RepositoryTools(grant, lambda:True)
    tools.write_file('mri3','src/safe/New.java','allowed')
    assert (tmp_path/'src/safe/New.java').read_text() == 'allowed'
    for path in ['src/safe-neighbor/New.java','src/other/New.java','docs/New.md']:
        with pytest.raises(PermissionError):
            tools.write_file('mri3',path,'denied')


@pytest.mark.parametrize('prefixes', [None, '{}', '{"mri3":["../"]}',
    '{"mri3":["src"]}', '{"mri3":["src/../docs/"]}',
    '{"other":["src/"]}', '{"mri3":["outside/"]}', '{"mri3":"src/"}'])
def test_read_write_requires_valid_host_bounded_task_prefixes(tmp_path, prefixes):
    extra = {} if prefixes is None else {'repository_write_prefixes':prefixes}
    with pytest.raises((PermissionError, ValueError)):
        host(tmp_path).for_task(task(tmp_path,'read-write',**extra))


@pytest.mark.parametrize('root', ['', '.', 'src', 'missing'])
def test_project_root_must_be_explicit_absolute_and_existing(tmp_path, root, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PermissionError):
        host(tmp_path).for_task(task(root))


def test_host_denied_component_cannot_be_read_or_written(tmp_path):
    (tmp_path/'mcp').mkdir()
    (tmp_path/'mcp/example.md').write_text('excluded')
    grant = host(tmp_path, denied_prefixes={'mri3':['mcp/']}).for_task(task(tmp_path))
    tools = RepositoryTools(grant, lambda:True)
    with pytest.raises(PermissionError):
        tools.read_file('mri3','mcp/example.md')
    assert tools.find_files('mri3')['files'] == []


def test_permissions_are_bound_and_do_not_share_mutable_task_or_host_state(tmp_path):
    from bridge.repository_worker import binding
    configured = host(tmp_path)
    t = task(tmp_path,'read-write',repository_write_prefixes='{"mri3":["src/safe/"]}')
    grant = configured.for_task(t)
    digest = binding(grant)
    configured.write_prefixes['mri3'].append('other/')
    t.context['repository_write_prefixes'] = '{"mri3":["docs/"]}'
    assert binding(grant) == digest
    assert binding(configured.for_task(task(tmp_path))) != digest


def test_explicit_root_maven_profile_has_no_legacy_module_selector(tmp_path):
    tools = RepositoryTools(host(tmp_path, maven_modules={'mri3':[]}).for_task(
        task(tmp_path,'read-write',repository_write_prefixes='{"mri3":["src/"]}')), lambda:True)
    assert tools.build_command('mri3','maven','test','ExampleTest') == [
        'mvn.cmd','-B','-ntp','-T','1','-o','-Dtest=ExampleTest',
        '-Dsurefire.failIfNoSpecifiedTests=false','test']


def test_linked_or_overlapping_host_roots_are_denied(tmp_path):
    nested = tmp_path/'nested'
    nested.mkdir()
    with pytest.raises(ValueError):
        RepositoryPolicy(enabled=True, roots={'first':str(tmp_path),'second':str(nested)})


def test_hardlinked_files_are_not_a_read_escape(tmp_path):
    import os
    root = tmp_path/'root'
    root.mkdir()
    private = tmp_path/'private.md'
    private.write_text('outside')
    os.link(private,root/'linked.md')
    tools = RepositoryTools(host(root), lambda:True)
    with pytest.raises(PermissionError, match='hard-linked'):
        tools.read_file('mri3','linked.md')


def test_read_only_cli_permission_excludes_every_mutating_tool(tmp_path):
    from bridge.repository import configure_repository_command
    from bridge.adapters.claude_cli import build_claude_command
    grant = host(tmp_path).for_task(task(tmp_path))
    command = configure_repository_command(build_claude_command('claude','sonnet','{}'), grant)
    assert set(command[command.index('--allowedTools')+1].split(',')) == {
        'mcp__repository__read_file','mcp__repository__find_files',
        'mcp__repository__search_text','mcp__repository__read_run_log','mcp__repository__git_status'}


def test_host_file_prefix_does_not_grant_filename_siblings(tmp_path):
    configured = RepositoryPolicy(enabled=True, roots={'mri3':str(tmp_path)},
        access='read-write', write_prefixes={'mri3':['one.java']})
    tools = RepositoryTools(configured, lambda:True)
    tools.write_file('mri3','one.java','yes')
    with pytest.raises(PermissionError):
        tools.write_file('mri3','one.java.extra.java','no')


def test_mixed_root_task_cannot_build_a_root_outside_write_scope(tmp_path):
    writable = tmp_path/'writable'
    readonly = tmp_path/'readonly'
    writable.mkdir()
    readonly.mkdir()
    p = RepositoryPolicy(enabled=True, roots={'first':str(writable),'second':str(readonly)},
        write_prefixes={'first':['src/'],'second':['src/']},
        check_kinds={'first':'maven','second':'maven'}, maven='not-executed.cmd',
        security_ai_campaigns={'second':['test-campaign']})
    grant = p.for_task(SimpleNamespace(scope=['repository-execution'], context={
        'repository_ids':'first,second','project_root':str(writable),
        'repository_access':'read-write','repository_write_prefixes':'{"first":["src/"]}'}))
    assert 'second' not in grant.check_kinds
    assert 'second' not in grant.security_ai_campaigns
    tools = RepositoryTools(grant, lambda:True)
    with pytest.raises((PermissionError,ValueError)):
        tools.run_checks('second','maven','version')


def test_read_only_git_status_does_not_execute_fsmonitor_hook(tmp_path):
    import subprocess
    root = tmp_path/'root'
    root.mkdir()
    def git(*args):
        return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True)
    git('init','--quiet')
    git('-c','user.name=Bridge test','-c','user.email=test@example.invalid',
        'commit','--quiet','--allow-empty','-m','Synthetic fixture')
    marker = tmp_path/'hook-ran.txt'
    hook = tmp_path/'fsmonitor.sh'
    hook.write_text('#!/bin/sh\nprintf called > "'+marker.as_posix()+'"\nprintf "token\\000"\n')
    hook.chmod(0o755)
    git('config','core.fsmonitor',hook.as_posix())
    grant = host(root).for_task(task(root))
    assert RepositoryTools(grant, lambda:True).git_status('mri3')['head']
    assert not marker.exists(), 'read-only status executed a repository-configured hook'
