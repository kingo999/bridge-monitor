import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def api():
    return importlib.import_module('bridge.repository')


def policy(root, **extra):
    return api().RepositoryPolicy(enabled=True, access='read-write', roots={'mri': str(root)},check_kinds={'mri':'maven'},write_prefixes={'mri':['src/','work/p1/','main.java','pom.xml']}, **extra)


def test_repository_is_disabled_by_default_and_has_no_roots():
    p = api().RepositoryPolicy()
    assert not p.enabled
    assert not p.roots


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'D:/escape', '.git/config', '.env',
                                  'env-local.ps1', '.npmrc', '.mvn/settings.xml', 'pt/export.json',
                                  'src/../pom.xml', 'file:stream', 'x\\..\\escape',
                                  '.git./config','.npmrc.','secrets /key','GIT~1/config','.mvn/settings.xml.'])
def test_repository_boundary_denies_escapes_and_secret_metadata(tmp_path, name):
    repo = api().RepositoryTools(policy(tmp_path), lambda: True)
    with pytest.raises((PermissionError, ValueError)):
        repo.read_file('mri', name)


def test_repository_write_requires_live_authority_and_matching_expected_content(tmp_path):
    (tmp_path/'src').mkdir()
    p = tmp_path/'src/Main.java'
    p.write_text('old', encoding='utf-8')
    alive = [True]
    repo = api().RepositoryTools(policy(tmp_path), lambda: alive[0])
    read = repo.read_file('mri', 'src/Main.java')
    repo.write_file('mri', 'src/Main.java', 'new', read['sha256'])
    assert p.read_text() == 'new'
    with pytest.raises(ValueError, match='changed'):
        repo.write_file('mri', 'src/Main.java', 'stale edit', read['sha256'])
    alive[0] = False
    with pytest.raises(PermissionError):
        repo.write_file('mri', 'src/Main.java', 'unauthorized', None)
    assert p.read_text() == 'new'


def test_repository_does_not_overwrite_existing_file_without_hash(tmp_path):
    (tmp_path/'main.java').write_text('original')
    repo = api().RepositoryTools(policy(tmp_path), lambda: True)
    with pytest.raises(ValueError):
        repo.write_file('mri', 'main.java', 'overwrite', None)
    assert (tmp_path/'main.java').read_text() == 'original'


def test_repository_write_allows_only_task_selected_roots_and_safe_text(tmp_path):
    repo = api().RepositoryTools(policy(tmp_path, max_file_bytes=20), lambda: True)
    with pytest.raises(PermissionError):
        repo.write_file('other', 'main.java', 'x', None)
    with pytest.raises(ValueError):
        repo.write_file('mri', 'main.java', 'x'*21, None)
    with pytest.raises(PermissionError):
        repo.write_file('mri', 'main.exe', 'x', None)
    repo.write_file('mri', 'work/p1/PROGRESS.md', 'checkpoint', None)
    assert (tmp_path/'work/p1/PROGRESS.md').read_text() == 'checkpoint'


def test_repository_refuses_linked_path_components(tmp_path):
    outside = tmp_path/'outside'
    outside.mkdir()
    root = tmp_path/'repo'
    root.mkdir()
    try:
        (root/'linked').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation unavailable')
    repo = api().RepositoryTools(policy(root), lambda: True)
    with pytest.raises(PermissionError):
        repo.write_file('mri', 'linked/stolen.java', 'x', None)


def test_maven_command_is_structured_and_cannot_invoke_git_or_arbitrary_goals(tmp_path):
    p = policy(tmp_path, maven='mvn.cmd')
    repo = api().RepositoryTools(p, lambda: True)
    command = repo.build_command('mri', 'maven', 'test', 'ExampleTest', True)
    assert command[0] == 'mvn.cmd'
    assert '-Dtest=ExampleTest' in command
    assert 'test' in command
    assert '-o' in command
    for kind, goal, selected in [('git','commit',''), ('maven','deploy',''),
                                  ('maven','test','A;git push'), ('maven','test','../../x')]:
        with pytest.raises(ValueError):
            repo.build_command('mri',kind,goal,selected,True)


def test_repository_command_and_reads_fail_closed_on_revoked_lease(tmp_path):
    repo = api().RepositoryTools(policy(tmp_path), lambda: False)
    with pytest.raises(PermissionError):
        repo.read_file('mri', 'pom.xml')
    with pytest.raises(PermissionError):
        repo.run_checks('mri','maven','test','',True)


