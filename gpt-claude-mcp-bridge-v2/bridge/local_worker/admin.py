"""Local operator commands; no recovery authority is exposed over MCP."""
import asyncio
import json
from .models import WorkerError,ReconnectRequest
from .service import WorkerService


def cmd_local_status(args,env):
    try:
        result=WorkerService(env).status()
        print(result.model_dump_json(indent=2) if args.json else f'{result.availability}; {result.coordination}; {result.code}')
        return 0
    except (WorkerError,OSError,ValueError):
        print('LOCAL-00 state unavailable; normal v2 architect workflow remains available.')
        return 1


def cmd_local_reconnect(args,env):
    async def run():
        service=WorkerService(env)
        try:return await service.handle(ReconnectRequest(action='reconnect',address=args.address))
        finally:await service.aclose()
    result=asyncio.run(run())
    print(result.model_dump_json(indent=2))
    return 0 if result.availability=='available' else 1


def cmd_local_resolve_uncertain(args,env):
    if not args.confirmed_idle or not args.reason.strip() or not args.evidence_ref.strip():
        print('Requires --confirmed-idle plus operator reason/evidence that the earlier server request ended.')
        return 2
    if len(args.request_id)!=32 or any(ch not in '0123456789abcdef' for ch in args.request_id):return 2
    service=WorkerService(env)
    try:
        with service.store.try_lock():
            if service.store.active() is None:
                path=service.store.path(f'resolutions/{args.request_id}.json')
                if path.exists():
                    previous=service.store.read(f'resolutions/{args.request_id}.json',16384)
                    if previous.get('reason')==args.reason and previous.get('evidence_ref')==args.evidence_ref:
                        print('Resolution already recorded; no inference replayed.');return 0
                raise WorkerError('request_conflict')
            service.store.resolve_uncertain(args.request_id,True,args.reason,args.evidence_ref)
        print('Uncertain request retired using operator confirmation; no inference replayed.')
        return 0
    except (WorkerError,OSError,ValueError):
        print('LOCAL-00 recovery refused; inspect status and the exact pending request.')
        return 1


def register_local_worker_commands(subparsers):
    group=subparsers.add_parser('local-worker',help='optional LOCAL-00 status, reconnect and deliberate recovery')
    commands=group.add_subparsers(dest='local_command',required=True)
    p=commands.add_parser('status');p.add_argument('--json',action='store_true');p.set_defaults(func=cmd_local_status)
    p=commands.add_parser('reconnect');p.add_argument('--address');p.set_defaults(func=cmd_local_reconnect)
    p=commands.add_parser('resolve-uncertain')
    p.add_argument('--request-id',required=True);p.add_argument('--confirmed-idle',action='store_true')
    p.add_argument('--reason',required=True);p.add_argument('--evidence-ref',required=True)
    p.set_defaults(func=cmd_local_resolve_uncertain)
