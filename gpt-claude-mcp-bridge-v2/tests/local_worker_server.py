"""Test-only stdio launcher. No production environment hook selects this fixture."""
import asyncio
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from bridge import server
from bridge.local_worker.service import WorkerService
from bridge.local_worker.models import TransportOutcome
from bridge.local_worker.settings import MODEL,DIGEST
from bridge.local_worker import _baseline


class SyntheticTransport:
    async def exchange(self,request):
        if len(sys.argv)>1:await asyncio.sleep(float(sys.argv[1]))
        data={'models':[]}
        if request.path=='/api/tags':data={'models':[{'name':MODEL,'digest':DIGEST}]}
        elif request.path=='/api/show':data=_baseline.METADATA
        elif request.path=='/api/version':data={'version':_baseline.VERSION}
        elif request.path=='/api/generate':data=dict(model=MODEL,response='def stable_unique(items): return list(dict.fromkeys(items or []))',done=True,done_reason='stop',prompt_eval_count=100,eval_count=20)
        return TransportOutcome(status=200,phase='terminal_received',peer_ip='192.168.0.78',data=data)
    async def aclose(self):pass


server._local_service=WorkerService(transport=SyntheticTransport())
if __name__=='__main__':server.main()