def test_repository_route_requires_host_capability_and_exact_root_binding(tmp_path):
    from bridge.router.codex_claude import prepare_codex_claude_route
    from contracts.codex_claude import CodexClaudePolicy
    from contracts.work import TaskPacket
    profiles=CodexClaudePolicy(enabled=True)
    profiles.profiles['sonnet'].enabled=True
    task=TaskPacket(task_id='t', work_item_id='w', category='coding',scope=['repository-execution'],
        token_budget=1000,call_budget=1,context={'complexity':'ordinary','risk':'high',
        'project_root':str(tmp_path),'repository_ids':'mri','repository_access':'read-only'})
    absent=SimpleNamespace(profile_models=['claude-sonnet-5-5'])
    assert prepare_codex_claude_route(profiles,task,'fix',absent).action != 'claude'
    allowed=SimpleNamespace(profile_models=['claude-sonnet-5-5'],
        repository_policy=policy(tmp_path))
    assert prepare_codex_claude_route(profiles,task,'fix',allowed).action == 'claude'
    task.context['project_root']=str(tmp_path/'not-approved')
    assert prepare_codex_claude_route(profiles,task,'fix',allowed).action != 'claude'


def test_repository_adapter_config_uses_only_scoped_mcp_tools(tmp_path):
    from bridge.adapters.claude_cli import build_claude_command
    command=build_claude_command('claude','sonnet','{}',system_prompt='test')
    configured=api().configure_repository_command(command,policy(tmp_path))
    assert configured[configured.index('--tools')+1] == ''
    allowed=configured[configured.index('--allowedTools')+1].split(',')
    assert set(allowed)==set(api().REPOSITORY_TOOLS)
    assert 'Bash' in configured[configured.index('--disallowedTools')+1:]
    assert 'Agent' in configured[configured.index('--disallowedTools')+1:]
    assert configured[configured.index('--max-turns')+1] == '64'


def test_actual_runner_returns_version_receipt_and_scoped_log(tmp_path):
    import sys
    repo=api().RepositoryTools(policy(tmp_path,maven=sys.executable),lambda:True,tmp_path/'logs')
    result=repo.run_checks('mri','maven','version')
    assert result['exit_code']==0
    assert 'Python' in result['tail']
    assert repo.read_run_log(result['run_id'])['text']==result['tail']


def test_timed_out_command_is_terminated_and_retains_failure_receipt(tmp_path,monkeypatch):
    import sys
    repo=api().RepositoryTools(policy(tmp_path,maven=sys.executable,command_timeout_seconds=1),lambda:True,tmp_path/'logs')
    monkeypatch.setattr(repo,'build_command',lambda *a:[sys.executable,'-c','import time; print("started",flush=True); time.sleep(30)'])
    result=repo.run_checks('mri','maven','version')
    assert result['timed_out'] is True
    assert result['exit_code']!=0
    assert result['terminated'] is True
    assert 'started' in result['tail']


def test_analysis_still_rejects_repository_tool_frames_but_repository_mode_accepts_them():
    import json
    from bridge.adapters.claude_cli import ClaudeCLIAdapter
    from contracts.work import DispatchLease,TaskPacket
    class Runner:
        returncode=0
        terminated=True
        def stream(self,command,prompt):
            yield json.dumps({'type':'stream_event','event':{'type':'content_block_start','index':0,
                'content_block':{'type':'tool_use','id':'repo-tool','name':'mcp__repository__read_file'}}})
            yield json.dumps({'type':'stream_event','event':{'type':'content_block_stop','index':0}})
            yield json.dumps({'type':'result','model':'claude-opus-5-5','is_error':False,
                'result':json.dumps({'answer':'checkpoint','local_results':[],'route_choice':'paid_direct',
                                    'fallback_child_call_id':None,'work_resolution':'resolved'})})
    class Sink:
        def emit(self,*a,**kw):return True
    lease=DispatchLease(lease_id='lease',call_id='call',owner_incarnation='owner',fencing_token=1,policy_version='p')
    task=TaskPacket(task_id='t',work_item_id='w',category='test',scope=['analysis'],token_budget=1000,call_budget=1)
    adapter=ClaudeCLIAdapter(Runner(),executable='claude',model='claude-opus-5-5',mcp_config_path='{}')
    assert adapter.run(lease,task,'test',Sink()).state=='uncertain'
    adapter.repository_mode=True
    adapter.repository_policy=api().RepositoryPolicy()
    result=adapter.run(lease,task,'test',Sink())
    assert result.state=='completed'
    assert result.local_tool_attempts==0


def test_missing_or_empty_write_prefixes_deny_every_write(tmp_path):
    for prefixes in ({},{'mri':[]}):
        p=api().RepositoryPolicy(enabled=True,roots={'mri':str(tmp_path)},write_prefixes=prefixes)
        with pytest.raises(PermissionError):
            api().RepositoryTools(p,lambda:True).write_file('mri','unexpected.java','no',None)


