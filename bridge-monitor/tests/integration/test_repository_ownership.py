import uuid

import psycopg
import pytest

from bridge.repository import RepositoryPolicy
from bridge.repository_worker import acquire,release,lock_ids
from bridge.runtime.admission import admit_call
from bridge.runtime.ownership import register_incarnation,claim_dispatch,mark_execution_uncertain
from tests.integration.test_admission import _call
from tests.integration.test_ownership import _setup


def test_repository_write_ownership_is_lease_bound_and_retained_if_uncertain(runtime_dsn,tmp_path):
    policy,version=_setup(runtime_dsn)
    name='repo-'+str(uuid.uuid4())
    request=_call(name,name)
    assert admit_call(runtime_dsn,request,policy,policy_version=version,pool='claude',reserve_tokens=100).state=='queued'
    owner=str(uuid.uuid4());register_incarnation(runtime_dsn,owner,'test-repo-worker')
    lease=claim_dispatch(runtime_dsn,pool='claude',incarnation_id=owner,call_id=name)
    repository=RepositoryPolicy(enabled=True,access='read-write',roots={'mri':str(tmp_path)},write_prefixes={'mri':['work/p1/']})
    assert acquire(runtime_dsn,lease,repository)
    assert acquire(runtime_dsn,lease,repository)
    assert not acquire(runtime_dsn,lease.model_copy(update={'fencing_token':lease.fencing_token+1}),repository)
    mark_execution_uncertain(runtime_dsn,name,reason='test-interruption')
    assert not acquire(runtime_dsn,lease,repository)
    with psycopg.connect(runtime_dsn) as conn:
        assert conn.execute('SELECT count(*) FROM runtime_source_locks WHERE holder_call_id=%s',(name,)).fetchone()[0]==1
    release(runtime_dsn,name)
    release(runtime_dsn,name)
    with psycopg.connect(runtime_dsn) as conn:
        assert conn.execute('SELECT count(*) FROM runtime_source_locks WHERE holder_call_id=%s',(name,)).fetchone()[0]==0


