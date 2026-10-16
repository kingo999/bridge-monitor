import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import receipts as rc

HERE = str(Path(__file__).resolve().parent)
HEAD = 'a' * 40
SNAPSHOT = {'state': 'captured', 'root': 'repo', 'branch': 'main', 'head': HEAD, 'digest': 'b' * 64,
            'file_count': 3, 'declared_paths': ['local/notes.txt'], 'declared_file_count': 1,
            'absent_declared': []}


def record(job_id='job-1', **changes):
    base = rc.new_record(job_id, 'instance1', {'root': 'repo', 'branch': 'main', 'head': HEAD},
                         source_before=dict(SNAPSHOT), source_paths=['local/notes.txt'])
    base.update(changes)
    return base


def finished(job_id='job-1', state='completed', **changes):
    extracted = rc.sanitize_result({'type': 'result', 'session_id': 'session-1'}, 0)
    value = rc.finish(record(job_id), state=state, code='success', finished_at=time.time(),
                      source_after=dict(SNAPSHOT), extracted=extracted)
    value.update(changes)
    return value


def with_changes(base, **changes):
    value = copy.deepcopy(base)
    value.update(changes)
    return value


def envelope(rec, **overrides) -> bytes:
    """A stored receipt whose integrity value is correct for `rec` (so only the shape is wrong)."""
    value = {'schema': 1, 'integrity': hashlib.sha256(rc.canonical(rec)).hexdigest(), 'record': rec}
    value.update(overrides)
    return json.dumps(value).encode('utf-8')


