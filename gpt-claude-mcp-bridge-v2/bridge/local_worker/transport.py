"""Total-deadline HTTP through a killable, private helper process."""
from __future__ import annotations
import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from .models import TransportRequest,TransportOutcome

ALLOWED={('GET','/api/tags'),('GET','/api/ps'),('POST','/api/show'),('GET','/api/version'),('POST','/api/generate')}


class WorkerTransport:
    def __init__(self,*,helper_path: Path | None=None,dial_override: tuple | None=None):
        self.helper_path=helper_path or Path(__file__).with_name('http_worker.py')
        self.dial_override=dial_override
        self.active_helpers=set()

    async def exchange(self,request: TransportRequest) -> TransportOutcome:
        if (request.method,request.path) not in ALLOWED or not math.isfinite(request.timeout_s) or request.timeout_s<=0 or not 1<=request.max_bytes<=4194304:
            return TransportOutcome(code='transport_request_rejected')
        payload=dict(host=request.endpoint.host,method=request.method,path=request.path,body=request.body,
                     timeout_s=request.timeout_s,max_bytes=request.max_bytes,test_dial=bool(self.dial_override))
        if self.dial_override:payload.update(dial_host=self.dial_override[0],dial_port=self.dial_override[1])
        data=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        if len(data)>262144:return TransportOutcome(code='transport_request_rejected')
        started=time.monotonic();process=None
        # Until the helper proves otherwise, a launched POST may have been delivered.
        phase='may_have_started' if request.path=='/api/generate' else 'not_sent'
        output_limit=12582912

        async def bounded_read(stream,limit):
            chunks=[];size=0
            while chunk:=await stream.read(min(65536,limit-size+1)):
                size+=len(chunk)
                if size>limit:raise ValueError('helper output exceeded limit')
                chunks.append(chunk)
            return b''.join(chunks)

        async def run():
            nonlocal process
            env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP'}}
            process=await asyncio.create_subprocess_exec(sys.executable,'-I','-B',str(self.helper_path.resolve()),
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.active_helpers.add(process)
            process.stdin.write(data);await process.stdin.drain();process.stdin.close()
            out,err=await asyncio.gather(bounded_read(process.stdout,output_limit),bounded_read(process.stderr,65536))
            await process.wait()
            if process.returncode:raise ValueError('helper failed')
            result=TransportOutcome.model_validate_json(out)
            if result.body_b64 and len(result.body_b64)>((request.max_bytes+2)//3)*4:
                raise ValueError('helper body exceeded limit')
            return result

        try:
            result=await asyncio.wait_for(run(),request.timeout_s)
        except asyncio.TimeoutError:
            result=TransportOutcome(code='transport_timeout',phase=phase)
        except (OSError,ValueError,RuntimeError):
            result=TransportOutcome(code='helper_protocol_error',phase=phase)
        finally:
            if process is not None:
                if process.returncode is None:
                    try:process.kill()
                    except ProcessLookupError:pass
                await asyncio.shield(process.wait())
                self.active_helpers.discard(process)
        result.elapsed_s=time.monotonic()-started
        return result

    async def aclose(self):
        for process in tuple(self.active_helpers):
            if process.returncode is None:process.kill()
            await process.wait()
            self.active_helpers.discard(process)
