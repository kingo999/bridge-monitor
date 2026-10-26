import json

import pytest

from bridge.adapters.claude_cli import ClaudeCLIAdapter
from bridge.runtime.codex_claude_execution import execute_profile
from contracts.codex_claude import ClaudeProfile
from contracts.work import DispatchLease, TaskPacket


class RecordedRunner:
    returncode = 0
    terminated = True

    def __init__(self, reply):
        self.reply = reply

    def stream(self, command, prompt):
        yield json.dumps({'type': 'result', 'model': 'claude-opus-5-5',
                         'result': self.reply, 'is_error': False})


class Sink:
    def __init__(self):
        self.events = []

    def emit(self, kind, **kwargs):
        self.events.append((kind, kwargs))
        return True


def execute(reply):
    profile = ClaudeProfile(model_id='claude-opus-5-5')
    adapter = ClaudeCLIAdapter(RecordedRunner(reply), executable='claude', model='sonnet',
                               mcp_config_path='{"mcpServers":{}}',
                               profile_models=(profile.model_id,))
    lease = DispatchLease(lease_id='lease-test', call_id='call-test',
                          owner_incarnation='test-owner', fencing_token=1, policy_version='test')
    task = TaskPacket(task_id='task-test', work_item_id='work-test', category='analysis',
                      scope=['analysis'], token_budget=1000, call_budget=1)
    return execute_profile(None, adapter, lease, task, 'Review supplied code', Sink(), profile)


CLAIM = {'answer': 'Verified source finding.\nNo tests run.', 'local_results': [],
         'route_choice': 'paid_direct', 'fallback_child_call_id': None,
         'work_resolution': 'resolved'}


@pytest.mark.parametrize('opening,newline', [('```json', '\n'), ('```', '\n'), ('```JSON', '\r\n')])
def test_bounded_profile_delivers_single_fenced_completion_preserving_raw_reply(opening, newline):
    raw = ' \n' + opening + newline + json.dumps(CLAIM) + newline + '```\n '
    result = execute(raw)
    assert result.state == 'completed'
    assert result.answer == 'Verified source finding.\nNo tests run.'
    assert result.completion_claim == CLAIM
    assert result.raw_answer == raw
    assert result.reported_model == 'claude-opus-5-5'
    assert result.local_tool_attempts == 0


@pytest.mark.parametrize('raw', [
    '```json\n' + json.dumps(CLAIM) + '\n```\nI changed my answer.',
    'Prose before.\n```json\n' + json.dumps(CLAIM) + '\n```',
    '```json\n' + json.dumps(CLAIM) + '\n' + json.dumps(CLAIM) + '\n```',
    '```json\n' + json.dumps(CLAIM) + '\n```\n```json\n' + json.dumps(CLAIM) + '\n```',
    '```python\n' + json.dumps(CLAIM) + '\n```',
    '```json\n{"answer":"missing accounting"}\n```',
    '```json\n{"answer":\n```',
])
def test_bounded_profile_rejects_ambiguous_or_invalid_fenced_completions(raw):
    result = execute(raw)
    assert result.state == 'uncertain'
    assert result.uncertainty == 'bounded_result_validation_failed'