@pytest.mark.parametrize('access', ['read-only', 'read-write'])
def test_repository_stdio_surface_is_scoped_and_revocation_fences_writes(runtime_dsn,tmp_path,access):
    import asyncio,json,os,sys
    import subprocess
    from pathlib import Path
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters,stdio_client
    from bridge.repository_worker import create_config
    policy,version=_setup(runtime_dsn)
    name='repo-'+str(uuid.uuid4())
    assert admit_call(runtime_dsn,_call(name,name),policy,policy_version=version,pool='claude',reserve_tokens=100).state=='queued'
    owner=str(uuid.uuid4());register_incarnation(runtime_dsn,owner,'test-repo-stdio')
    lease=claim_dispatch(runtime_dsn,pool='claude',incarnation_id=owner,call_id=name)
    repository=RepositoryPolicy(enabled=True,access=access,roots={'mri':str(tmp_path)},check_kinds={'mri':'maven'},write_prefixes={'mri':['work/p1/']},maven=sys.executable)
    subprocess.run(['git','init','--quiet',str(tmp_path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['git','-C',str(tmp_path),'-c','user.name=Bridge test','-c','user.email=test@example.invalid','commit','--quiet','--allow-empty','-m','Synthetic fixture'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    assert acquire(runtime_dsn,lease,repository)
    root=Path(__file__).resolve().parents[2]
    config=create_config(repository,lease,root)
    entry=json.loads(config.read_text())['mcpServers']['repository']
    async def exercise():
        params=StdioServerParameters(command=entry['command'],args=entry['args'],cwd=str(root),
            env={**os.environ,'BRIDGE_MONITOR_REPOSITORY_DSN':runtime_dsn})
        async with stdio_client(params) as (reader,writer):
            async with ClientSession(reader,writer) as session:
                await session.initialize()
                if access == 'read-only':
                    assert {t.name for t in (await session.list_tools()).tools} == {'read_file','find_files','search_text','read_run_log','git_status'}
                    absent = await session.call_tool('write_file',{'repository':'mri','path':'work/p1/PROGRESS.md','content':'forbidden'})
                    assert absent.isError
                    absent = await session.call_tool('run_checks',{'repository':'mri','kind':'maven','goal':'version'})
                    assert absent.isError
                    assert not (tmp_path/'work/p1/PROGRESS.md').exists()
                    assert not (await session.call_tool('git_status',{'repository':'mri'})).isError
                    mark_execution_uncertain(runtime_dsn,name,reason='test revoke read-only')
                    assert (await session.call_tool('git_status',{'repository':'mri'})).isError
                    return
                assert {t.name for t in (await session.list_tools()).tools}=={'read_file','write_file','replace_text','find_files','search_text','run_checks','run_framework','read_run_log','git_status'}
                status=await asyncio.wait_for(session.call_tool('git_status',{'repository':'mri'}),timeout=10)
                assert not status.isError
                checked=await asyncio.wait_for(session.call_tool('run_checks',{'repository':'mri','kind':'maven','goal':'version'}),timeout=15)
                assert not checked.isError
                assert checked.structuredContent['result']['exit_code']==0 if 'result' in (checked.structuredContent or {}) else '"exit_code": 0' in str(checked.content)
                made=await session.call_tool('write_file',{'repository':'mri','path':'work/p1/PROGRESS.md','content':'test checkpoint'})
                assert not made.isError
                assert (tmp_path/'work/p1/PROGRESS.md').read_text()=='test checkpoint'
                import hashlib
                edited=await session.call_tool('replace_text',{'repository':'mri','path':'work/p1/PROGRESS.md','old_text':'test checkpoint','new_text':'updated checkpoint','expected_sha256':hashlib.sha256(b'test checkpoint').hexdigest()})
                assert not edited.isError
                assert (tmp_path/'work/p1/PROGRESS.md').read_text()=='updated checkpoint'
                denied=await session.call_tool('write_file',{'repository':'mri','path':'.git/config','content':'forbidden'})
                assert denied.isError
                mark_execution_uncertain(runtime_dsn,name,reason='test revoke')
                denied=await session.call_tool('write_file',{'repository':'mri','path':'after.java','content':'forbidden'})
                assert denied.isError
                assert not (tmp_path/'after.java').exists()
    try:
        asyncio.run(exercise())
    finally:
        release(runtime_dsn,name)


def test_confirmed_termination_frees_concurrency_without_inventing_usage(runtime_dsn,monkeypatch):
    from contracts.codex_claude import CodexClaudePolicy
    from bridge.runtime.codex_claude import reserve_codex_claude_call
    from bridge.runtime.reconciliation import reconcile_execution
    monkeypatch.setenv('BRIDGE_MONITOR_OPERATOR_TOKEN','synthetic-operator')
    dsn=runtime_dsn;policy,version=_setup(dsn)
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM cc_reservations WHERE workflow_id LIKE 'repo-hold-%%'")
    first='repo-hold-'+str(uuid.uuid4());second='repo-next-'+str(uuid.uuid4())
    p=CodexClaudePolicy(max_active_workers=1)
    def reserve(call):
        return reserve_codex_claude_call(dsn,call_id=call,work_item_id=call,workflow_id=first,profile='opus',amount='1',policy=p)
    assert reserve(first)['state']=='held'
    assert admit_call(dsn,_call(first,first),policy,policy_version=version,pool='claude',reserve_tokens=100).state=='queued'
    owner=str(uuid.uuid4());register_incarnation(dsn,owner,'synthetic-hold')
    lease=claim_dispatch(dsn,pool='claude',incarnation_id=owner,call_id=first)
    mark_execution_uncertain(dsn,first,reason='synthetic loss')
    assert reserve(second)['reason']=='worker_limit'
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE runtime_jobs SET state='terminated',slot_held=false WHERE call_id=%s",(first,))
    assert reserve(second)['reason']=='worker_limit'  # no correlated termination receipt yet
    reconcile_execution(dsn,first,{'kind':'operator_assertion','terminated':True},actor_kind='operator',actor_id='test',auth_token='synthetic-operator')
    assert reserve(second)['reason']=='worker_limit'  # an assertion is not proof
    assert reconcile_execution(dsn,first,{'kind':'adapter_termination_receipt','call_id':first,'lease_id':lease.lease_id,'owner_incarnation':owner,'terminated':True},actor_kind='operator',actor_id='test',auth_token='synthetic-operator')=='terminated'
    assert reserve(second)['state']=='held'
    with psycopg.connect(dsn) as conn:
        assert conn.execute('SELECT state FROM cc_reservations WHERE call_id=%s',(first,)).fetchone()[0]=='held'
        assert conn.execute('SELECT count(*) FROM cc_receipts WHERE call_id=%s',(first,)).fetchone()[0]==0
        conn.execute('DELETE FROM cc_reservations WHERE workflow_id=%s',(first,))


def test_repository_authority_database_lock_cannot_stall_cancellation(runtime_dsn,tmp_path):
    import time
    from bridge.repository_worker import repository_authorized
    policy,version = _setup(runtime_dsn)
    name = 'repo-'+str(uuid.uuid4())
    assert admit_call(runtime_dsn,_call(name,name),policy,policy_version=version,
        pool='claude',reserve_tokens=100).state == 'queued'
    owner = str(uuid.uuid4())
    register_incarnation(runtime_dsn,owner,'test-bounded-authority')
    lease = claim_dispatch(runtime_dsn,pool='claude',incarnation_id=owner,call_id=name)
    grant = RepositoryPolicy(enabled=True,roots={'mri':str(tmp_path)})
    assert acquire(runtime_dsn,lease,grant)
    try:
        assert repository_authorized(runtime_dsn,grant,lease.model_dump(),time.time()+30)
        with psycopg.connect(runtime_dsn) as blocker, blocker.transaction():
            blocker.execute('LOCK TABLE runtime_jobs IN ACCESS EXCLUSIVE MODE')
            started = time.monotonic()
            assert not repository_authorized(runtime_dsn,grant,lease.model_dump(),time.time()+30)
            assert time.monotonic()-started < 4
    finally:
        release(runtime_dsn,name)
