"""End-to-end FLOW-01 fixtures: the real bridge functions against mocked Claude and isolated stores.

No Claude CLI is invoked and no signed-in account is used. Each test gets a temporary Git
repository and a temporary receipt store; `subprocess.Popen` is intercepted only for the fake
`claude.exe` command, every other process (git) is real.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import implementer_bridge as bridge
import receipts
import source_snapshot as snap

REAL_POPEN = subprocess.Popen
FAKE_CLI = 'claude.exe'
APPROVAL = 'Kian, 2026-10-14, test approval recorded in the contract'
BOUND_PATHS = ['ignored/notes.txt']


def git(root, *args):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    '-c', 'commit.gpgsign=false', *args],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)


def only_linked(*paths):
    wanted = {os.path.normcase(str(path)) for path in paths}
    return lambda path: os.path.normcase(str(path)) in wanted


def result_json(**changes):
    value = {'type': 'result', 'is_error': False, 'result': 'RESULT-MARKER', 'session_id': 'session-1',
             'permission_denials': [], 'usage': {'input_tokens': 5, 'output_tokens': 7},
             'modelUsage': {'claude-sonnet-5-5': {}}}
    value.update(changes)
    return json.dumps(value)


class FlowCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.root = self.parent / 'repo'
        self.root.mkdir()
        git(self.root, 'init', '-q')
        (self.root / '.gitignore').write_bytes(b'ignored/\n')
        (self.root / 'tracked.txt').write_bytes(b'one\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'base')
        (self.root / 'ignored').mkdir()
        (self.root / 'ignored' / 'notes.txt').write_bytes(b'notes\n')
        self.head = subprocess.run(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], capture_output=True,
                                   text=True, stdin=subprocess.DEVNULL).stdout.strip()
        store_base = self.parent / 'store'
        store_base.mkdir()
        self.store = receipts.ReceiptStore(store_base)
        for patcher in (patch.object(bridge, 'STORE', self.store), patch.object(bridge, 'RECOVERED', False),
                        patch.dict(bridge.JOBS, {}, clear=True),
                        patch.dict(os.environ, {'CLAUDE_IMPLEMENTER_ROOT': str(self.root)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.launched = []

    # -- helpers

    def claude(self, stdout=None, returncode=0, edit=None, timeout=False, popen_error=None):
        """Patch the CLI lookup and Popen so only the fake `claude.exe` is intercepted."""
        process = Mock()
        process.returncode = returncode
        process.pid = 1
        process.poll.return_value = None if timeout else returncode
        calls = {'count': 0}

        def communicate(*args, **kwargs):
            calls['count'] += 1
            if calls['count'] == 1:
                if edit:
                    edit()
                if timeout:
                    raise subprocess.TimeoutExpired(FAKE_CLI, 1)
            return (result_json() if stdout is None else stdout, 'SECRET stderr')

        process.communicate.side_effect = communicate

        def popen(command, *args, **kwargs):
            if command and command[0] == FAKE_CLI:
                self.launched.append(list(command))
                if popen_error:
                    raise popen_error
                return process
            return REAL_POPEN(command, *args, **kwargs)

        class Patches:
            def __enter__(inner):
                inner.stack = [patch.object(bridge.shutil, 'which', return_value=FAKE_CLI),
                               patch.object(bridge.subprocess, 'Popen', side_effect=popen)]
                if timeout:
                    inner.stack.append(patch.object(bridge, 'stop_tree', Mock()))
                for item in inner.stack:
                    item.start()

            def __exit__(inner, *exc):
                for item in reversed(inner.stack):
                    item.stop()

        return Patches()

    def wait(self, job_id, seconds=30):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if bridge.JOBS[job_id]['status'] != 'running':
                return bridge.JOBS[job_id]
            time.sleep(0.02)
        self.fail('the mocked job did not finish')

    def run_legacy(self, **claude_options):
        with self.claude(**claude_options):
            started = bridge.implement_changes('task text', 'scope text', 'criteria text')
            return started, self.wait(started['job_id'])

    def durable(self, job_id):
        result = self.store.read(job_id)
        self.assertEqual(result.status, 'ok', result.reason)
        return result.record

    def write_contract(self, status='Approved', approval=APPROVAL, base=None, name='docs/contract.md'):
        (self.root / 'docs').mkdir(exist_ok=True)
        text = (f'# FLOW-01 Test Contract\n\n- Status: {status}\n- Approval: {approval}\n'
                f'- Base revision: {base or self.head}\n\n## Objective\nText.\n')
        (self.root / name).write_bytes(text.encode('utf-8'))
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    def binding(self, **changes):
        sha = self.write_contract()
        value = {'task_id': 'FLOW-01', 'contract_path': 'docs/contract.md', 'contract_sha256': sha,
                 'approval': APPROVAL, 'base_revision': self.head, 'source_paths': list(BOUND_PATHS),
                 'source_digest': bridge.capture_source_state(BOUND_PATHS)['digest']}
        value.update(changes)
        return value

    def assert_lock_free(self):
        probe = self.store.lock('active-job')
        self.assertTrue(probe.try_acquire(), 'the active-job lock was left held')
        probe.release()
        self.assertFalse(bridge.ACTIVE_LOCKS)

    def assert_nothing_launched(self):
        self.assertEqual(self.launched, [])
        self.assertEqual(bridge.JOBS, {})
        self.assertEqual(self.store.list()['receipts'], [])
        self.assert_lock_free()


class LegacyFlowTests(FlowCase):
    def test_legacy_dispatch_status_and_content_free_receipt(self):
        started, job = self.run_legacy()
        self.assertEqual((started['status'], started['binding']), ('running', 'unbound'))
        self.assertEqual(started['receipt'], {'recorded': True, 'state': 'running'})
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['receipt'], {'recorded': True, 'state': 'completed'})
        self.assertEqual(job['session_id'], 'session-1')
        status = bridge.implementation_status(started['job_id'])
        self.assertEqual(status['status'], 'completed')
        self.assertEqual(status['human_acceptance'], {'state': 'not_recorded'})
        record = self.durable(started['job_id'])
        self.assertEqual((record['state'], record['binding'], record['contract'], record['task_id']),
                         ('completed', 'unbound', None, None))
        self.assertEqual(record['outcome']['code'], 'success')
        self.assertEqual(record['source_before']['state'], 'captured')
        self.assertEqual(record['source_after']['state'], 'captured')
        self.assertEqual(record['model'], {'reported': ['claude-sonnet-5-5'], 'selection_verified': False})
        raw = self.store.path_for(started['job_id']).read_bytes().decode('utf-8')
        for forbidden in ('task text', 'scope text', 'criteria text', 'RESULT-MARKER', 'SECRET'):
            self.assertNotIn(forbidden, raw)
        self.assertNotIn('--resume', self.launched[0])
        self.assert_lock_free()

    def test_terminal_outcomes_are_recorded_with_sanitized_codes(self):
        denials = result_json(permission_denials=[{'tool_name': 'Bash', 'tool_input': {'command': 'SECRET'}}])
        cases = (('completed', None, 0, False, 'completed', 'success'),
                 ('needs_attention', denials, 0, False, 'needs_attention', 'permission_denials'),
                 ('failed', result_json(is_error=True), 0, False, 'failed', 'cli_error'),
                 ('nonzero', result_json(), 3, False, 'failed', 'nonzero_exit'),
                 ('malformed', 'this is not json', 0, False, 'failed', 'no_valid_result'),
                 ('timeout', '', 0, True, 'timed_out', 'timeout'))
        for label, stdout, returncode, timeout, state, code in cases:
            with self.subTest(label):
                edit = (lambda: (self.root / 'tracked.txt').write_bytes(b'partial edit\n')) if timeout else None
                started, job = self.run_legacy(stdout=stdout, returncode=returncode, timeout=timeout, edit=edit)
                self.assertEqual(job['status'], state)
                record = self.durable(started['job_id'])
                self.assertEqual((record['state'], record['outcome']['code']), (state, code))
                self.assertIsNotNone(record['finished_at'])
                if label == 'needs_attention':
                    self.assertEqual(record['outcome']['denied_tools'], ['Bash'])
                    self.assertNotIn('SECRET', self.store.path_for(started['job_id']).read_text('utf-8'))
                if timeout:  # partial changes stay visible as differing before/after evidence
                    self.assertNotEqual(record['source_before']['digest'], record['source_after']['digest'])
                self.assert_lock_free()

    def test_worker_failures_are_recorded_not_lost(self):
        started, job = self.run_legacy(popen_error=OSError('cannot start'))
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(self.durable(started['job_id'])['outcome']['code'], 'bridge_exception')
        with patch.object(bridge.shutil, 'which', return_value=None):
            started = bridge.implement_changes('task', 'scope', 'criteria')
            self.wait(started['job_id'])
        self.assertEqual(self.durable(started['job_id'])['outcome']['code'], 'cli_unavailable')
        self.assert_lock_free()

    def test_initial_persistence_failure_launches_nothing(self):
        with self.claude(), patch.object(self.store, 'create', side_effect=receipts.ReceiptError('write_failed')):
            with self.assertRaises(ValueError) as caught:
                bridge.implement_changes('task', 'scope', 'criteria')
        self.assertIn('nothing was launched', str(caught.exception))
        self.assert_nothing_launched()

    def test_final_persistence_failure_is_uncertain_never_completed(self):
        with self.claude(), patch.object(self.store, 'update', side_effect=receipts.ReceiptError('write_failed')):
            started = bridge.implement_changes('task', 'scope', 'criteria')
            job = self.wait(started['job_id'])
        self.assertEqual(job['status'], 'persistence_error')
        self.assertEqual(job['receipt'], {'recorded': False, 'reason': 'write_failed',
                                          'reported_outcome': 'completed'})
        self.assertIn('uncertain', job['message'])
        self.assertEqual(self.durable(started['job_id'])['state'], 'running')  # authoritative record untouched
        self.assert_lock_free()

    def test_cached_completed_job_with_lost_or_corrupt_receipt_is_uncertain(self):
        started, _ = self.run_legacy()
        job_id = started['job_id']
        path = self.store.path_for(job_id)
        good = path.read_bytes()
        for label, damage, reason in (('corrupt', lambda: path.write_bytes(b'{not json'), 'malformed'),
                                      ('missing', lambda: os.unlink(path), 'missing')):
            with self.subTest(label):
                damage()
                status = bridge.implementation_status(job_id)
                self.assertEqual(status['status'], 'receipt_unavailable')
                self.assertEqual(status['reported_status'], 'completed')
                self.assertEqual(status['durable_assurance'], 'uncertain')
                self.assertEqual(status['receipt'], {'recorded': False, 'reason': reason,
                                                     'reported_outcome': 'completed'})
                self.assertNotIn('evidence', status)
        path.write_bytes(good)
        self.assertEqual(bridge.implementation_status(job_id)['status'], 'completed')

    def test_unknown_and_invalid_ids_do_not_leak(self):
        self.assertEqual(bridge.implementation_status('missing-job'), {'status': 'unknown_job'})
        self.assertEqual(bridge.implementation_status('../x'), {'status': 'unknown_job'})


class RestartAndResumeTests(FlowCase):
    def dead_receipt(self, job_id='dead-job'):
        state = bridge.repository_state()
        record = receipts.new_record(job_id, 'other-instance',
                                     {'root': state['root'], 'branch': state['branch'], 'head': state['head']},
                                     source_paths=[])
        self.store.create(record)
        return job_id

    def test_restart_marks_interrupted_and_never_relaunches(self):
        job_id = self.dead_receipt()
        with self.claude():
            status = bridge.implementation_status(job_id)
            listing = bridge.list_receipts()
        self.assertEqual(status['status'], 'interrupted')
        self.assertIn('Nothing was resumed or relaunched', status['message'])
        record = self.durable(job_id)
        self.assertEqual((record['state'], record['note']), ('interrupted', 'interrupted_owner_not_running'))
        self.assertEqual(record['source_at_recovery']['state'], 'captured')
        self.assertEqual([item['state'] for item in listing['receipts']], ['interrupted'])
        self.assertEqual(self.launched, [])

    def test_receipt_owned_by_a_live_other_bridge_is_not_marked_interrupted(self):
        job_id = self.dead_receipt()
        other = self.store.lock('active-job')
        self.assertTrue(other.try_acquire())
        try:
            with self.claude():
                status = bridge.implementation_status(job_id)
                self.assertEqual(status['status'], 'running')
                self.assertEqual(self.durable(job_id)['state'], 'running')
                with self.assertRaises(ValueError) as caught:
                    bridge.implement_changes('task', 'scope', 'criteria')
            self.assertIn('Another bridge instance', str(caught.exception))
            self.assertEqual(self.launched, [])
            self.assertEqual(self.durable(job_id)['state'], 'running')
        finally:
            other.release()
        self.assertFalse(bridge.ACTIVE_LOCKS)

    def test_deliberate_resume_and_stale_source_refusal(self):
        first, _ = self.run_legacy()
        bridge.JOBS.clear()  # simulate a bridge restart: only durable receipts remain
        with self.claude():
            second = bridge.implement_changes('fix it', 'scope', 'criteria', first['job_id'])
            self.wait(second['job_id'])
        self.assertEqual(self.launched[-1][self.launched[-1].index('--resume') + 1], 'session-1')
        self.assertEqual(self.durable(second['job_id'])['parent_job_id'], first['job_id'])
        before = len(self.launched)
        (self.root / 'tracked.txt').write_bytes(b'changed after the job\n')
        bridge.JOBS.clear()
        with self.claude(), self.assertRaises(ValueError) as caught:
            bridge.implement_changes('again', 'scope', 'criteria', second['job_id'])
        self.assertIn('Source evidence changed', str(caught.exception))
        self.assertEqual(len(self.launched), before)
        self.assert_lock_free()

    def test_resume_of_unfinished_or_unknown_job_is_refused(self):
        job_id = self.dead_receipt()
        for target in (job_id, 'no-such-job', '../x'):
            with self.subTest(target), self.claude(), self.assertRaises(ValueError):
                bridge.implement_changes('task', 'scope', 'criteria', target)
        self.assertEqual(self.launched, [])
        self.assert_lock_free()


class BoundFlowTests(FlowCase):
    def test_bound_job_records_contract_and_source_evidence(self):
        binding = self.binding()
        with self.claude():
            started = bridge.implement_changes('task', 'scope', 'criteria', contract=binding)
            self.wait(started['job_id'])
        self.assertEqual(started['binding'], 'bound')
        record = self.durable(started['job_id'])
        self.assertEqual(record['task_id'], 'FLOW-01')
        self.assertEqual(record['contract'], {'path': 'docs/contract.md', 'sha256': binding['contract_sha256'],
                                              'approval': APPROVAL, 'base_revision': self.head})
        self.assertEqual(record['source_before']['digest'], binding['source_digest'])
        self.assertEqual(record['source_paths'], BOUND_PATHS)

    def test_rejections_happen_before_launch_and_release_the_lock(self):
        good = self.binding()
        cases = {
            'proposed': lambda: (self.write_contract(status='Proposed'),
                                 dict(good, contract_sha256=self.write_contract(status='Proposed')))[1],
            'pending approval': lambda: dict(good, contract_sha256=self.write_contract(approval='pending'),
                                             approval='pending'),
            'approval mismatch': lambda: dict(good, approval='someone else, 2026-10-14'),
            'base revision': lambda: dict(good, base_revision='d' * 40),
            'contract digest': lambda: dict(good, contract_sha256='c' * 64),
            'source digest': lambda: dict(good, source_digest='e' * 64),
            'task id': lambda: dict(good, task_id='FLOW-02'),
            'unsafe contract path': lambda: dict(good, contract_path='../contract.md'),
            'shape': lambda: {'task_id': 'FLOW-01'},
        }
        for label, build in cases.items():
            with self.subTest(label):
                self.write_contract()
                contract = build()
                with self.claude(), self.assertRaises(ValueError):
                    bridge.implement_changes('task', 'scope', 'criteria', contract=contract)
                self.assert_nothing_launched()

    def test_source_changed_after_capture_is_refused(self):
        binding = self.binding()
        (self.root / 'ignored' / 'notes.txt').write_bytes(b'edited after capture\n')
        with self.claude(), self.assertRaises(ValueError):
            bridge.implement_changes('task', 'scope', 'criteria', contract=binding)
        self.assert_nothing_launched()

    def test_head_moved_since_base_revision_is_refused(self):
        binding = self.binding()
        git(self.root, 'commit', '-q', '--allow-empty', '-m', 'moved')
        with self.claude(), self.assertRaises(ValueError):
            bridge.implement_changes('task', 'scope', 'criteria', contract=binding)
        self.assertEqual(self.launched, [])

    def test_all_preflight_runs_while_the_cross_process_lock_is_held(self):
        binding = self.binding()
        observed = []
        real_validate = bridge.contract_binding.validate
        real_state = bridge.repository_state

        def held_now():
            probe = self.store.lock('active-job')
            acquired = probe.try_acquire()
            if acquired:
                probe.release()
            return not acquired

        def spy_validate(*args, **kwargs):
            observed.append(('contract', held_now()))
            return real_validate(*args, **kwargs)

        def spy_state(*args, **kwargs):
            observed.append(('repository', held_now()))
            return real_state(*args, **kwargs)

        with self.claude(), patch.object(bridge.contract_binding, 'validate', side_effect=spy_validate), \
                patch.object(bridge, 'repository_state', side_effect=spy_state):
            started = bridge.implement_changes('task', 'scope', 'criteria', contract=binding)
            self.wait(started['job_id'])
        self.assertEqual(observed[:2], [('repository', True), ('contract', True)])

    def test_another_instance_holding_the_lock_blocks_preflight_and_launch(self):
        binding = self.binding()
        other = self.store.lock('active-job')
        self.assertTrue(other.try_acquire())
        try:
            with self.claude(), patch.object(bridge.contract_binding, 'validate') as validate:
                with self.assertRaises(ValueError):
                    bridge.implement_changes('task', 'scope', 'criteria', contract=binding)
            validate.assert_not_called()
        finally:
            other.release()
        self.assertEqual(self.launched, [])
        self.assertEqual(bridge.JOBS, {})


class ReviewFlowTests(FlowCase):
    def finished_bound_job(self):
        binding = self.binding()
        with self.claude():
            started = bridge.implement_changes('task', 'scope', 'criteria', contract=binding)
            self.wait(started['job_id'])
        return started['job_id']

    def review(self, job_id, digest, verdict='changes_requested'):
        return bridge.record_review(job_id, verdict, concerns=[{'id': 'C-1', 'disposition': 'open'}],
                                    verification=[{'command': 'python -B -m unittest', 'outcome': 'passed',
                                                   'exit_code': 0}],
                                    reviewer='codex', reviewed_source_digest=digest)

    def test_review_requires_an_explicit_matching_digest(self):
        job_id = self.finished_bound_job()
        for digest in ('', 'short', 'A' * 64, None):
            with self.subTest(repr(digest)), self.assertRaises(ValueError):
                self.review(job_id, digest)
        with self.assertRaises(ValueError):
            self.review(job_id, 'f' * 64)  # well-formed but not the current source
        self.assertEqual(self.durable(job_id)['reviews'], [])

    def test_reviews_are_append_only_and_go_stale_on_change(self):
        job_id = self.finished_bound_job()
        digest = bridge.capture_source_state(BOUND_PATHS)['digest']
        first = self.review(job_id, digest)
        self.assertEqual(first['review_count'], 1)
        self.assertEqual(first['human_acceptance'], {'state': 'not_recorded'})
        status = bridge.implementation_status(job_id)
        self.assertEqual(status['status'], 'completed')  # implementation completion stays separate
        self.assertEqual(status['evidence']['reviews'], [{'review_id': 'r1', 'stale': False, 'reasons': []}])
        (self.root / 'ignored' / 'notes.txt').write_bytes(b'changed after review\n')
        stale = bridge.implementation_status(job_id)['evidence']['reviews'][0]
        self.assertEqual((stale['stale'], stale['reasons']), (True, ['source_changed']))
        with self.assertRaises(ValueError):
            self.review(job_id, digest)  # the old digest no longer matches current source
        fresh = bridge.capture_source_state(BOUND_PATHS)['digest']
        second = self.review(job_id, fresh, verdict='no_blocking_concerns')
        self.assertEqual(second['review_count'], 2)
        history = self.durable(job_id)['reviews']
        self.assertEqual(history[0], first['review'])  # the earlier review is untouched
        self.assertNotEqual(history[0]['source']['digest'], history[1]['source']['digest'])
        self.assertEqual(self.durable(job_id)['human_acceptance'], {'state': 'not_recorded'})

    def test_contract_change_marks_review_stale_and_blocks_new_review(self):
        job_id = self.finished_bound_job()
        digest = bridge.capture_source_state(BOUND_PATHS)['digest']
        self.review(job_id, digest)
        (self.root / 'docs' / 'contract.md').write_bytes(b'# FLOW-01 Test Contract\n\n- Status: Rejected\n')
        reasons = bridge.implementation_status(job_id)['evidence']['reviews'][0]['reasons']
        self.assertIn('contract_changed', reasons)
        with self.assertRaises(ValueError):
            self.review(job_id, bridge.capture_source_state(BOUND_PATHS)['digest'])
        self.assertEqual(len(self.durable(job_id)['reviews']), 1)

    def test_review_of_running_or_missing_job_is_refused(self):
        digest = bridge.capture_source_state(BOUND_PATHS)['digest']
        with self.assertRaises(ValueError):
            self.review('no-such-job', digest)
        bridge.JOBS['live'] = {'status': 'running'}
        with self.assertRaises(ValueError):
            self.review('live', digest)

    def test_secret_bearing_verification_is_refused(self):
        job_id = self.finished_bound_job()
        digest = bridge.capture_source_state(BOUND_PATHS)['digest']
        with self.assertRaises(ValueError):
            bridge.record_review(job_id, 'inconclusive', verification=[
                {'command': '{"raw_stdout":"PATIENT_MARKER"}', 'outcome': 'passed'}],
                reviewed_source_digest=digest)
        self.assertEqual(self.durable(job_id)['reviews'], [])


class StrictAncestorTests(FlowCase):
    def test_configured_root_refuses_linked_root_or_ancestor(self):
        self.assertEqual(bridge.configured_root(), self.root)
        for linked in (self.root, self.parent, self.parent.parent):
            with self.subTest(str(linked)), patch.object(snap, 'is_link', only_linked(linked)):
                with self.assertRaises(ValueError):
                    bridge.configured_root()
                with self.assertRaises(ValueError):
                    bridge.capture_source_state([])

    def test_receipt_root_must_be_a_real_directory_without_link_ancestry(self):
        base = self.parent / 'store'
        with patch.object(bridge, 'STORE', None):
            with patch.dict(os.environ, {'CLAUDE_IMPLEMENTER_RECEIPT_ROOT': str(base)}):
                with patch.object(snap, 'is_link', only_linked(base)):
                    with self.assertRaises(ValueError):
                        bridge.get_store()
                with patch.object(snap, 'is_link', only_linked(self.parent)):
                    with self.assertRaises(ValueError):
                        bridge.get_store()
                self.assertEqual(bridge.get_store().base, base)
        with patch.object(bridge, 'STORE', None):
            for bad in (str(self.parent / 'missing'), 'relative/path'):
                with patch.dict(os.environ, {'CLAUDE_IMPLEMENTER_RECEIPT_ROOT': bad}):
                    with self.assertRaises(ValueError):
                        bridge.get_store()

    def test_receipt_root_ancestry_is_vetted_before_the_existence_probe(self):
        base = self.parent / 'store'
        real_isdir = os.path.isdir
        probed = []

        def isdir(path):
            probed.append(os.path.normcase(str(path)))
            return real_isdir(path)

        with patch.object(bridge, 'STORE', None), \
                patch.dict(os.environ, {'CLAUDE_IMPLEMENTER_RECEIPT_ROOT': str(base)}), \
                patch.object(snap, 'is_link', only_linked(self.parent)), \
                patch.object(bridge.os.path, 'isdir', isdir):
            with self.assertRaises(ValueError):
                bridge.get_store()
        self.assertNotIn(os.path.normcase(str(base)), probed)

    def test_launch_is_refused_when_the_root_ancestry_becomes_unsafe(self):
        with self.claude(), patch.object(snap, 'is_link', only_linked(self.parent)):
            with self.assertRaises(ValueError):
                bridge.implement_changes('task', 'scope', 'criteria')
        self.assertEqual(self.launched, [])
        self.assertEqual(bridge.JOBS, {})
        self.assertFalse(bridge.ACTIVE_LOCKS)


if __name__ == '__main__':
    unittest.main()
