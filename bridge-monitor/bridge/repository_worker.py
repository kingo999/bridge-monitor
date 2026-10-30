"""Leased repository-only MCP surface belonging to the existing Bridge."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import psycopg
from mcp.server.fastmcp import FastMCP

from bridge.repository import RepositoryPolicy, RepositoryTools, tool_names_for_policy
from bridge.runtime.admission import ARBITRATION_LOCK


def binding(policy):
    return hashlib.sha256(json.dumps(policy.model_dump(), sort_keys=True).encode()).hexdigest()


def lock_ids(policy):
    return ['repository:'+hashlib.sha256(os.path.normcase(str(Path(p).resolve())).encode()).hexdigest()
            for p in policy.roots.values()]


def acquire(dsn, lease, policy):
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (ARBITRATION_LOCK,))
        if not conn.execute("SELECT 1 FROM runtime_jobs WHERE call_id=%s AND lease_id=%s "
                "AND owner_incarnation=%s AND fencing_token=%s AND state='active'",
                (lease.call_id,lease.lease_id,lease.owner_incarnation,lease.fencing_token)).fetchone():
            return False
        for key in lock_ids(policy):
            other=conn.execute('SELECT holder_call_id FROM runtime_source_locks WHERE source_id=%s', (key,)).fetchall()
            if any(row[0] != lease.call_id for row in other):
                return False
        for key in lock_ids(policy):
            conn.execute("INSERT INTO runtime_source_locks(source_id,holder_call_id,mode,snapshot_ref) "
                "VALUES(%s,%s,'write',%s) ON CONFLICT DO NOTHING",(key,lease.call_id,binding(policy)))
        return True


def release(dsn, call_id):
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM runtime_source_locks WHERE holder_call_id=%s AND source_id LIKE 'repository:%%'",(call_id,))


def create_config(policy, lease, project, deadline_seconds=240):
    folder=Path(project)/'var'/'mcp'/('repository-'+lease.lease_id)
    folder.mkdir(parents=True,exist_ok=True)
    grant=folder/'grant.json'
    grant.write_text(json.dumps({'policy':policy.model_dump(),'lease':lease.model_dump(),
                                'deadline_epoch':time.time()+deadline_seconds-5}),encoding='utf-8')
    config=folder/'repository.json'
    config.write_text(json.dumps({'mcpServers':{'repository':{
        'command':sys.executable,'args':['-B','-m','bridge.repository_worker','--grant',str(grant)],
        }}}),encoding='utf-8')
    return config


def repository_authorized(dsn, policy, lease, deadline_epoch):
    """Bounded authority lookup: DB outages must not stall process cancellation."""
    try:
        if time.time() >= deadline_epoch:
            return False
        with psycopg.connect(dsn, connect_timeout=2,
                options='-c statement_timeout=1500 -c lock_timeout=1000') as conn:
            live=conn.execute("SELECT 1 FROM runtime_jobs WHERE call_id=%s AND lease_id=%s "
                "AND owner_incarnation=%s AND fencing_token=%s AND state='active' AND execution_deadline_at>now() "
                "AND NOT EXISTS (SELECT 1 FROM runtime_reconciliations WHERE call_id=%s "
                "AND evidence->>'kind'='cancellation_requested')",
                (lease['call_id'],lease['lease_id'],lease['owner_incarnation'],lease['fencing_token'],lease['call_id'])).fetchone()
            held=conn.execute('SELECT source_id,snapshot_ref FROM runtime_source_locks WHERE holder_call_id=%s',
                              (lease['call_id'],)).fetchall()
            return bool(live) and time.time() < deadline_epoch and all(
                (key,binding(policy)) in held for key in lock_ids(policy))
    except Exception:
        return False


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--grant',required=True)
    args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]
    grant=Path(args.grant).resolve(strict=True)
    if not grant.is_relative_to(project/'var'/'mcp'):
        raise PermissionError('grant must belong to Bridge lease configuration')
    data=json.loads(grant.read_text(encoding='utf-8'))
    policy=RepositoryPolicy.model_validate(data['policy'])
    lease=data['lease']
    dsn=os.environ['BRIDGE_MONITOR_REPOSITORY_DSN']
    def authorized():
        return repository_authorized(dsn,policy,lease,data['deadline_epoch'])
    logs=project/'var'/'logs'/'repository'/lease['call_id']
    logs.mkdir(parents=True,exist_ok=True)
    tools=RepositoryTools(policy,authorized,logs,deadline_epoch=data['deadline_epoch'])
    server=FastMCP('Bridge leased repository tools')
    import functools
    def audited(name, method):
        @functools.wraps(method)
        def run(*a,**kw):
            note={'tool':name,'state':'interrupted'}
            try:
                result=method(*a,**kw)
                note={'tool':name,'state':'completed','result_sha256':hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()}
                if isinstance(result,dict):
                    note.update({k:result[k] for k in ('path','sha256','bytes','run_id','exit_code','seconds') if k in result})
                return result
            except Exception as exc:
                note={'tool':name,'state':'failed','error_type':type(exc).__name__}
                raise
            finally:
                note.update(call_id=lease['call_id'],observed_at=datetime.now(timezone.utc).isoformat(),
                            access=policy.access,policy_binding=binding(policy))
                with (logs/'operations.jsonl').open('a',encoding='utf-8') as handle:
                    handle.write(json.dumps(note)+'\n')
        return run
    import asyncio
    async def run_checks(repository: str, kind: str, goal: str, tests: str = '', offline: bool = True):
        return await asyncio.to_thread(audited('run_checks',tools.run_checks),repository,kind,goal,tests,offline)
    if policy.access == 'read-write':
        server.add_tool(run_checks,name='run_checks')
    async def run_framework(repository: str, campaign: str, action: str, payload: dict | None = None):
        return await asyncio.to_thread(audited('run_framework',tools.run_framework),repository,campaign,action,payload)
    if policy.access == 'read-write':
        server.add_tool(run_framework,name='run_framework')
    for name in tool_names_for_policy(policy):
        if name in ('run_checks','run_framework'):
            continue
        server.add_tool(audited(name,getattr(tools,name)),name=name)
    server.run(transport='stdio')


if __name__=='__main__':
    main()