def test_build_environment_has_no_bridge_provider_git_or_ssh_credentials(monkeypatch):
    values={'PATH':'safe-path','SystemRoot':'C:/Windows','JAVA_HOME':'java','TEMP':'tmp',
            'BRIDGE_MONITOR_DSN':'SECRET-DSN','BRIDGE_MONITOR_SYSTEM_TOKEN':'SECRET-TOKEN',
            'ANTHROPIC_API_KEY':'SECRET-API','OPENAI_API_KEY':'SECRET-OPENAI',
            'SSH_AUTH_SOCK':'secret-socket','GH_TOKEN':'SECRET-GH','JWT_KEY':'SECRET-JWT'}
    for k,v in values.items():monkeypatch.setenv(k,v)
    env=api().build_environment('configured-java')
    assert env['JAVA_HOME']=='configured-java'
    assert not any('SECRET' in value or value=='secret-socket' for value in env.values())
    assert not any(key.startswith(('BRIDGE_','ANTHROPIC_','OPENAI_','GIT_','SSH_')) for key in env)


def test_build_descriptor_write_is_not_a_capability(tmp_path):
    repo=api().RepositoryTools(policy(tmp_path),lambda:True)
    with pytest.raises(PermissionError):repo.write_file('mri','pom.xml','<execution>shell</execution>',None)


def test_parent_cli_job_terminates_a_child_left_after_normal_exit(tmp_path):
    import os,sys,time
    if os.name!='nt':pytest.skip('Windows job containment test')
    from bridge.adapters.claude_cli import SubprocessRunner
    runner=SubprocessRunner(cwd=tmp_path,timeout_seconds=5,client_tool_timeout_seconds=5,
        environment_overrides={'BRIDGE_REPOSITORY_MODE':'1'})
    source='import subprocess,sys; child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(6)"]); print(child.pid,flush=True)'
    started=time.monotonic()
    rows=list(runner.stream([sys.executable,'-c',source],''))
    assert time.monotonic()-started<4, 'descendant stdout held the worker open after parent exit'
    assert len(rows)==1
    assert runner.terminated and runner.tree_terminated
    import ctypes as c
    from ctypes import wintypes as w
    kernel=c.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.GetExitCodeProcess.argtypes=[w.HANDLE,c.POINTER(w.DWORD)];kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,int(rows[0]))
    if handle:
        try:
            code=w.DWORD();assert kernel.GetExitCodeProcess(handle,c.byref(code));assert code.value!=259
        finally:kernel.CloseHandle(handle)


def test_command_cannot_outlive_its_worker_budget(tmp_path):
    import sys,time
    repo=api().RepositoryTools(policy(tmp_path,maven=sys.executable),lambda:True,tmp_path/'logs',deadline_epoch=time.time()+15)
    with pytest.raises(PermissionError,match='remaining worker budget'):
        repo.run_checks('mri','maven','version')


def test_replace_text_preserves_other_bytes_and_requires_unique_match_and_hash(tmp_path):
    import hashlib
    p=tmp_path/'main.java';original=b'one\r\nold\r\nthree\r\n';p.write_bytes(original)
    alive=[True];repo=api().RepositoryTools(policy(tmp_path),lambda:alive[0])
    old_hash=hashlib.sha256(original).hexdigest()
    changed=repo.replace_text('mri','main.java','old','new',old_hash)
    assert p.read_bytes()==b'one\r\nnew\r\nthree\r\n'
    with pytest.raises(ValueError):repo.replace_text('mri','main.java','new','stale',old_hash)
    with pytest.raises(ValueError):repo.replace_text('mri','main.java','absent','x',changed['sha256'])
    p.write_text('repeat repeat');h=hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(ValueError):repo.replace_text('mri','main.java','repeat','x',h)
    alive[0]=False
    with pytest.raises(PermissionError):repo.replace_text('mri','main.java','repeat repeat','x',h)
    alive[0]=True
    with pytest.raises(PermissionError):repo.replace_text('mri','pom.xml','x','y',h)
    assert p.read_text()=='repeat repeat'


def test_ui_checks_support_only_pinned_parser_preparation_and_bounded_test_selection(tmp_path):
    p=api().RepositoryPolicy(enabled=True,roots={'ui':str(tmp_path)},check_kinds={'ui':'ui'},pnpm='pnpm.cmd',ui_upload_parser_install=True)
    repo=api().RepositoryTools(p,lambda:True)
    command=repo.build_command('ui','ui','prepare-upload-parser')
    assert command==['pnpm.cmd','--filter','mri-ui-server','add','--save-exact','--ignore-scripts','@fastify/busboy@3.2.4']
    assert repo.build_command('ui','ui','test','uploadByteLimit.node.spec.ts')[-2:]==['test:auth','uploadByteLimit.node.spec.ts']
    assert repo.build_command('ui','ui','auth-build')[-1]=='test:auth:build'
    import pytest
    for selected in ('--config=evil','../outside.spec.ts','x;whoami'):
        with pytest.raises(ValueError):repo.build_command('ui','ui','test',selected)
    denied=api().RepositoryTools(p.model_copy(update={'ui_upload_parser_install':False}),lambda:True)
    with pytest.raises(ValueError):denied.build_command('ui','ui','prepare-upload-parser')


