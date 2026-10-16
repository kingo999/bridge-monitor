"""Model-free MCP stdio transport checks against the real server module in a child process.

A temporary Python shim imports `implementer_bridge`, replaces the Claude CLI lookup and
launcher with an in-process fake, and serves MCP on stdio. The test speaks newline-delimited
JSON-RPC to it. No Claude CLI, account, model or the registered live server is involved; the
repository and receipt store are temporary directories.
"""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import receipts

BRIDGE_DIR = Path(__file__).resolve().parent
OLD_REQUIRED_TOOLS = {'implement_changes', 'implementation_status'}
NEW_TOOLS = {'capture_source_state', 'record_review', 'list_receipts', 'review_changes'}

SHIM = '''
import json, os, subprocess, sys
from unittest.mock import Mock
sys.path.insert(0, os.environ['FLOW_BRIDGE_DIR'])
import implementer_bridge as bridge

FAKE = 'fake-claude.exe'
real_popen = subprocess.Popen


def popen(command, *args, **kwargs):
    if command and command[0] == FAKE:
        process = Mock()
        process.returncode = 0
        process.pid = 1
        process.poll.return_value = 0
        process.communicate.return_value = (json.dumps({
            'type': 'result', 'is_error': False, 'result': 'TRANSPORT-RESULT', 'session_id': 'session-t',
            'permission_denials': []}), '')
        return process
    return real_popen(command, *args, **kwargs)


bridge.shutil.which = lambda name: FAKE
subprocess.Popen = popen
bridge.mcp.run(transport='stdio')
'''


def git(root, *args):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    '-c', 'commit.gpgsign=false', *args],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)


class McpClient:
    """Minimal JSON-RPC 2.0 client for the stdio transport (one JSON message per line)."""

    def __init__(self, shim: Path, environment: dict):
        self.process = subprocess.Popen([sys.executable, '-B', str(shim)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment,
                                        text=True, encoding='utf-8')
        self.lines: queue.Queue = queue.Queue()
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _send(self, message: dict):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 60):
        self.next_id += 1
        expected = self.next_id
        self._send({'jsonrpc': '2.0', 'id': expected, 'method': method, 'params': params or {}})
        while True:
            line = self.lines.get(timeout=timeout)
            if line is None:
                raise AssertionError('the server closed the transport')
            message = json.loads(line)
            if message.get('id') == expected:
                return message

    def initialize(self) -> dict:
        reply = self.request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                            'clientInfo': {'name': 'flow-transport-test', 'version': '0'}})
        self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        return reply['result']

    def tools(self) -> dict:
        return {tool['name']: tool for tool in self.request('tools/list')['result']['tools']}

    def call(self, name: str, arguments: dict | None = None) -> tuple[bool, object]:
        """Return (is_error, parsed payload or error text) for a tool call."""
        result = self.request('tools/call', {'name': name, 'arguments': arguments or {}})['result']
        if result.get('isError'):
            return True, ' '.join(block.get('text', '') for block in result.get('content', []))
        if result.get('structuredContent'):
            return False, result['structuredContent']
        text = next(block['text'] for block in result['content'] if block.get('type') == 'text')
        return False, json.loads(text)

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait(timeout=15)
        self.process.stdout.close()


class TransportCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.root = self.parent / 'repo'
        self.root.mkdir()
        git(self.root, 'init', '-q')
        (self.root / 'tracked.txt').write_bytes(b'one\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'base')
        self.store_base = self.parent / 'store'
        self.store_base.mkdir()
        self.shim = self.parent / 'shim.py'  # outside the repository and the bridge directory
        self.shim.write_text(SHIM, encoding='utf-8')
        self.environment = dict(os.environ, FLOW_BRIDGE_DIR=str(BRIDGE_DIR),
                                CLAUDE_IMPLEMENTER_ROOT=str(self.root),
                                CLAUDE_IMPLEMENTER_RECEIPT_ROOT=str(self.store_base))
        self.environment.pop('CLAUDECODE', None)

    def start(self) -> McpClient:
        client = McpClient(self.shim, self.environment)
        self.addCleanup(client.close)
        client.initialize()
        return client


class TransportTests(TransportCase):
    def test_initialization_and_discovery_keep_the_legacy_tools(self):
        client = McpClient(self.shim, self.environment)
        self.addCleanup(client.close)
        info = client.initialize()
        self.assertEqual(info['serverInfo']['name'], 'Claude Implementer')
        tools = client.tools()
        self.assertTrue(OLD_REQUIRED_TOOLS <= set(tools), set(tools))
        self.assertTrue(NEW_TOOLS <= set(tools), set(tools))
        implement = tools['implement_changes']['inputSchema']
        self.assertEqual(set(implement['required']), {'task', 'scope', 'acceptance_criteria'})
        self.assertTrue({'resume_job_id', 'contract'} <= set(implement['properties']))
        self.assertEqual(tools['implementation_status']['inputSchema']['required'], ['job_id'])
        review = tools['review_changes']['inputSchema']
        self.assertEqual(set(review['required']), {'task', 'scope', 'acceptance_criteria'})
        self.assertNotIn('resume_job_id', review['properties'])
        self.assertIn('additional_read_roots', review['properties'])
        self.assertTrue(tools['review_changes']['annotations']['readOnlyHint'])
        self.assertIn('reviewed_source_digest', tools['record_review']['inputSchema']['properties'])

    def test_status_preflight_and_review_validation_over_the_transport(self):
        client = self.start()
        self.assertEqual(client.call('implementation_status', {'job_id': 'missing'}), (False, {'status': 'unknown_job'}))
        self.assertEqual(client.call('list_receipts'), (False, {'receipts': [], 'truncated': False}))
        failed, text = client.call('implement_changes', {'task': '', 'scope': '', 'acceptance_criteria': ''})
        self.assertTrue(failed, text)
        failed, text = client.call('implement_changes', {'task': 't', 'scope': 's', 'acceptance_criteria': 'c',
                                                         'contract': {'task_id': 'FLOW-01'}})
        self.assertTrue(failed, text)
        failed, text = client.call('record_review', {'job_id': 'missing', 'verdict': 'inconclusive',
                                                     'reviewed_source_digest': ''})
        self.assertTrue(failed, text)
        failed, payload = client.call('capture_source_state', {'source_paths': []})
        self.assertFalse(failed)
        self.assertRegex(payload['digest'], r'^[0-9a-f]{64}$')
        self.assertEqual(payload['state'], 'captured')
        store = self.store_base / '.receipts'
        self.assertEqual(list(store.glob('*.json')) if store.is_dir() else [], [],
                         'refused calls must not create receipts')

    def test_legacy_dispatch_status_and_review_with_a_faked_cli(self):
        client = self.start()
        failed, started = client.call('implement_changes', {'task': 'PROMPT-TASK-MARKER',
                                                            'scope': 'PROMPT-SCOPE-MARKER',
                                                            'acceptance_criteria': 'PROMPT-CRITERIA-MARKER'})
        self.assertFalse(failed, started)
        self.assertEqual((started['status'], started['binding']), ('running', 'unbound'))
        job_id = started['job_id']
        status = None
        for _ in range(300):
            failed, status = client.call('implementation_status', {'job_id': job_id})
            self.assertFalse(failed, status)
            if status['status'] != 'running':
                break
            time.sleep(0.1)
        self.assertEqual(status['status'], 'completed', status)
        self.assertEqual(status['human_acceptance'], {'state': 'not_recorded'})
        failed, digest = client.call('capture_source_state')
        failed, review = client.call('record_review', {
            'job_id': job_id, 'verdict': 'no_blocking_concerns', 'reviewer': 'codex',
            'reviewed_source_digest': digest['digest'],
            'verification': [{'command': 'python -B -m unittest', 'outcome': 'passed', 'exit_code': 0}]})
        self.assertFalse(failed, review)
        self.assertEqual(review['review_count'], 1)
        self.assertEqual(review['human_acceptance'], {'state': 'not_recorded'})
        failed, listing = client.call('list_receipts')
        self.assertEqual([(item['state'], item['review_count']) for item in listing['receipts']],
                         [('completed', 1)])
        stored = (self.store_base / '.receipts' / f'{job_id}.json').read_text('utf-8')
        for forbidden in ('TRANSPORT-RESULT', 'PROMPT-TASK-MARKER', 'PROMPT-SCOPE-MARKER',
                          'PROMPT-CRITERIA-MARKER'):
            self.assertNotIn(forbidden, stored)

    def test_restart_recovery_reports_interrupted_without_relaunching(self):
        record = receipts.new_record('left-running', 'dead-instance',
                                     {'root': str(self.root), 'branch': 'master', 'head': 'a' * 40},
                                     source_paths=[])
        receipts.ReceiptStore(self.store_base).create(record)
        client = self.start()
        failed, status = client.call('implementation_status', {'job_id': 'left-running'})
        self.assertFalse(failed, status)
        self.assertEqual(status['status'], 'interrupted')
        self.assertIn('Nothing was resumed or relaunched', status['message'])
        failed, listing = client.call('list_receipts')
        self.assertEqual([item['state'] for item in listing['receipts']], ['interrupted'])
        failed, again = client.call('implementation_status', {'job_id': 'left-running'})
        self.assertEqual(again['status'], 'interrupted')  # stable: no relaunch, no state change


if __name__ == '__main__':
    unittest.main()
