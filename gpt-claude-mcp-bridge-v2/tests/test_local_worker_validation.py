import unittest
from bridge.local_worker.models import *
from bridge.local_worker.settings import parse_address
from tests import test_local_worker_generation as generation_tests


class Validation(unittest.IsolatedAsyncioTestCase):
    async def seed(self):
        try:from bridge.local_worker.validation import ValidationRecorder
        except ImportError:self.fail('supervisor validation and retry enforcement missing')
        runner,store,server,request=generation_tests.Generation.setup_runner(self)
        result=await runner.run(request,parse_address('skynet.local'))
        return ValidationRecorder(store),runner,store,server,request,result

    def report(self,result,verdict='accepted',**kwargs):
        observations=[CheckObservation(check_id=key,passed=verdict=='accepted',evidence_ref='executed-tests',observed='passed' if verdict=='accepted' else 'None raised TypeError') for key in ['compile','behaviour']]
        return ValidationReport(task_id=result.task_id,attempt=result.attempt,packet_hash=result.packet_hash,result_digest=result.result_digest,
            verdict=verdict,observations=observations,toolchain_ref='Python 3.13 independent candidate tests',**kwargs)

    async def test_compiler_only_cannot_accept_and_no_commands_execute(self):
        recorder,runner,store,server,request,result=await self.seed()
        report=self.report(result);report.observations=report.observations[:1]
        with self.assertRaises(WorkerError) as caught:recorder.record(report)
        self.assertEqual(caught.exception.code,'validation_evidence_incomplete')
        accepted=recorder.record(self.report(result))
        self.assertEqual(accepted.validation,'accepted');self.assertEqual(accepted.evidence_origin,'supervisor_reported')
        audit=store.read(f'audit/{result.request_id}/metadata.json')
        self.assertIn('timings',audit,'validation must retain measured generation timing')
        recorder.record(self.report(result));self.assertEqual(len(store.read_task('sample').validations),1)
        retry=request.model_copy(update={'attempt':2,'feedback_ref':result.result_digest})
        rejected=await runner.run(retry,parse_address('skynet.local'))
        self.assertEqual(rejected.code,'retry_not_eligible');self.assertEqual(server.generation_count,1)

    async def test_one_exact_failure_retry_survives_restarts(self):
        recorder,runner,store,server,request,result=await self.seed()
        premature=await runner.run(request.model_copy(update={'attempt':2,'feedback_ref':result.result_digest}),parse_address('skynet.local'))
        self.assertEqual(premature.code,'retry_not_eligible')
        failed=self.report(result,'failed',diagnostics='input None: expected [], actual TypeError')
        recorder.record(failed)
        retry=request.model_copy(update={'attempt':2,'feedback_ref':result.result_digest})
        retried=await runner.run(retry,parse_address('skynet.local'))
        self.assertEqual(retried.state,'draft_returned',retried.model_dump())
        self.assertEqual(server.generation_count,2)
        replay=await runner.run(retry,parse_address('skynet.local'))
        self.assertTrue(replay.replayed_from_ledger);self.assertEqual(server.generation_count,2)
        sent=[body['prompt'] for method,path,body in server.requests if path=='/api/generate'][-1]
        self.assertIn(failed.diagnostics,sent)

    async def test_wrong_result_or_checks_cannot_change_verdict(self):
        recorder,runner,store,server,request,result=await self.seed()
        for report in [self.report(result).model_copy(update={'result_digest':'e'*64}),self.report(result).model_copy(update={'observations':[]})]:
            with self.assertRaises(WorkerError):recorder.record(report)
        accepted=recorder.record(self.report(result,supervisor_edits='fixed result manually'))
        self.assertEqual(accepted.code,'supervisor_corrected')
        self.assertEqual(server.generation_count,1)

    async def test_replayed_old_report_cannot_reverse_newer_correction(self):
        recorder,runner,store,server,request,result=await self.seed()
        accepted=self.report(result)
        failed=self.report(result,'failed',diagnostics='later independent check found wrong result')
        recorder.record(accepted);recorder.record(failed)
        replay=recorder.record(accepted)
        self.assertEqual(replay.validation,'failed')
        reloaded=store.read_task('sample')
        self.assertEqual(reloaded.validations[-1].verdict,'failed')
        self.assertEqual(reloaded.attempts['1'].result.validation,'failed')
        self.assertEqual(len(reloaded.validations),2)
        generated=await runner.run(request,parse_address('skynet.local'))
        self.assertEqual(generated.validation,'failed')
