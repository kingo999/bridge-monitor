import asyncio
import copy
import unittest
from bridge.local_worker.models import *
from bridge.local_worker.settings import MODEL,DIGEST,parse_address
from bridge.local_worker.storage import WorkerStore
from bridge.local_worker.transport import WorkerTransport
from bridge.local_worker import _baseline
from tests.local_worker_helpers import TempWorld,HTTPFixture,packet_data


class Generation(unittest.IsolatedAsyncioTestCase):
    def setup_runner(self,reply=None):
        try:from bridge.local_worker.generation import GenerationRunner
        except ImportError:self.fail('audited generation transaction missing')
        w=TempWorld();self.addCleanup(w.close)
        store=WorkerStore(w.root,(),w.home)
        reply=reply or dict(model=MODEL,response='def stable_unique(items): return list(dict.fromkeys(items or []))',done=True,done_reason='stop',prompt_eval_count=200,eval_count=20,total_duration=1000000000)
        server=HTTPFixture({'/api/show':(200,_baseline.METADATA,0),'/api/version':(200,{'version':_baseline.VERSION},0),'/api/generate':(200,reply,0)})
        self.addCleanup(server.close)
        runner=GenerationRunner(store,WorkerTransport(dial_override=server.dial))
        request=GenerateRequest(action='generate',packet=TaskPacket(**packet_data()),attempt=1)
        return runner,store,server,request

    async def test_one_post_with_full_audit_and_idempotent_replay(self):
        runner,store,server,request=self.setup_runner()
        first=await runner.run(request,parse_address('skynet.local'))
        self.assertEqual(first.state,'draft_returned',first.model_dump())
        self.assertEqual(first.validation,'pending')
        self.assertEqual(first.metrics['total_duration_s'],1.0)
        second=await runner.run(request,parse_address('192.168.0.78'))
        self.assertEqual(server.generation_count,1);self.assertTrue(second.replayed_from_ledger)
        self.assertEqual(first.result_digest,second.result_digest)
        import json
        sent=[body for method,path,body in server.requests if path=='/api/generate'][0]
        self.assertEqual(sent['options'],dict(num_ctx=16384,num_predict=2048,temperature=0))
        self.assertNotIn('system',sent);self.assertNotIn('raw',sent)
        self.assertTrue(list((store.root/'audit').glob('*/request.json')))
        self.assertTrue(list((store.root/'audit').glob('*/response.json')))
        changed=request.model_copy(update={'packet':request.packet.model_copy(update={'source':'other'})})
        result=await runner.run(changed,parse_address('skynet.local'))
        self.assertEqual(result.code,'task_conflict');self.assertEqual(server.generation_count,1)

    async def test_plain_code_unwraps_one_fence_and_preserves_raw_audit_and_replay(self):
        raw='```python\ndef clamp(value, lower, upper):\n    return max(lower, min(value, upper))\n```'
        reply=dict(model=MODEL,response=raw,done=True,done_reason='stop',prompt_eval_count=100)
        runner,store,server,request=self.setup_runner(reply)
        request.packet.output_form='plain_code'
        result=await runner.run(request,parse_address('skynet.local'))
        expected='def clamp(value, lower, upper):\n    return max(lower, min(value, upper))\n'
        self.assertEqual(result.state,'draft_returned',result.model_dump())
        self.assertEqual(result.response,expected)
        compile(result.response,'worker.py','exec')
        audit=store.read(f'audit/{result.request_id}/response.json')
        self.assertEqual(audit['transport']['data']['response'],raw)
        normalized=store.read(f'audit/{result.request_id}/normalization.json')
        self.assertEqual(normalized['returned_response'],expected)
        self.assertEqual(normalized['raw_result_digest'],content_hash(reply))
        self.assertEqual(normalized['result_digest'],result.result_digest)
        self.assertNotEqual(result.result_digest,content_hash(reply))
        self.assertIn(f'audit/{result.request_id}/normalization.json',result.audit_refs)
        self.assertIn('outer',result.explanation)
        again=await runner.run(request,parse_address('skynet.local'))
        self.assertEqual(again.response,expected)
        self.assertEqual(again.result_digest,result.result_digest)
        self.assertTrue(again.replayed_from_ledger)
        self.assertEqual(server.generation_count,1)

    async def test_plain_code_does_not_rewrite_bare_source_or_generic_output(self):
        source='def example():\n    return "```python"\n'
        for output_form,raw in [('plain_code',source),('One replacement function.',f'```python\n{source}```')]:
            with self.subTest(output_form=output_form):
                reply=dict(model=MODEL,response=raw,done=True,done_reason='stop',prompt_eval_count=100)
                runner,store,server,request=self.setup_runner(reply)
                request.packet.output_form=output_form
                result=await runner.run(request,parse_address('skynet.local'))
                self.assertEqual(result.state,'draft_returned',result.model_dump())
                self.assertEqual(result.response,raw)
                self.assertEqual(result.result_digest,content_hash(reply))
                self.assertFalse((store.root/f'audit/{result.request_id}/normalization.json').exists())

    async def test_plain_code_preserves_payload_bytes_for_matching_fence_styles(self):
        for raw,expected in [('~~~python\r\nx = 1\r\n~~~','x = 1\r\n'),
                             ('\n````c++\n  x();\n````\n\n','  x();\n')]:
            with self.subTest(raw=raw):
                reply=dict(model=MODEL,response=raw,done=True,done_reason='stop',prompt_eval_count=100)
                runner,store,server,request=self.setup_runner(reply)
                request.packet.output_form='plain_code'
                result=await runner.run(request,parse_address('skynet.local'))
                self.assertEqual(result.state,'draft_returned',result.model_dump())
                self.assertEqual(result.response,expected)

    async def test_plain_code_audit_failure_cannot_return_a_successful_normalized_draft(self):
        from unittest.mock import patch
        reply=dict(model=MODEL,response='```python\nx = 1\n```',done=True,done_reason='stop',prompt_eval_count=100)
        runner,store,server,request=self.setup_runner(reply)
        request.packet.output_form='plain_code'
        with patch.object(runner.audit,'write_normalization',side_effect=OSError('disk full')):
            result=await runner.run(request,parse_address('skynet.local'))
        self.assertEqual(result.state,'incomplete_error',result.model_dump())
        self.assertEqual(result.code,'audit_or_state_error')
        self.assertEqual(result.response,reply['response'])
        replay=await runner.run(request,parse_address('skynet.local'))
        self.assertTrue(replay.replayed_from_ledger)
        self.assertEqual(server.generation_count,1)

    async def test_plain_code_cannot_hide_a_clarification_inside_a_fence(self):
        reply=dict(model=MODEL,response='```text\nNEEDS_CONTEXT: caller?\n```',done=True,done_reason='stop',prompt_eval_count=100)
        runner,store,server,request=self.setup_runner(reply)
        request.packet.output_form='plain_code'
        result=await runner.run(request,parse_address('skynet.local'))
        self.assertEqual(result.code,'worker_clarification')
        self.assertEqual(result.state,'incomplete_error')
        self.assertTrue(result.needs_context)

    async def test_plain_code_rejects_ambiguous_or_incomplete_fences_without_retry(self):
        cases=['Here is the fix:\n```python\nx = 1\n```',
               '```python\nx = 1\n```\nExplanation follows.',
               '```python\nx = 1\n```\n```python\ny = 2\n```',
               '```python\nx = 1', '```python\n```']
        for raw in cases:
            with self.subTest(raw=raw):
                reply=dict(model=MODEL,response=raw,done=True,done_reason='stop',prompt_eval_count=100)
                runner,store,server,request=self.setup_runner(reply)
                request.packet.output_form='plain_code'
                result=await runner.run(request,parse_address('skynet.local'))
                self.assertEqual(result.state,'incomplete_error',result.model_dump())
                self.assertEqual(result.code,'plain_code_invalid_envelope')
                self.assertEqual(result.response,raw)
                self.assertEqual(result.coordination,'idle')
                self.assertEqual(server.generation_count,1)
                self.assertIsNone(store.active())

    async def test_malformed_length_clarification_and_metric_failures(self):
        base=dict(model=MODEL,response='answer',done=True,done_reason='stop',prompt_eval_count=50)
        cases=[({'done_reason':'length'},'completion_incomplete'),({'response':''},'completion_incomplete'),({'model':'other'},'completion_incomplete'),({'prompt_eval_count':True},'metrics_invalid'),({'response':'NEEDS_CONTEXT: caller?'},'worker_clarification')]
        for change,code in cases:
            with self.subTest(change=change):
                runner,store,server,request=self.setup_runner(base|change)
                result=await runner.run(request,parse_address('skynet.local'))
                self.assertEqual(result.state,'incomplete_error',result.model_dump())
                self.assertEqual(result.code,code)

    async def test_prompt_count_contradiction_blocks_following_task(self):
        runner,store,server,request=self.setup_runner(dict(model=MODEL,response='answer',done=True,done_reason='stop',prompt_eval_count=99999))
        result=await runner.run(request,parse_address('skynet.local'))
        self.assertEqual(result.code,'prompt_budget_contradiction')
        other=request.model_copy(update={'packet':TaskPacket(**packet_data('other'))})
        again=await runner.run(other,parse_address('skynet.local'))
        self.assertEqual(again.code,'budget_blocked');self.assertEqual(server.generation_count,1)

    async def test_busy_resident_model_or_metadata_drift_never_generates(self):
        runner,store,server,request=self.setup_runner()
        server.routes['/api/ps']=(200,{'models':[{'name':'another','digest':'x'}]},0)
        result=await runner.run(request,parse_address('skynet.local'))
        self.assertEqual(result.state,'busy');self.assertEqual(server.generation_count,0)
        server.routes['/api/ps']=(200,{'models':[]},0)
        server.routes['/api/show']=(200,_baseline.METADATA|{'system':'drift'},0)
        result=await runner.run(request,parse_address('skynet.local'))
        self.assertEqual(result.code,'budget_unverifiable');self.assertEqual(server.generation_count,0)

    async def test_timeout_keeps_uncertainty_across_restart(self):
        runner,store,server,request=self.setup_runner()
        from bridge.local_worker.models import TransportOutcome
        original=runner.transport.exchange
        async def dropped(req):
            if req.path=='/api/generate':return TransportOutcome(code='transport_timeout',phase='may_have_started')
            return await original(req)
        runner.transport.exchange=dropped
        result=await runner.run(request,parse_address('skynet.local'))
        self.assertEqual(result.coordination,'uncertain')
        other=request.model_copy(update={'packet':TaskPacket(**packet_data('other'))})
        result=await runner.run(other,parse_address('skynet.local'))
        self.assertEqual(result.code,'worker_uncertain')

    async def test_anyio_cancellation_releases_lock_but_retains_uncertainty(self):
        import anyio
        runner,store,server,request=self.setup_runner()
        server.routes['/api/generate']=(200,{},3)
        async with anyio.create_task_group() as group:
            group.start_soon(runner.run,request,parse_address('skynet.local'))
            for _ in range(200):
                if server.generation_count:break
                await anyio.sleep(.01)
            self.assertEqual(server.generation_count,1)
            group.cancel_scope.cancel()
        self.assertEqual(store.read_snapshot().coordination,'uncertain')
        with store.try_lock():self.assertIsNotNone(store.active())

    async def test_stale_endpoint_selection_prevents_any_post(self):
        runner,store,server,request=self.setup_runner()
        with store.try_lock():
            snapshot=store.read_snapshot();snapshot.endpoint=parse_address('new-host')
            snapshot.availability='available';snapshot.connection_revision=2
            store.write('snapshot.json',snapshot)
        result=await runner.run(request,parse_address('old-host'))
        self.assertEqual(result.code,'endpoint_selection_changed',result.model_dump())
        self.assertEqual(server.generation_count,0)

    async def test_same_hostname_with_changed_selection_revision_refuses(self):
        runner,store,server,request=self.setup_runner()
        endpoint=parse_address('skynet.local')
        with store.try_lock():
            snapshot=store.read_snapshot();snapshot.endpoint=endpoint
            snapshot.availability='available';snapshot.connection_revision=3
            store.write('snapshot.json',snapshot)
        result=await runner.run(request,endpoint,selection_revision=2)
        self.assertEqual(result.code,'endpoint_selection_changed')
        self.assertEqual(server.generation_count,0)