def make_dir_link(link, target) -> bool:
    """Create a directory symlink, or a junction on Windows; False when neither is permitted."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == 'nt':
        done = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                              capture_output=True, stdin=subprocess.DEVNULL)
        return done.returncode == 0 and os.path.isdir(link)
    return False


def remove_link(link) -> None:
    try:
        os.rmdir(link)
    except OSError:
        try:
            os.unlink(link)
        except OSError:
            pass


def only_linked(*paths):
    """An `is_link` stand-in that reports exactly `paths` as linked (deterministic, no privilege)."""
    wanted = {os.path.normcase(str(path)) for path in paths}
    return lambda path: os.path.normcase(str(path)) in wanted


def reviewed(**changes):
    entry = rc.build_review(1, verdict='changes_requested', reviewer='codex',
                            concerns=[{'id': 'C-1', 'disposition': 'open'}],
                            verification=[{'command': 'python -B -m unittest', 'outcome': 'passed',
                                           'exit_code': 0}],
                            receipt_state='completed', reviewed_source=dict(SNAPSHOT),
                            contract_sha256=None, recorded_at=2.0)
    entry.update(changes)
    return entry


def malformed_records():
    """(label, record): every nested shape that previously could raise TypeError or similar."""
    base = finished()
    bound = rc.new_record('job-b', 'instance1', {'root': 'repo', 'branch': 'main', 'head': HEAD},
                          contract={'path': 'docs/plan.md', 'sha256': 'c' * 64, 'approval': 'Kian, 2026-10-14',
                                    'base_revision': HEAD}, task_id='FLOW-01')
    outcome = base['outcome']
    contract = bound['contract']
    yield from [
        ('state dict', with_changes(base, state={})),
        ('state list', with_changes(base, state=[])),
        ('binding dict', with_changes(base, binding={})),
        ('schema bool', with_changes(base, schema=True)),
        ('instance list', with_changes(base, instance_id=[])),
        ('huge timestamp', with_changes(base, created_at=10 ** 400)),
        ('task dict', with_changes(base, task_id={})),
        ('session dict', with_changes(base, session_id={})),
        ('parent dict', with_changes(base, parent_job_id={})),
        ('note dict', with_changes(base, note={})),
        ('note list', with_changes(base, note=[])),
        ('outcome code dict', with_changes(base, outcome=dict(outcome, code={}))),
        ('denied tool dict', with_changes(base, outcome=dict(outcome, denied_tools=[{}]))),
        ('denied tool lists', with_changes(base, outcome=dict(outcome, denied_tools=[[], []]))),
        ('denied tools dict', with_changes(base, outcome=dict(outcome, denied_tools={}))),
        ('model name dict', with_changes(base, model={'reported': [{}], 'selection_verified': False})),
        ('model names lists', with_changes(base, model={'reported': [[], []], 'selection_verified': False})),
        ('model reported str', with_changes(base, model={'reported': 'x', 'selection_verified': False})),
        ('snapshot reason dict', with_changes(base, source_before={'state': 'unavailable', 'reason': {}})),
        ('snapshot head dict', with_changes(base, source_after=dict(SNAPSHOT, head={}))),
        ('snapshot paths dict', with_changes(base, source_after=dict(SNAPSHOT, declared_paths=[{}]))),
        ('repository root dict', with_changes(base, repository={'root': {}, 'branch': 'main', 'head': HEAD})),
        ('contract path dict', with_changes(bound, contract=dict(contract, path={}))),
        ('contract path int', with_changes(bound, contract=dict(contract, path=5))),
        ('contract path escape', with_changes(bound, contract=dict(contract, path='../x'))),
        ('contract approval dict', with_changes(bound, contract=dict(contract, approval={}))),
        ('reviews str', with_changes(base, reviews='x')),
        ('review int', with_changes(base, reviews=[5])),
        ('review verdict dict', with_changes(base, reviews=[reviewed(verdict={})])),
        ('review state list', with_changes(base, reviews=[reviewed(receipt_state=[])])),
        ('review reviewer dict', with_changes(base, reviews=[reviewed(reviewer={})])),
        ('review source dict', with_changes(base, reviews=[reviewed(source={})])),
        ('review disposition dict', with_changes(
            base, reviews=[reviewed(concerns=[{'id': 'C-1', 'disposition': {}}])])),
        ('review concern id dict', with_changes(
            base, reviews=[reviewed(concerns=[{'id': {}, 'disposition': 'open'}])])),
        ('review outcome dict', with_changes(
            base, reviews=[reviewed(verification=[{'command': 'x', 'outcome': {}, 'exit_code': None}])])),
        ('review command dict', with_changes(
            base, reviews=[reviewed(verification=[{'command': {}, 'outcome': 'passed', 'exit_code': None}])])),
    ]


class StoreCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = rc.ReceiptStore(self.base)

    def raw(self, job_id='job-1'):
        return (self.store.directory / f'{job_id}.json').read_bytes()

    def put(self, job_id, data: bytes):
        self.store.directory.mkdir(exist_ok=True)
        (self.store.directory / f'{job_id}.json').write_bytes(data)


class PersistenceTests(StoreCase):
    def test_store_is_not_created_until_written(self):
        self.assertFalse(self.store.directory.exists())
        self.assertEqual(self.store.read('job-1').status, 'missing')
        self.assertEqual(self.store.list(), {'receipts': [], 'truncated': False})
        self.assertFalse(self.store.directory.exists())

    def test_create_and_reload_round_trip(self):
        created = self.store.create(record())
        result = self.store.read('job-1')
        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.record, created)
        self.assertEqual(result.record['binding'], 'unbound')
        self.assertEqual(result.record['human_acceptance'], {'state': 'not_recorded'})
        leftovers = [name for name in os.listdir(self.store.directory) if name.startswith('.tmp-')]
        self.assertEqual(leftovers, [])

    def test_create_refuses_to_overwrite(self):
        self.store.create(record())
        with self.assertRaises(rc.ReceiptError) as caught:
            self.store.create(record())
        self.assertEqual(caught.exception.code, 'exists')

    def test_failed_replace_keeps_the_authoritative_record_and_no_temp_file(self):
        self.store.create(record())
        before = self.raw()
        with patch.object(rc.os, 'replace', side_effect=PermissionError('denied')):
            with self.assertRaises(rc.ReceiptError) as caught:
                self.store.update('job-1', lambda rec: dict(rec, state='failed', finished_at=1.0,
                                                            outcome=finished()['outcome']))
        self.assertEqual(caught.exception.code, 'write_failed')
        self.assertEqual(self.raw(), before)
        self.assertEqual([n for n in os.listdir(self.store.directory) if n.startswith('.tmp-')], [])

    def test_failed_create_leaves_no_record(self):
        with patch.object(rc.os, 'fsync', side_effect=OSError('disk')):
            with self.assertRaises(rc.ReceiptError):
                self.store.create(record())
        self.assertEqual(self.store.read('job-1').status, 'missing')

    def test_unwritable_store_is_an_explicit_error(self):
        (self.base / '.receipts').write_bytes(b'a file where the directory should be')
        with self.assertRaises(rc.ReceiptError):
            self.store.create(record())

    def test_corrupt_unknown_schema_tampered_and_oversized_are_unavailable(self):
        good = json.loads(self.store.create(record()) and self.raw())
        self.put('garbage', b'{not json')
        self.assertEqual(self.store.read('garbage').reason, 'malformed')
        self.put('wrongtype', b'[1, 2]')
        self.assertEqual(self.store.read('wrongtype').reason, 'malformed')
        future = dict(good, schema=2)
        self.put('future', json.dumps(future).encode())
        self.assertEqual(self.store.read('future').reason, 'unknown_schema')
        tampered = copy.deepcopy(good)
        tampered['record']['state'] = 'completed'
        self.put('tampered', json.dumps(tampered).encode())
        self.assertEqual(self.store.read('tampered').reason, 'integrity_mismatch')
        self.put('big', b' ' * (rc.MAX_RECEIPT_BYTES + 1))
        self.assertEqual(self.store.read('big').reason, 'oversized')
        self.put('moved', self.raw('job-1'))
        self.assertEqual(self.store.read('moved').reason, 'identity_mismatch')
        for name in ('garbage', 'future', 'tampered', 'big', 'moved'):
            self.assertEqual(self.store.read(name).status, 'unavailable', name)

    def test_unavailable_receipts_are_listed_not_hidden(self):
        self.store.create(record())
        self.put('garbage', b'nope')
        entries = {item['job_id']: item for item in self.store.list()['receipts']}
        self.assertEqual(entries['garbage']['state'], 'unavailable')
        self.assertEqual(entries['job-1']['state'], 'running')

    def test_list_is_bounded_and_newest_first(self):
        for index in range(4):
            value = record(f'job-{index}')
            value['created_at'] = 100.0 + index
            self.store.create(value)
        listing = self.store.list(2)
        self.assertEqual([item['job_id'] for item in listing['receipts']], ['job-3', 'job-2'])
        self.assertTrue(listing['truncated'])
        self.assertEqual(len(self.store.running_ids()), 4)


class SchemaTests(unittest.TestCase):
    def test_unknown_and_missing_fields_are_rejected_not_copied(self):
        with self.assertRaises(rc.ReceiptError):
            rc.validate_record(dict(record(), prompt='the task text'))
        value = record()
        del value['usage']
        with self.assertRaises(rc.ReceiptError):
            rc.validate_record(value)
        value = record()
        value['repository']['working_tree'] = ' M secret.txt'
        with self.assertRaises(rc.ReceiptError):
            rc.validate_record(value)

    def test_job_ids_are_portable_and_contained(self):
        for good in ('worker', 'a1', '123e4567-e89b-12d3-a456-426614174000', 'job_1'):
            self.assertTrue(rc.valid_job_id(good), good)
        for bad in ('', '../x', 'a/b', 'a\\b', 'C:x', 'con', 'NUL', 'Upper', '.hidden', 'a.json', 'x' * 65,
                    'a b', 'job\n', None, 7):
            self.assertFalse(rc.valid_job_id(bad), repr(bad))

    def test_human_acceptance_cannot_be_manufactured(self):
        value = finished()
        value['human_acceptance'] = {'state': 'accepted'}
        with self.assertRaises(rc.ReceiptError):
            rc.validate_record(value)

    def test_bound_receipt_needs_contract_and_task(self):
        value = record()
        value['binding'] = 'bound'
        with self.assertRaises(rc.ReceiptError):
            rc.validate_record(value)
        contract = {'path': 'docs/plan.md', 'sha256': 'c' * 64, 'approval': 'Kian, 2026-10-14',
                    'base_revision': HEAD}
        bound = rc.new_record('job-b', 'instance1', {'root': 'repo', 'branch': 'main', 'head': HEAD},
                              contract=contract, task_id='FLOW-01')
        self.assertEqual(rc.validate_record(bound)['binding'], 'bound')

    def test_terminal_state_requires_finished_at(self):
        value = finished()
        value['finished_at'] = None
        with self.assertRaises(rc.ReceiptError):
            rc.validate_record(value)

    def test_secret_like_approval_is_refused(self):
        contract = {'path': 'plan.md', 'sha256': 'c' * 64, 'approval': 'api_key=abc123', 'base_revision': HEAD}
        value = rc.new_record('job-b', 'instance1', {'root': 'repo'}, contract=contract, task_id='T-1')
        with self.assertRaises(rc.ReceiptError) as caught:
            rc.validate_record(value)
        self.assertEqual(caught.exception.code, 'sensitive_metadata')


class SanitizeTests(unittest.TestCase):
    def test_hostile_cli_payload_yields_only_allowlisted_scalars(self):
        hostile = {
            'type': 'result', 'session_id': '--dangerous', 'result': 'SECRET RESULT', 'stderr': 'SECRET',
            'permission_denials': [{'tool_name': 'Bash', 'tool_input': {'command': 'SECRET curl'}},
                                   {'tool_name': 'Bash'}, {'tool_name': 'rm -rf /'}, 'SECRET', None],
            'usage': {'input_tokens': 12, 'output_tokens': True, 'cache_read_input_tokens': -4,
                      'cache_creation_input_tokens': 2.0, 'prompt': 'SECRET'},
            'num_turns': 3, 'duration_ms': 1.5, 'total_cost_usd': 9.99,
            'modelUsage': {'claude-sonnet-5-5': {'costUSD': 1}, 'bad model name\n': {}, 'x' * 200: {}}}
        extracted = rc.sanitize_result(hostile, 0)
        self.assertIsNone(extracted['session_id'])
        self.assertEqual(extracted['denial_count'], 5)
        self.assertEqual(extracted['denied_tools'], ['Bash', 'other'])
        self.assertEqual(extracted['usage'], {'reported': True, 'input_tokens': 12, 'output_tokens': None,
                                              'cache_creation_input_tokens': 2, 'cache_read_input_tokens': None,
                                              'num_turns': 3, 'duration_ms': None, 'duration_api_ms': None})
        self.assertEqual(extracted['model'], {'reported': ['claude-sonnet-5-5'], 'selection_verified': False})
        self.assertNotIn('SECRET', json.dumps(extracted))
        self.assertNotIn('9.99', json.dumps(extracted))

    def test_missing_metadata_is_unknown_not_zero(self):
        extracted = rc.sanitize_result({'type': 'result'}, 0)
        self.assertEqual(extracted['usage'], rc.empty_usage())
        self.assertFalse(extracted['usage']['reported'])
        self.assertIsNone(extracted['usage']['input_tokens'])
        self.assertEqual(rc.sanitize_result(None, 1)['returncode'], 1)
        self.assertEqual(rc.sanitize_result([1], True)['returncode'], None)

    def test_sanitized_result_always_validates_in_a_record(self):
        extracted = rc.sanitize_result({'session_id': 'ok-1', 'permission_denials': [{'tool_name': 'Edit'}]}, 0)
        value = rc.finish(record(), state='needs_attention', code='permission_denials',
                          finished_at=1.0, source_after=None, extracted=extracted)
        self.assertEqual(rc.validate_record(value)['outcome']['denied_tools'], ['Edit'])


class TransitionTests(StoreCase):
    def test_running_to_terminal_then_frozen_except_reviews(self):
        self.store.create(record())
        final = finished()
        self.store.update('job-1', lambda rec: rc.finish(
            rec, state='completed', code='success', finished_at=final['finished_at'],
            source_after=dict(SNAPSHOT), extracted=rc.sanitize_result({'session_id': 's-1'}, 0)))
        with self.assertRaises(rc.ReceiptError) as caught:
            self.store.update('job-1', lambda rec: dict(rec, state='failed'))
        self.assertEqual(caught.exception.code, 'immutable_terminal')
        with self.assertRaises(rc.ReceiptError):
            self.store.update('job-1', lambda rec: dict(rec, job_id='other'))

    def test_reviews_are_append_only_history(self):
        self.store.create(record())
        self.store.update('job-1', lambda rec: rc.finish(
            rec, state='completed', code='success', finished_at=1.0, source_after=dict(SNAPSHOT)))

        def review(position, verdict='no_blocking_concerns'):
            return rc.build_review(position, verdict=verdict, reviewer='codex',
                                   concerns=[{'id': 'C-1', 'disposition': 'fixed'}],
                                   verification=[{'command': 'python -B -m unittest', 'outcome': 'passed',
                                                  'exit_code': 0}],
                                   receipt_state='completed', reviewed_source=dict(SNAPSHOT),
                                   contract_sha256=None, recorded_at=2.0)

        self.store.update('job-1', lambda rec: dict(rec, reviews=[review(1)]))
        self.store.update('job-1', lambda rec: dict(rec, reviews=rec['reviews'] + [review(2, 'inconclusive')]))
        with self.assertRaises(rc.ReceiptError) as caught:
            self.store.update('job-1', lambda rec: dict(rec, reviews=[review(1, 'changes_requested'),
                                                                      rec['reviews'][1]]))
        self.assertEqual(caught.exception.code, 'immutable_review')
        with self.assertRaises(rc.ReceiptError):
            self.store.update('job-1', lambda rec: dict(rec, reviews=rec['reviews'][:1]))
        self.assertEqual(len(self.store.read('job-1').record['reviews']), 2)

    def test_update_of_unavailable_record_is_refused_and_untouched(self):
        self.put('job-1', b'garbage')
        with self.assertRaises(rc.ReceiptError):
            self.store.update('job-1', lambda rec: rec)
        self.assertEqual(self.raw(), b'garbage')

    def test_no_change_mutation_does_not_write(self):
        self.store.create(record())
        before = self.raw()
        self.store.update('job-1', lambda rec: None)
        self.assertEqual(self.raw(), before)


class ReviewInputTests(unittest.TestCase):
    def build(self, **changes):
        arguments = dict(verdict='changes_requested', reviewer='codex', concerns=None, verification=None,
                         receipt_state='completed', reviewed_source=dict(SNAPSHOT), contract_sha256=None,
                         recorded_at=1.0)
        arguments.update(changes)
        return rc.build_review(1, **arguments)

    def test_valid_review_contains_only_allowlisted_fields(self):
        review = self.build(concerns=[{'id': 'FLOW-1', 'disposition': 'open'}],
                            verification=[{'command': 'python -B -m unittest discover', 'outcome': 'passed'}])
        self.assertEqual(review['verification'][0]['exit_code'], None)
        self.assertEqual(set(review), {'review_id', 'recorded_at', 'verdict', 'reviewer', 'receipt_state',
                                       'source', 'contract_sha256', 'concerns', 'verification'})

    def test_rejects_unknown_shapes_and_values(self):
        for changes in ({'verdict': 'approved'}, {'verdict': 'looks good'},
                        {'concerns': [{'id': 'C-1'}]},
                        {'concerns': [{'id': 'C 1', 'disposition': 'open'}]},
                        {'concerns': [{'id': 'C-1', 'disposition': 'wontfix'}]},
                        {'concerns': [{'id': 'C-1', 'disposition': 'open', 'note': 'x'}]},
                        {'verification': [{'command': 'x', 'outcome': 'ok'}]},
                        {'verification': [{'command': 'x', 'outcome': 'passed', 'stdout': 'raw'}]},
                        {'verification': [{'command': 'a\nb', 'outcome': 'passed'}]},
                        {'verification': [{'command': 'x' * 301, 'outcome': 'passed'}]},
                        {'verification': [{'command': 'x', 'outcome': 'passed', 'exit_code': 9999}]},
                        {'reviewer': 'a\nb'}, {'concerns': 'all'},
                        {'concerns': [{'id': f'C{i}', 'disposition': 'open'} for i in range(51)]}):
            with self.assertRaises(ValueError, msg=str(changes)):
                self.build(**changes)

    def test_secret_bearing_commands_are_rejected(self):
        for command in ('curl -H "Authorization: Bearer abc" https://x', 'run --password=hunter2',
                        'API_KEY=abcd deploy', 'git clone https://user:pw@host/repo',
                        'echo ' + 'A' * 60, '-----BEGIN PRIVATE KEY-----', 'use sk-' + 'a' * 20):
            with self.assertRaises(ValueError, msg=command):
                self.build(verification=[{'command': command, 'outcome': 'passed'}])


class LockTests(StoreCase):
    def test_in_process_exclusion_and_release(self):
        first = self.store.lock('active-job')
        second = self.store.lock('active-job')
        self.assertTrue(first.try_acquire())
        self.assertFalse(second.try_acquire())
        first.release()
        self.assertTrue(second.try_acquire())
        second.release()

    def test_cross_process_exclusion_survives_only_while_holder_lives(self):
        lock_path = self.store.lock('active-job').path
        code = ('import sys; sys.path.insert(0, %r); import receipts; '
                'lock = receipts.FileLock(%r); assert lock.try_acquire(); print("held", flush=True); '
                'sys.stdin.readline()' % (HERE, lock_path))
        child = subprocess.Popen([sys.executable, '-B', '-c', code], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'held')
            self.assertFalse(rc.FileLock(lock_path).try_acquire())
            child.stdin.write('\n')
            child.stdin.flush()
            child.wait(timeout=20)
            probe = rc.FileLock(lock_path)
            self.assertTrue(probe.try_acquire(attempts=20, delay=0.1))
            probe.release()
        finally:
            if child.poll() is None:
                child.kill()
            child.stdout.close()
            child.stdin.close()

    def test_store_lock_times_out_explicitly(self):
        held = self.store.lock('store')
        self.assertTrue(held.try_acquire())
        try:
            with self.assertRaises(rc.ReceiptError) as caught:
                self.store.lock('store').acquire(timeout=0.1)
            self.assertEqual(caught.exception.code, 'lock_timeout')
        finally:
            held.release()


class UnsafeStoreTests(unittest.TestCase):
    def test_store_name_cannot_escape_or_be_absolute(self):
        with tempfile.TemporaryDirectory() as base:
            for name in ('..', '../x', '/abs', 'C:/x', 'a:b'):
                with self.assertRaises(rc.ReceiptError, msg=name):
                    rc.ReceiptStore(base, name)

    def test_linked_store_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            base = Path(parent) / 'base'
            outside = Path(parent) / 'outside'
            base.mkdir()
            outside.mkdir()
            try:
                os.symlink(outside, base / '.receipts', target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('symbolic links are not permitted here')
            with self.assertRaises(rc.ReceiptError):
                rc.ReceiptStore(base)
            self.assertEqual(os.listdir(outside), [])

    def test_invalid_ids_never_reach_the_filesystem(self):
        with tempfile.TemporaryDirectory() as base:
            store = rc.ReceiptStore(base)
            for bad in ('../escape', 'a/b', 'C:x', ''):
                with self.assertRaises(rc.ReceiptError):
                    store.read(bad)


class MalformedShapeTests(StoreCase):
    def test_every_malformed_nested_shape_is_a_receipt_error_never_a_crash(self):
        count = 0
        for label, bad in malformed_records():
            count += 1
            with self.subTest(label):
                with self.assertRaises(rc.ReceiptError):
                    rc.validate_record(bad)
                with self.assertRaises(rc.ReceiptError):
                    rc.encode(bad)
                with self.assertRaises(rc.ReceiptError):
                    rc.decode(envelope(bad))
        self.assertGreater(count, 30)

    def test_valid_integrity_envelopes_with_bad_shapes_are_unavailable(self):
        for index, (label, bad) in enumerate(malformed_records()):
            with self.subTest(label):
                name = f'bad-{index}'
                self.put(name, envelope(bad))
                result = self.store.read(name)
                self.assertEqual(result.status, 'unavailable')
                self.assertEqual(result.reason, 'malformed')
        listing = self.store.list(50)
        self.assertTrue(all(item['state'] == 'unavailable' for item in listing['receipts']))

    def test_envelope_level_oddities_are_unavailable(self):
        good = finished()
        for label, data in (('schema bool', envelope(good, schema=True)),
                            ('integrity dict', envelope(good, integrity={})),
                            ('record list', envelope([1, 2])),
                            ('extra key', envelope(good, extra=1)),
                            ('nan', b'{"schema":1,"integrity":"x","record":NaN}')):
            with self.subTest(label):
                with self.assertRaises(rc.ReceiptError):
                    rc.decode(data)
        with self.assertRaises(rc.ReceiptError):
            rc.decode('not bytes')

    def test_unhashable_review_input_is_a_value_error(self):
        for changes in ({'verdict': {}}, {'concerns': [{'id': 'C-1', 'disposition': {}}]},
                        {'verification': [{'command': 'x', 'outcome': []}]}):
            arguments = dict(verdict='changes_requested', reviewer='codex', concerns=None, verification=None,
                             receipt_state='completed', reviewed_source=dict(SNAPSHOT), contract_sha256=None,
                             recorded_at=1.0)
            arguments.update(changes)
            with self.assertRaises(ValueError, msg=str(changes)):
                rc.build_review(1, **arguments)


class IdentifierSecrecyTests(StoreCase):
    SECRET_ID = 'sk-' + 'a' * 32

    def test_credential_shaped_session_and_model_ids_are_not_extracted(self):
        extracted = rc.sanitize_result({'session_id': self.SECRET_ID,
                                        'modelUsage': {self.SECRET_ID: {}, 'AKIA' + 'A' * 16: {},
                                                       'claude-opus-4-1-20250805': {}}}, 0)
        self.assertIsNone(extracted['session_id'])
        self.assertEqual(extracted['model']['reported'], ['claude-opus-4-1-20250805'])
        self.assertNotIn(self.SECRET_ID, json.dumps(extracted))

    def test_ordinary_session_uuid_and_model_ids_are_kept(self):
        uuid_text = '123e4567-e89b-12d3-a456-426614174000'
        extracted = rc.sanitize_result({'session_id': uuid_text,
                                        'modelUsage': {'claude-sonnet-5-5': {}, 'claude-haiku-5-5[1m]': {}}}, 0)
        self.assertEqual(extracted['session_id'], uuid_text)
        self.assertEqual(extracted['model']['reported'], ['claude-sonnet-5-5', 'claude-haiku-5-5[1m]'])
        rc.validate_record(rc.finish(record(), state='completed', code='success', finished_at=1.0,
                                     source_after=None, extracted=extracted))

    def test_persisted_credential_shaped_identifiers_are_rejected(self):
        base = finished()
        for label, bad in (('session', with_changes(base, session_id=self.SECRET_ID)),
                           ('model', with_changes(base, model={'reported': [self.SECRET_ID],
                                                               'selection_verified': False})),
                           ('task', with_changes(base, task_id=self.SECRET_ID)),
                           ('instance', with_changes(base, instance_id=self.SECRET_ID))):
            with self.subTest(label):
                with self.assertRaises(rc.ReceiptError) as caught:
                    rc.validate_record(bad)
                self.assertEqual(caught.exception.code, 'sensitive_metadata')
                self.put('leaky', envelope(bad))
                result = self.store.read('leaky')
                self.assertEqual((result.status, result.reason), ('unavailable', 'sensitive_metadata'))


class CommandMetadataTests(unittest.TestCase):
    REJECTED = ('{"raw_stdout":"PATIENT_MARKER"}', '{"a": 1}', '[1, 2]', 'echo "stdout": PATIENT_MARKER',
                'stdout=PATIENT_MARKER', 'tool_input PATIENT_MARKER',
                'export AWS_SECRET_ACCESS_KEY abcdefghijklmnop', 'export AWS_SECRET_ACCESS_KEY=abcdefghijkl',
                'set TOKEN abc', 'setx api_token abc', '$env:API_KEY abc', 'deploy --password hunter2',
                'deploy --api-key hunter2', 'curl -u user:pass https://x', 'curl --user=user:pass https://x',
                'curl -H Authorization Bearer abc', 'echo ghp_' + 'a' * 30)
    ACCEPTED = ('python -B -m unittest discover', 'py -3 -B -I docs/architecture/validate_context.py',
                'mvn -pl context-security test', 'pytest -k token C:/AI/gpt-claude-mcp-bridge-v2/tools/claude-bridge',
                'git diff --stat', 'python -B -m unittest test_receipts.SanitizeTests', 'deploy --password')

    def test_structured_payloads_and_credential_command_forms_are_rejected(self):
        for command in self.REJECTED:
            with self.subTest(command):
                with self.assertRaises(ValueError):
                    rc.check_text(command, 'verification command', 300)
                with self.assertRaises(ValueError):
                    rc.build_review(1, verdict='inconclusive', reviewer=None, concerns=None,
                                    verification=[{'command': command, 'outcome': 'passed'}],
                                    receipt_state='completed', reviewed_source=dict(SNAPSHOT),
                                    contract_sha256=None, recorded_at=1.0)

    def test_ordinary_short_commands_stay_valid(self):
        for command in self.ACCEPTED:
            with self.subTest(command):
                self.assertEqual(rc.check_text(command, 'verification command', 300), command)

    def test_persisted_structured_commands_are_rejected_on_validation(self):
        for command in self.REJECTED:
            with self.subTest(command):
                bad = with_changes(finished(), reviews=[reviewed(
                    verification=[{'command': command, 'outcome': 'passed', 'exit_code': None}])])
                with self.assertRaises(rc.ReceiptError) as caught:
                    rc.validate_record(bad)
                self.assertEqual(caught.exception.code, 'sensitive_metadata')

    def test_approval_prose_is_not_subject_to_the_command_forms(self):
        contract = {'path': 'plan.md', 'sha256': 'c' * 64, 'base_revision': HEAD,
                    'approval': 'Kian, 2026-10-14, authorization to implement FLOW-01 given in chat'}
        value = rc.new_record('job-b', 'instance1', {'root': 'repo'}, contract=contract, task_id='T-1')
        self.assertEqual(rc.validate_record(value)['binding'], 'bound')
        contract['approval'] = '{"raw_stdout":"PATIENT_MARKER"}'
        with self.assertRaises(rc.ReceiptError):
            rc.validate_record(rc.new_record('job-b', 'instance1', {'root': 'repo'}, contract=contract,
                                             task_id='T-1'))


class RevalidatedStoreTests(StoreCase):
    def untouched(self):
        """Fail loudly if any raw file open happens while the store must be refused."""
        return patch.object(rc.os, 'open', side_effect=AssertionError('the store was touched'))

    def test_store_replaced_by_a_link_after_construction_is_refused_everywhere(self):
        self.store.create(record())
        with patch.object(rc.source, 'is_link', only_linked(self.store.directory)):
            with self.untouched():
                result = self.store.read('job-1')
                self.assertEqual((result.status, result.reason), ('unavailable', 'unsafe_store'))
                listing = self.store.list()
                self.assertEqual(listing['receipts'], [{'state': 'unavailable', 'reason': 'unsafe_store'}])
                self.assertFalse(self.store.exists())
                for action in (lambda: self.store.create(record('job-2')),
                               lambda: self.store.update('job-1', lambda rec: rec),
                               lambda: self.store.lock('store')):
                    with self.assertRaises(rc.ReceiptError) as caught:
                        action()
                    self.assertEqual(caught.exception.code, 'unsafe_store')
        self.assertEqual(self.store.read('job-1').status, 'ok')
        self.assertFalse((self.store.directory / 'job-2.json').exists())

    def test_linked_ancestor_of_a_nested_store_is_refused(self):
        store = rc.ReceiptStore(self.base, 'sub/store')
        store.create(record())
        with patch.object(rc.source, 'is_link', only_linked(self.base / 'sub')):
            with self.untouched():
                self.assertEqual(store.read('job-1').reason, 'unsafe_store')
                with self.assertRaises(rc.ReceiptError):
                    store.create(record('job-2'))

    def test_base_that_changed_since_construction_is_refused(self):
        self.store.create(record())
        with patch.object(rc.source, 'pin', return_value='elsewhere'):
            with self.untouched():
                self.assertEqual(self.store.read('job-1').reason, 'unsafe_store')
                with self.assertRaises(rc.ReceiptError) as caught:
                    self.store.create(record('job-2'))
                self.assertEqual(caught.exception.code, 'unsafe_store')

    def test_linked_base_is_refused_at_construction(self):
        with patch.object(rc.source, 'is_link', only_linked(self.base)):
            with self.assertRaises(rc.ReceiptError) as caught:
                rc.ReceiptStore(self.base)
        self.assertEqual(caught.exception.code, 'unsafe_store')

    def test_real_link_swapped_in_after_construction_never_touches_the_target(self):
        self.store.create(record())
        outside = Path(self.base.parent) / (self.base.name + '-outside')
        outside.mkdir()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / 'job-1.json').write_bytes(self.raw())
        original = os.listdir(outside)
        os.rename(self.store.directory, self.base / 'moved-store')
        if not make_dir_link(self.store.directory, outside):
            os.rename(self.base / 'moved-store', self.store.directory)
            self.skipTest('neither symbolic links nor junctions are permitted here')
        self.addCleanup(remove_link, self.store.directory)
        result = self.store.read('job-1')
        self.assertEqual((result.status, result.reason), ('unavailable', 'unsafe_store'))
        self.assertEqual(self.store.list()['receipts'][0]['reason'], 'unsafe_store')
        for action in (lambda: self.store.create(record('job-3')), lambda: self.store.lock('active-job')):
            with self.assertRaises(rc.ReceiptError):
                action()
        self.assertEqual(sorted(os.listdir(outside)), sorted(original))

    def test_unsafe_lock_names_are_rejected_before_any_filesystem_access(self):
        for name in ('../x', 'a/b', 'a\\b', 'con', '', 'x.lock', 'UPPER', '.hidden', 'x' * 40, None, 5, ['store']):
            with self.subTest(repr(name)):
                with self.assertRaises(rc.ReceiptError) as caught:
                    self.store.lock(name)
                self.assertEqual(caught.exception.code, 'invalid_lock')
        self.assertFalse(self.store.directory.exists())

    def test_linked_lock_leaf_is_refused_before_it_is_opened_or_created(self):
        lock = self.store.lock('active-job')
        with patch.object(rc.source, 'is_link', only_linked(lock.path)):
            with self.untouched():
                with self.assertRaises(rc.ReceiptError) as caught:
                    lock.try_acquire()
                self.assertEqual(caught.exception.code, 'lock_unavailable')
        self.assertFalse(os.path.exists(lock.path))

    def test_lock_acquisition_revalidates_the_store(self):
        lock = self.store.lock('active-job')
        with patch.object(rc.source, 'is_link', only_linked(self.store.directory)):
            with self.untouched():
                with self.assertRaises(rc.ReceiptError) as caught:
                    lock.acquire(timeout=0.1)
                self.assertEqual(caught.exception.code, 'unsafe_store')
        self.assertFalse(os.path.exists(lock.path))

    def test_real_linked_lock_file_is_not_written(self):
        lock = self.store.lock('active-job')
        external = Path(self.base.parent) / (self.base.name + '-external.lock')
        external.write_bytes(b'')
        self.addCleanup(lambda: external.unlink())
        try:
            os.symlink(external, lock.path)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links are not permitted here')
        self.addCleanup(remove_link, lock.path)
        with self.assertRaises(rc.ReceiptError):
            lock.try_acquire()
        self.assertEqual(external.read_bytes(), b'')


if __name__ == '__main__':
    unittest.main()
