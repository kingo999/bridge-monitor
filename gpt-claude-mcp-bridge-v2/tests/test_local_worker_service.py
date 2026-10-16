import asyncio
import unittest
from bridge.local_worker.models import *
from bridge.local_worker.settings import MODEL,DIGEST
from tests.local_worker_helpers import TempWorld


class FakeTransport:
    def __init__(self):self.calls=[];self.fail_hosts=set();self.gate=None
    async def exchange(self,request):
        self.calls.append(request)
        if self.gate is not None:await self.gate.wait()
        if request.endpoint.host in self.fail_hosts:return TransportOutcome(code='transport_error')
        return TransportOutcome(status=200,phase='terminal_received',peer_ip='192.168.0.78',data={'models':[{'name':MODEL,'digest':DIGEST}]})
    async def aclose(self):pass


class Discovery(unittest.IsolatedAsyncioTestCase):
    def service(self):
        try:from bridge.local_worker.service import WorkerService
        except ImportError:self.fail('optional worker service missing')
        w=TempWorld();self.addCleanup(w.close)
        transport=FakeTransport()
        return WorkerService({'USERPROFILE':str(w.home)},transport=transport),transport,w

    async def test_hostname_first_saved_peer_and_status_no_network(self):
        service,t,w=self.service()
        result=await service.discover()
        self.assertEqual(result.availability,'available')
        self.assertEqual(t.calls[0].endpoint.host,'skynet.local')
        from bridge.local_worker.settings import load_worker_settings
        self.assertEqual(load_worker_settings(w.root).last_validated_ip,'192.168.0.78')
        count=len(t.calls);await service.handle(StatusRequest(action='status'));self.assertEqual(len(t.calls),count)

    async def test_fallback_and_continue_after_both_fail(self):
        service,t,w=self.service();t.fail_hosts={'skynet.local'}
        result=await service.discover()
        self.assertEqual([q.endpoint.host for q in t.calls],['skynet.local','192.168.0.78'])
        t.fail_hosts.add('192.168.0.78')
        result=await service.discover();self.assertEqual(result.availability,'unavailable')
        self.assertEqual(len(result.next_actions),3)
        result=await service.handle(ContinueWithoutRequest(action='continue_without'))
        self.assertEqual(result.availability,'disabled')

    async def test_late_discovery_cannot_reenable_disabled_session(self):
        service,t,w=self.service();t.gate=asyncio.Event()
        discovery=asyncio.create_task(service.startup())
        await asyncio.sleep(.05)
        await service.handle(ContinueWithoutRequest(action='continue_without'))
        t.gate.set();await discovery
        result=await service.handle(StatusRequest(action='status'))
        self.assertEqual(result.availability,'disabled')
        result=await service.handle(ReconnectRequest(action='reconnect'))
        self.assertEqual(result.availability,'available')

    async def test_invalid_address_and_corrupt_config_are_optional(self):
        service,t,w=self.service()
        result=await service.handle(ReconnectRequest(action='reconnect',address='x:80'))
        self.assertEqual(result.state,'unavailable');self.assertFalse(t.calls)
        await service.discover();(w.root/'config.json').write_text('{')
        result=await service.handle(ReconnectRequest(action='reconnect'))
        self.assertEqual(result.code,'worker_config_invalid')

    async def test_preconfigured_first_start_is_supported(self):
        service,t,w=self.service();w.root.mkdir()
        (w.root/'config.json').write_text('{"schema_version":1,"initial_ip":null}')
        result=await service.handle(ReconnectRequest(action='reconnect'))
        self.assertEqual(result.availability,'available',result.model_dump())

    async def test_status_remains_discovering_during_first_store_creation(self):
        import time
        service,t,w=self.service()
        original=service.store._establish
        def delayed():time.sleep(.2);original()
        service.store._establish=delayed
        task=asyncio.create_task(service.startup())
        await asyncio.sleep(.05)
        status=await service.handle(StatusRequest(action='status'))
        await task
        self.assertEqual(status.availability,'discovering',status.model_dump())

    async def test_queued_reconnect_cannot_undo_newer_continue_without(self):
        service,t,w=self.service();t.gate=asyncio.Event()
        startup=asyncio.create_task(service.startup());await asyncio.sleep(.05)
        queued=asyncio.create_task(service.handle(ReconnectRequest(action='reconnect')))
        await asyncio.sleep(.05)
        await service.handle(ContinueWithoutRequest(action='continue_without'))
        t.gate.set();await startup;await queued
        self.assertTrue(service.disabled,'queued older reconnect re-enabled a newer disabled session')
        await service.handle(ReconnectRequest(action='reconnect'))
        self.assertFalse(service.disabled)

    async def test_continue_before_startup_stays_disabled(self):
        service,t,w=self.service()
        await service.handle(ContinueWithoutRequest(action='continue_without'))
        await service.startup()
        self.assertTrue(service.disabled,'automatic startup must never re-enable the session')

    async def test_corrupt_witness_shapes_do_not_escape_startup(self):
        import json
        for value in [[],None,1,'invalid',{'schema_version':True}]:
            service,t,w=self.service();w.root.mkdir()
            (w.root/'instance.json').write_text(json.dumps(value))
            await service.startup()
            result=await service.handle(StatusRequest(action='status'))
            self.assertEqual(result.state,'unavailable')
