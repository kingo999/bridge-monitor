"""Explicit LOCAL-00 native-runtime check. Default is metadata only.

Generation returns a draft for supervisor inspection; it never executes model output.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from bridge.local_worker.models import TransportRequest
from bridge.local_worker.settings import parse_address
from bridge.local_worker.transport import WorkerTransport
from bridge.local_worker.service import check_tags

ROOT=Path(__file__).resolve().parent.parent


def pilot_packet(task_id):
    return dict(task_id=task_id,category='isolated_repair',objective='Repair stable_unique',
        constraints='Integer list input. None and empty return []. Preserve first-occurrence order, do not mutate input, always return a fresh list.',
        source='def stable_unique(items): return sorted(set(items))',context='Standalone Python function; builtins only.',
        diagnostic='None raises TypeError; duplicates are sorted instead of preserving input order.',
        versions={'Python':'3.13'},references=[],output_form='One complete replacement function; no markdown fences.',context_id='local00-synthetic-v1',
        approval={'classification':'synthetic','approval_ref':'approved LOCAL-00 implementation plan synthetic pilot','audit_content_approved':True},
        checks=[{'check_id':'compile','kind':'compile','contract':'py_compile succeeds','method':'independent reviewed candidate compilation'},
                {'check_id':'behaviour','kind':'behaviour','contract':'None, empty, [2,1,2,0,1] -> [2,1,0]; input unchanged; fresh output','method':'independent executable assertions'}])


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    modes=p.add_mutually_exclusive_group();modes.add_argument('--metadata-only',action='store_true');modes.add_argument('--generate-synthetic',action='store_true')
    p.add_argument('--source-approved',action='store_true');p.add_argument('--audit-approved',action='store_true')
    p.add_argument('--task-id');p.add_argument('--output',type=Path)
    args=p.parse_args(argv)
    if args.generate_synthetic and not (args.source_approved and args.audit_approved and args.task_id):
        p.error('generation requires --source-approved --audit-approved and a stable --task-id')
    if args.task_id:
        from bridge.local_worker.models import TaskPacket
        try:TaskPacket(**pilot_packet(args.task_id))
        except ValueError:p.error('invalid task ID')
    return args


async def run_check(args):
    started=time.monotonic();report={'schema_version':1,'inference_requested':args.generate_synthetic,'connectivity':[]}
    transport=WorkerTransport()
    for host in ['skynet.local','192.168.0.78']:
        result=await transport.exchange(TransportRequest(endpoint=parse_address(host),method='GET',path='/api/tags'))
        try:check_tags(result);code='validated'
        except Exception as exc:code=getattr(exc,'code','validation_error')
        report['connectivity'].append({'host':host,'code':code,'status':result.status,'peer_ip':result.peer_ip,'elapsed_s':result.elapsed_s})
    params=StdioServerParameters(command=sys.executable,args=['-B',str(ROOT/'architect_bridge.py')],env=dict(os.environ))
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            tools=await client.list_tools();report['tools']=[tool.name for tool in tools.tools]
            async def call(request):
                response=await client.call_tool('local_worker',{'request':request})
                if response.isError:raise RuntimeError('local worker tool call rejected')
                return response.structuredContent
            # Health retry is explicit and separate from generation attempts.
            for _ in range(120):
                status=await call({'action':'status'})
                if status['availability']!='discovering':break
                await asyncio.sleep(.1)
            report['continue_without']=await call({'action':'continue_without'})
            report['reconnect']=await call({'action':'reconnect'})
            if args.generate_synthetic:
                report['generation']=await call({'action':'generate','attempt':1,'packet':pilot_packet(args.task_id)})
            report['status']=await call({'action':'status'})
    await transport.aclose()
    report['elapsed_s']=time.monotonic()-started
    return report


def main(argv=None):
    args=parse_args(argv)
    report=asyncio.run(run_check(args))
    output=args.output or ROOT/'docs/evidence/local00'/('live-'+(args.task_id or 'metadata')+'.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(output),'availability':report['status']['availability'],'generation':report.get('generation')},indent=2))
    if args.generate_synthetic:return 0 if report['generation']['state']=='draft_returned' else 1
    return 0 if report['status']['availability']=='available' else 1


if __name__=='__main__':raise SystemExit(main())
