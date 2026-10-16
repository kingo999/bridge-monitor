"""Review dispatch must remove write capabilities and fail closed on source drift."""
import json
from pathlib import Path
from unittest.mock import patch

import implementer_bridge as bridge
import receipts
from test_flow import FlowCase, git


class ReadOnlyReviewTests(FlowCase):
    def review(self, **options):
        with self.claude(**options):
            started = bridge.review_changes('Review the design.', 'Source only.', 'Findings only.')
            return started, self.wait(started['job_id'])

    def test_review_launch_removes_all_execution_and_edit_tools(self):
        started, job = self.review()
        command = self.launched[0]
        self.assertEqual(command[command.index('--tools') + 1], 'Read,Glob,Grep')
        self.assertEqual(command[command.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(command[command.index('--permission-prompts') + 1], 'none')
        for flag in ['--restricted', '--safe-mode', '--disable-slash-commands', '--strict-mcp-config']:
            self.assertIn(flag, command)
        self.assertNotIn('--resume', command)
        self.assertEqual(json.loads(command[command.index('--mcp-config') + 1]), {'mcpServers': {}})
        self.assertEqual(job['status'], 'completed')
        self.assertTrue(job['review_verification']['unchanged'])
        self.assertEqual(self.durable(started['job_id'])['execution_mode'], 'review')

    def test_source_change_cannot_be_reported_as_success(self):
        _, job = self.review(edit=lambda: (self.root / 'tracked.txt').write_text('changed'))
        self.assertEqual(job['status'], 'needs_attention')
        self.assertFalse(job['review_verification']['unchanged'])
        self.assertEqual(self.durable(job['job_id'])['outcome']['code'], 'review_source_changed')

    def test_additional_repository_is_readable_and_checked_for_drift(self):
        extra = self.parent / 'other-repo'
        extra.mkdir()
        git(extra, 'init', '-q')
        (extra / 'source.txt').write_text('before')
        git(extra, 'add', '-A')
        git(extra, 'commit', '-q', '-m', 'base')
        with self.claude(edit=lambda: (extra / 'source.txt').write_text('after')):
            started = bridge.review_changes('Review both.', 'Two repos.', 'Findings.',
                                             additional_read_roots=[str(extra)])
            job = self.wait(started['job_id'])
        command = self.launched[0]
        self.assertIn(str(extra), command[command.index('--add-dir') + 1:])
        self.assertEqual(job['status'], 'needs_attention')
        self.assertFalse(job['review_verification']['unchanged'])

    def test_review_session_cannot_be_resumed_by_the_implementation_tool(self):
        started, _ = self.review()
        # Also exercise durable recovery, rather than relying on in-memory mode.
        bridge.JOBS.clear()
        with self.claude(), self.assertRaisesRegex(ValueError, 'review'):
            bridge.implement_changes('Implement.', 'Source.', 'Tests.', started['job_id'])
        self.assertEqual(len(self.launched), 1)

    def test_review_refuses_unavailable_snapshot_before_launch(self):
        with patch.object(bridge, 'strict_snapshot', side_effect=ValueError('unavailable')):
            with self.claude(), self.assertRaises(ValueError):
                bridge.review_changes('Review.', 'Source.', 'Findings.')
        self.assert_nothing_launched()

    def test_declared_ignored_source_is_included_in_drift_check(self):
        ignored = self.root / 'ignored' / 'notes.txt'
        with self.claude(edit=lambda: ignored.write_text('changed')):
            started = bridge.review_changes('Review.', 'Source.', 'Findings.',
                                             source_paths=['ignored/notes.txt'])
            job = self.wait(started['job_id'])
        self.assertEqual(job['status'], 'needs_attention')
        self.assertFalse(job['review_verification']['unchanged'])

    def test_missing_after_evidence_cannot_be_reported_as_unchanged(self):
        with patch.object(bridge, 'snapshot', return_value={'state': 'unavailable', 'reason': 'unexpected'}):
            _, job = self.review()
        self.assertEqual(job['status'], 'needs_attention')
        self.assertIsNone(job['review_verification']['unchanged'])

    def test_cli_builder_refuses_resuming_an_existing_session_for_review(self):
        with self.assertRaises(ValueError):
            bridge.claude_command('claude.exe', 'old-session', review=True)

    def test_receipt_mode_cannot_be_downgraded_or_removed(self):
        started, _ = self.review()
        for change in ['implementation', None]:
            def downgrade(record):
                if change is None:
                    record.pop('execution_mode')
                else:
                    record['execution_mode'] = change
                return record
            with self.subTest(change=change), self.assertRaises(receipts.ReceiptError):
                self.store.update(started['job_id'], downgrade)

    def test_review_refuses_non_repository_and_relative_read_roots(self):
        plain = self.parent / 'plain'
        plain.mkdir()
        for root in ['../repo', str(plain)]:
            with self.subTest(root=root), self.claude(), self.assertRaises(ValueError):
                bridge.review_changes('Review.', 'Source.', 'Findings.',
                                      additional_read_roots=[root])
        self.assert_nothing_launched()

    def test_legacy_receipt_without_mode_still_round_trips(self):
        record = receipts.new_record('legacy', 'instance', {'root': str(self.root)})
        record.pop('execution_mode', None)
        self.assertEqual(receipts.decode(receipts.encode(record)), record)

