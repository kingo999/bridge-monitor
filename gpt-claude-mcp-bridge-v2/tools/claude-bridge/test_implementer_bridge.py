import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import implementer_bridge as bridge
import receipts


class BridgeTests(unittest.TestCase):
    def setUp(self):
        # Receipt writes go to an isolated temporary store, never C:/AI/gpt-claude-mcp-bridge-v2/tools/claude-bridge/.receipts.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        saved = (bridge.STORE, bridge.RECOVERED)
        bridge.STORE, bridge.RECOVERED = receipts.ReceiptStore(temporary.name), False
        self.addCleanup(lambda: setattr(bridge, 'STORE', saved[0]) or setattr(bridge, 'RECOVERED', saved[1]))

    def test_command_keeps_permissions_and_disables_recursive_mcp(self):
        command = bridge.claude_command('claude.exe', None)
        self.assertNotIn('--dangerously-skip-permissions', command)
        self.assertEqual(command[command.index('--permission-prompts') + 1], 'none')
        self.assertEqual(json.loads(command[command.index('--mcp-config') + 1]), {'mcpServers': {}})
        self.assertIn('--strict-mcp-config', command)

    def test_git_does_not_inherit_mcp_protocol_stdin(self):
        with patch.object(bridge.subprocess, 'run', return_value=Mock(returncode=0, stdout='head')) as run:
            self.assertEqual(bridge.git(Path('.'), 'rev-parse', 'HEAD'), 'head')
            self.assertEqual(run.call_args.kwargs.get('stdin'), bridge.subprocess.DEVNULL)

    def test_resume_is_explicit(self):
        self.assertNotIn('--resume', bridge.claude_command('claude.exe', None))
        self.assertIn('--resume', bridge.claude_command('claude.exe', 'session'))

    def test_result_requires_success_and_reports_denials(self):
        result = {'type': 'result', 'is_error': False, 'result': 'Done',
                  'session_id': 'session', 'permission_denials': [{'tool_name': 'Bash'}]}
        self.assertEqual(bridge.result_status(result, 0), 'needs_attention')
        self.assertEqual(bridge.result_status(dict(result, permission_denials=[]), 0), 'completed')
        self.assertEqual(bridge.result_status(result, 1), 'failed')
        self.assertEqual(bridge.result_status(None, 0), 'failed')

    def test_missing_job_does_not_leak_state(self):
        self.assertEqual(bridge.implementation_status('missing'), {'status': 'unknown_job'})

    def test_empty_task_rejected_before_launch(self):
        with self.assertRaises(ValueError):
            bridge.implement_changes('', '', '')

    def test_worker_reports_denied_command_and_preserves_checkout_evidence(self):
        result = {'type': 'result', 'is_error': False, 'result': 'Check denied',
                  'session_id': 'session', 'permission_denials': [{'tool_name': 'Bash'}]}
        process = Mock(returncode=0)
        process.poll.return_value = 0
        process.communicate.return_value = (json.dumps(result), 'SECRET stderr')
        bridge.JOBS['worker'] = {'status': 'running', 'repository': {'root': 'repo'}}
        bridge.create_receipt('worker')  # the durable record that precedes any worker dispatch
        try:
            with patch.object(bridge.shutil, 'which', return_value='claude.exe'), \
                    patch.object(bridge.subprocess, 'Popen', return_value=process), \
                    patch.object(bridge, 'repository_state', return_value={'root': 'repo'}):
                bridge.run_job('worker', 'request', None)
            job = bridge.JOBS['worker']
            self.assertEqual(job['status'], 'needs_attention')
            self.assertEqual(job['session_id'], 'session')
            self.assertNotIn('SECRET', json.dumps(job))
            self.assertEqual(job['repository_after'], {'root': 'repo'})
        finally:
            bridge.JOBS.pop('worker', None)

    def test_concurrent_job_is_rejected(self):
        bridge.JOBS['busy'] = {'status': 'running'}
        try:
            with patch.object(bridge, 'repository_state', return_value={}):
                with self.assertRaises(ValueError):
                    bridge.implement_changes('task', 'scope', 'criteria')
        finally:
            bridge.JOBS.pop('busy', None)

    def test_resume_from_another_branch_rejected(self):
        with patch.object(bridge, 'repository_state', return_value={'root': 'repo', 'branch': 'other'}):
            bridge.JOBS['prior'] = {'status': 'completed', 'repository': {'root': 'repo', 'branch': 'main'},
                                    'session_id': 'session'}
            try:
                with self.assertRaises(ValueError):
                    bridge.implement_changes('task', 'scope', 'criteria', 'prior')
            finally:
                bridge.JOBS.pop('prior', None)


if __name__ == '__main__':
    unittest.main()
