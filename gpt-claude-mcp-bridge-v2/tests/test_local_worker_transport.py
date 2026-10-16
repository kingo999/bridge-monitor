import asyncio
from pathlib import Path
import sys
import time
import unittest
from tests.local_worker_helpers import HTTPFixture,TempWorld
from bridge.local_worker.models import TransportRequest
from bridge.local_worker.settings import parse_address


class Transport(unittest.IsolatedAsyncioTestCase):
    def transport(self,**kwargs):
        try:from bridge.local_worker.transport import WorkerTransport
        except ImportError:self.fail('bounded transport missing')
        return WorkerTransport(**kwargs)

    def request(self,**kwargs):
        return TransportRequest(endpoint=parse_address('skynet.local'),method='GET',path='/api/tags',**kwargs)

    async def test_http_peer_no_redirect_and_bounded_body(self):
        server=HTTPFixture({'/api/ps':(302,{},0),'/api/show':(200,b'x'*5000,0)})
        self.addCleanup(server.close)
        transport=self.transport(dial_override=server.dial)
        out=await transport.exchange(self.request())
        self.assertEqual(out.peer_ip,'127.0.0.1');self.assertEqual(out.status,200)
        out=await transport.exchange(TransportRequest(endpoint=parse_address('skynet.local'),method='GET',path='/api/ps'))
        self.assertEqual(out.status,302);self.assertEqual(len(server.requests),2)
        out=await transport.exchange(TransportRequest(endpoint=parse_address('skynet.local'),method='POST',path='/api/show',body={},max_bytes=100))
        self.assertTrue(out.truncated);self.assertIsNone(out.data)

    async def test_slow_header_timeout_kills_helper(self):
        server=HTTPFixture({'/api/tags':(200,{},2)})
        self.addCleanup(server.close)
        transport=self.transport(dial_override=server.dial)
        started=time.monotonic()
        out=await transport.exchange(self.request(timeout_s=.25))
        self.assertEqual(out.code,'transport_timeout');self.assertLess(time.monotonic()-started,1.25)
        self.assertEqual(transport.active_helpers,set())

    async def test_stalled_helper_dns_and_malformed_protocol(self):
        w=TempWorld();self.addCleanup(w.close)
        helper=w.home/'stall.py';helper.write_text('import time; time.sleep(30)')
        transport=self.transport(helper_path=helper)
        out=await transport.exchange(self.request(timeout_s=.2))
        self.assertEqual(out.code,'transport_timeout');self.assertFalse(transport.active_helpers)
        helper.write_text('print("not JSON")')
        out=await transport.exchange(self.request())
        self.assertEqual(out.code,'helper_protocol_error')

    async def test_cancelled_post_has_no_replay_and_reaps(self):
        server=HTTPFixture({'/api/generate':(200,{},3)})
        self.addCleanup(server.close)
        transport=self.transport(dial_override=server.dial)
        request=TransportRequest(endpoint=parse_address('skynet.local'),method='POST',path='/api/generate',body={'prompt':'test'})
        task=asyncio.create_task(transport.exchange(request))
        for _ in range(100):
            if server.generation_count:break
            await asyncio.sleep(.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(server.generation_count,1);self.assertFalse(transport.active_helpers)

    async def test_unapproved_path_never_spawns(self):
        transport=self.transport()
        out=await transport.exchange(TransportRequest(endpoint=parse_address('skynet.local'),method='POST',path='/api/pull',body={}))
        self.assertEqual(out.code,'transport_request_rejected');self.assertFalse(transport.active_helpers)