def test_search_reaches_beyond_file_listing_cap_and_never_hides_truncation(tmp_path):
    folder=tmp_path/'src';folder.mkdir()
    for i in range(600):(folder/f'Class{i:04}.java').write_text('target marker')
    repo=api().RepositoryTools(policy(tmp_path),lambda:True)
    listing=repo.find_files('mri','src','*.java')
    assert listing['truncated'] and len(listing['files'])==500
    # Each unique class marker is present only in the file's contents, not its filename.
    for i in range(600):(folder/f'Class{i:04}.java').write_text(f'unique{i:04}')
    searched=repo.search_text('mri','unique0599','src','*.java')
    assert searched['matches'] and searched['matches'][0]['path']=='src/Class0599.java'
    assert not searched['truncated']


def test_sql_writes_require_explicit_resource_directory_grant(tmp_path):
    allowed = 'report-application/src/main/resources/db/'
    scope = {'mri': ['report-application/', 'work/p1/']}
    denied = api().RepositoryTools(api().RepositoryPolicy(enabled=True, roots={'mri': str(tmp_path)},
        write_prefixes=scope), lambda: True)
    with pytest.raises(PermissionError):
        denied.write_file('mri', allowed + 'deferral.sql', 'SELECT 1;', None)
    granted = api().RepositoryTools(api().RepositoryPolicy(enabled=True, access='read-write', roots={'mri': str(tmp_path)},
        write_prefixes=scope, sql_write_prefixes={'mri': [allowed]}), lambda: True)
    granted.write_file('mri', allowed + 'deferral.sql', 'SELECT 1;', None)
    assert (tmp_path / allowed / 'deferral.sql').read_text() == 'SELECT 1;'
    for path in ['report-application/other.sql', 'work/p1/live.sql', allowed + '../outside.sql']:
        with pytest.raises(PermissionError):
            granted.write_file('mri', path, 'SELECT 1;', None)
    revoked = api().RepositoryTools(granted.policy, lambda: False)
    with pytest.raises(PermissionError):
        revoked.write_file('mri', allowed + 'second.sql', 'SELECT 1;', None)


def test_replace_text_accepts_read_window_newlines_without_rewriting_other_bytes(tmp_path):
    import hashlib
    p = tmp_path / 'main.java'
    original = b'prefix\r\nfirst\r\nsecond\r\nsuffix\r\n'
    p.write_bytes(original)
    repo = api().RepositoryTools(policy(tmp_path), lambda: True)
    old_hash = hashlib.sha256(original).hexdigest()
    repo.replace_text('mri', 'main.java', 'first\nsecond', 'one\ntwo', old_hash)
    assert p.read_bytes() == b'prefix\r\none\r\ntwo\r\nsuffix\r\n'
    with pytest.raises(ValueError, match='changed'):
        repo.replace_text('mri', 'main.java', 'one\ntwo', 'other', old_hash)
    ambiguous = b'first\r\nsecond\r\nfirst\r\nsecond'
    p.write_bytes(ambiguous)
    with pytest.raises(ValueError, match='exactly once'):
        repo.replace_text('mri', 'main.java', 'first\nsecond', 'no', hashlib.sha256(ambiguous).hexdigest())
    assert p.read_bytes() == ambiguous


def test_repository_log_budget_has_safe_finite_bounds(tmp_path):
    assert policy(tmp_path).max_log_bytes == 8 * 1024 * 1024
    for value in (0, 32 * 1024 * 1024 + 1):
        with pytest.raises(ValueError):
            policy(tmp_path, max_log_bytes=value)


@pytest.mark.parametrize('budget,limited', [(65536, True), (131072, False)])
def test_runner_uses_explicit_log_budget(tmp_path, monkeypatch, budget, limited):
    import sys
    repo = api().RepositoryTools(policy(tmp_path, maven=sys.executable, max_log_bytes=budget,
        command_timeout_seconds=5), lambda: True, tmp_path / 'logs')
    monkeypatch.setattr(repo, 'build_command', lambda *args: [sys.executable, '-c',
        "import sys,time;sys.stdout.write('x'*100000);sys.stdout.flush();time.sleep(1)"])
    result = repo.run_checks('mri', 'maven', 'test')
    assert result['log_limit'] is limited
    assert result['timed_out'] is False
    assert result['terminated'] is True
    assert (result['exit_code'] == 0) is (not limited)
