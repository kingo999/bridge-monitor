import hashlib
import importlib
import json
import os
import runpy

import pytest


def fixture(tmp_path, *, delegated=False, thread_only=False, complete=True):
    home = tmp_path / 'codex'
    path = home / 'sessions' / 'active.jsonl'
    path.parent.mkdir(parents=True)
    header = {'type': 'session_meta', 'payload': {'id': 'session-1',
        'originator': 'Codex Desktop', 'model_provider': 'openai'}}
    path.write_text(json.dumps(header) + '\n')
    offset = path.stat().st_size
    data = [{'type': 'turn_context', 'payload': {'turn_id': 'turn-1', 'model': 'runtime-model'}}]
    usage = {'input_tokens': 12, 'output_tokens': 0, 'cached_input_tokens': 8,
             'reasoning_output_tokens': 0}
    data.append({'type': 'token_usage_record', 'payload': {'session_id': 'session-1',
        'thread_id': 'session-1', 'turn_id': 'turn-1', 'response_id': 'response-1',
        'usage': usage, 'turn_token_usage': usage, 'thread_token_usage': {'input_tokens': 999999}}})
    if thread_only:
        data[-1]['payload'].pop('usage'); data[-1]['payload'].pop('turn_token_usage')
    if delegated:
        data.append({'type': 'response_item', 'payload': {'type': 'function_call',
            'name': 'mcp__bridge_monitor__route_work', 'arguments': 'SECRET TOOL BODY'}})
    if complete:
        data.append({'type': 'event_msg', 'payload': {'type': 'task_complete',
            'turn_id': 'turn-1', 'last_agent_message': 'PRIVATE ANSWER'}})
    raw = ('\n'.join(json.dumps(d) for d in data) + '\n').encode()
    with path.open('ab') as f: f.write(raw)
    return home, path, offset, raw


def read(home, path, offset, **kwargs):
    return importlib.import_module('bridge.desktop_capture').read_desktop_turn(path,
        codex_home=home, session_id='session-1', turn_id='turn-1', start_offset=offset, **kwargs)


def test_real_turn_counters_keep_scope_identity_zero_and_provenance(tmp_path):
    home, path, offset, raw = fixture(tmp_path)
    observation = read(home, path, offset)
    assert observation['status'] == 'completed'
    usage = observation['usage']
    assert usage.input_tokens == 12 and usage.output_tokens == 0
    assert usage.cache_read_input_tokens == 8
    assert usage.cache_creation_input_tokens is None
    assert usage.provenance == 'codex_desktop_usage_record'
    assert usage.runtime_model == 'runtime-model' and usage.runtime_provider == 'openai'
    assert usage.source_sha256 == hashlib.sha256(raw).hexdigest()


def test_native_clock_hook_name_is_a_direct_management_operation():
    kind = importlib.import_module('bridge.desktop_capture').tool_kind
    assert kind('clockcurr_time') == 'direct'
    assert kind('clockworker') == 'unknown'


def test_session_totals_are_never_used_as_turn_usage(tmp_path):
    home, path, offset, _ = fixture(tmp_path, thread_only=True)
    assert read(home, path, offset)['usage'] is None


def test_delegated_and_incomplete_turns_are_not_claimed_direct(tmp_path):
    home, path, offset, _ = fixture(tmp_path, delegated=True)
    assert read(home, path, offset)['reason'] == 'delegation_observed'
    assert read(home, path, offset)['usage'] is None


def test_no_complete_marker_means_no_final_measurement(tmp_path):
    home, path, offset, _ = fixture(tmp_path, complete=False)
    assert read(home, path, offset)['reason'] == 'awaiting_completion'
    assert read(home, path, offset)['usage'] is None


@pytest.mark.parametrize('call_type,name,reason', [
    ('custom_tool_call', 'mcp__bridge_monitor__route_work', 'delegation_observed'),
    ('future_worker_call', 'worker', 'tool_coverage_unknown'),
])
def test_other_tool_call_shapes_cannot_hide_delegation(tmp_path, call_type, name, reason):
    home, path, offset, _ = fixture(tmp_path)
    lines = path.read_bytes().splitlines(keepends=True)
    call = json.dumps({'type': 'response_item', 'payload': {'type': call_type, 'name': name}}).encode() + b'\n'
    path.write_bytes(b''.join(lines[:-1] + [call, lines[-1]]))
    observation = read(home, path, offset)
    assert observation['reason'] == reason
    assert observation['usage'] is None


def test_wrong_session_and_outside_paths_are_rejected(tmp_path):
    home, path, offset, raw = fixture(tmp_path)
    outside = tmp_path / 'outside.jsonl'; outside.write_bytes(path.read_bytes())
    with pytest.raises(ValueError): read(home, outside, offset)
    path.write_bytes(path.read_bytes().replace(b'"id": "session-1"', b'"id": "session-2"'))
    with pytest.raises(ValueError): read(home, path, offset)


@pytest.mark.skipif(os.name != 'nt', reason='Windows extended path representation')
def test_desktop_extended_length_path_matches_canonical_session_root(tmp_path):
    home, path, offset, _ = fixture(tmp_path)
    extended = (chr(92) * 2 + '?' + chr(92)) + str(path.resolve())
    assert read(home, extended, offset)['usage'].input_tokens == 12


def test_duplicate_response_is_not_summed_twice(tmp_path):
    home, path, offset, _ = fixture(tmp_path)
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b''.join(lines[:3] + [lines[2]] + lines[3:]))
    assert read(home, path, offset)['usage'].input_tokens == 12


def test_hook_checkpoints_discard_prompt_and_tool_bodies(tmp_path):
    module = importlib.import_module('bridge.desktop_capture')
    home, path, _, _ = fixture(tmp_path)
    state = tmp_path / 'capture.sqlite'
    common = {'session_id': 'session-1', 'turn_id': 'turn-new', 'cwd': str(tmp_path),
              'transcript_path': str(path), 'model': 'runtime-model'}
    module.capture_hook({**common, 'hook_event_name': 'UserPromptSubmit', 'prompt': 'PRIVATE PROMPT'},
                        codex_home=home, state_path=state)
    module.capture_hook({**common, 'hook_event_name': 'PostToolUse', 'tool_name': 'Bash',
        'tool_use_id': 'tool-1', 'tool_input': {'command': 'SECRET COMMAND'},
        'tool_response': 'SECRET OUTPUT'}, codex_home=home, state_path=state)
    raw = state.read_bytes()
    for secret in (b'PRIVATE PROMPT', b'SECRET COMMAND', b'SECRET OUTPUT'):
        assert secret not in raw


def test_hook_diagnostics_are_bounded_and_structural(tmp_path):
    from pathlib import Path
    module = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts/capture-codex-desktop.py'))
    state = tmp_path / 'health.sqlite'
    for _ in range(40):
        module['diagnostic'](state, 'PostToolUse', 'no_forward_checkpoint')
    with importlib.import_module('bridge.desktop_capture')._state(state) as conn:
        rows = conn.execute('SELECT event,outcome FROM hook_diagnostics').fetchall()
    assert len(rows) == 32
    assert set(rows) == {('PostToolUse', 'no_forward_checkpoint')}
