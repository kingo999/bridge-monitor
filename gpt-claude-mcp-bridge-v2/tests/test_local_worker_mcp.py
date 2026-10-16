import asyncio
import json
import os
from pathlib import Path
import sys
import time
import unittest
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from tests.local_worker_helpers import TempWorld,packet_data


class Mcp(unittest.IsolatedAsyncioTestCase):
    async def test_exact_surface_and_real_roundtrip_all_five_actions(self):
        from bridge.server import mcp
        tools=await mcp.list_tools()
        self.assertEqual(sorted(t.name for t in tools),['consult_architect','local_worker','review_implementation'])
        w=TempWorld();self.addCleanup(w.close)
        env=dict(os.environ,USERPROFILE=str(w.home))
        launcher=Path(__file__).with_name('local_worker_server.py')
        async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B',str(launcher)],env=env)) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                await session.send_ping()
                for action in ['status','continue_without','reconnect']:
                    result=await session.call_tool('local_worker',{'request':{'action':action}})
                    self.assertFalse(result.isError,result.content)
                result=await session.call_tool('local_worker',{'request':{'action':'generate','packet':packet_data(),'attempt':1}})
                self.assertFalse(result.isError,result.content)
                data=result.structuredContent
                self.assertEqual(data['state'],'draft_returned',data)
                report=dict(task_id='sample',attempt=1,packet_hash=data['packet_hash'],result_digest=data['result_digest'],verdict='accepted',toolchain_ref='candidate pycompile and behaviour tests',
                    observations=[dict(check_id=k,passed=True,evidence_ref='executed synthetic checks',observed='passed') for k in ['compile','behaviour']])
                checked=await session.call_tool('local_worker',{'request':{'action':'record_validation','report':report}})
                self.assertEqual(checked.structuredContent['validation'],'accepted')
                bad=await session.call_tool('local_worker',{'request':{'action':'status','raw':True}})
                self.assertTrue(bad.isError)

    async def test_initialization_ping_and_status_during_delayed_discovery(self):
        from bridge.server import mcp
        self.assertIn('local_worker',[t.name for t in await mcp.list_tools()])
        w=TempWorld();self.addCleanup(w.close)
        params=StdioServerParameters(command=sys.executable,args=['-B',str(Path(__file__).with_name('local_worker_server.py')),'2'],env=dict(os.environ,USERPROFILE=str(w.home)))
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await asyncio.wait_for(session.initialize(),2)
                await asyncio.wait_for(session.send_ping(),1)
                result=await asyncio.wait_for(session.call_tool('local_worker',{'request':{'action':'status'}}),1)
                self.assertIn(result.structuredContent['availability'],['discovering','available','unavailable'])

    async def test_corrupt_worker_witness_keeps_stdio_and_architect_usable(self):
        from tests.helpers import World
        for bad in [[],None,7]:
            with self.subTest(witness=bad):
                world=World()
                try:
                    worker=world.home/'.gpt-architect-bridge-local00';worker.mkdir()
                    (worker/'instance.json').write_text(json.dumps(bad))
                    params=StdioServerParameters(command=sys.executable,args=['-B',str(Path(__file__).with_name('local_worker_server.py'))],env=world.env())
                    async with stdio_client(params) as (read,write):
                        async with ClientSession(read,write) as session:
                            await asyncio.wait_for(session.initialize(),4)
                            await asyncio.wait_for(session.send_ping(),1)
                            result=await session.call_tool('local_worker',{'request':{'action':'status'}})
                            self.assertEqual(result.structuredContent['state'],'unavailable')
                            architect=await session.call_tool('consult_architect',{'question':'ordinary v2 remains usable'})
                            self.assertFalse(architect.isError,architect.content)
                            self.assertEqual(architect.content[0].text,'FAKE ANSWER')
                finally:world.cleanup()
