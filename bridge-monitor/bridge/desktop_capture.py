"""Forward-only desktop hook capture. This module never invokes a model or changes routing."""

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from contracts.direct_execution import CodexTurnReceipt

MAX_RANGE = 8 * 1024 * 1024
DELEGATION = {"mcp__bridge_monitor__route_work", "mcp__bridge_monitor__delegate_claude",
              "mcp__claude_implementer_internal__implement_changes",
              "mcp__claude_implementer_internal__review_changes", "spawn_agent", "Agent", "Task"}


def tool_kind(name):
    if name in DELEGATION or name.startswith(('mcp__gpt_architect', 'mcp__gpt-architect', 'collab')):
        return 'delegated'
    if name.startswith('mcp__bridge_monitor__'):
        return 'direct' if name.rsplit('__', 1)[-1] in (
            'record_direct_task', 'get_execution_status', 'cancel_work',
            'acknowledge_claude_result', 'complete_work') else 'unknown'
    if name.startswith(('mcp__node_repl__', 'mcp__cua_repl__', 'mcp__codex_app__')):
        return 'direct'
    if name.startswith('mcp__'):
        return 'unknown'
    return 'direct' if name in ('Bash', 'apply_patch', 'Edit', 'Write', 'exec_command',
        'write_stdin', 'exec', 'wait', 'functions.exec', 'functions.wait', 'web.run',
        'web__run', 'view_image', 'request_user_input', 'request_user_input_async') else 'unknown'


def _canonical(path):
    text = str(Path(path).resolve(strict=True))
    prefix = chr(92) * 2 + '?' + chr(92)
    if os.name == 'nt' and text.startswith(prefix):
        tail = text[len(prefix):]
        if tail.startswith('UNC' + chr(92)):
            text = chr(92) * 2 + tail[4:]
        elif len(tail) >= 3 and tail[1] == ':':
            text = tail
    return Path(text)


def transcript_header(path, codex_home, session_id):
    home = _canonical(codex_home)
    root = _canonical(home / 'sessions')
    path = _canonical(path)
    if not root.is_relative_to(home) or not path.is_relative_to(root) or not path.is_file():
        raise ValueError('transcript_outside_sessions')
    with path.open('rb') as f:
        raw = f.readline(65537)
    if len(raw) > 65536:
        raise ValueError('session_header_too_large')
    frame = json.loads(raw.decode('utf-8-sig'))
    header = frame.get('payload') or {}
    if frame.get('type') != 'session_meta' or header.get('id') != session_id:
        raise ValueError('session_identity_mismatch')
    return path, header


def read_desktop_turn(path, *, codex_home, session_id, turn_id, start_offset,
                      model_hint=None):
    path, header = transcript_header(path, codex_home, session_id)
    result = {'status': 'unknown', 'usage': None, 'reason': 'awaiting_completion', 'observed_at': None}
    if header.get('originator') != 'Codex Desktop':
        return {**result, 'reason': 'not_desktop'}
    if type(start_offset) is not int or start_offset < 0 or path.stat().st_size < start_offset:
        raise ValueError('invalid_capture_offset')
    with path.open('rb') as f:
        f.seek(start_offset); raw = f.read(MAX_RANGE + 1)
    if len(raw) > MAX_RANGE:
        return {**result, 'reason': 'capture_range_exceeded'}
    responses, models, final_usage = {}, set(), None
    consumed = 0
    for line in raw.splitlines(keepends=True):
        consumed += len(line)
        if not line.endswith(b'\n'):
            return {**result, 'reason': 'partial_line'}
        try:
            frame = json.loads(line)
        except ValueError:
            return {**result, 'reason': 'unrecognized_transcript'}
        payload = frame.get('payload') or {}
        if not isinstance(payload, dict):
            return {**result, 'reason': 'unrecognized_transcript'}
        kind = frame.get('type')
        if kind == 'response_item' and str(payload.get('type', '')).endswith('_call'):
            classification = (tool_kind(str(payload.get('name', '')))
                if payload['type'] in ('function_call', 'custom_tool_call') else 'unknown')
            if classification != 'direct':
                return {**result, 'reason': 'delegation_observed' if classification == 'delegated'
                        else 'tool_coverage_unknown'}
        if payload.get('turn_id') != turn_id:
            continue
        if kind == 'turn_context' and isinstance(payload.get('model'), str):
            models.add(payload['model'])
        if kind == 'token_usage_record':
            if payload.get('session_id') != session_id or payload.get('thread_id') != session_id:
                return {**result, 'reason': 'usage_identity_mismatch'}
            response_id, usage = payload.get('response_id'), payload.get('usage')
            if not isinstance(response_id, str) or not isinstance(usage, dict):
                return {**result, 'reason': 'turn_usage_unavailable'}
            if response_id in responses and responses[response_id] != usage:
                return {**result, 'reason': 'response_usage_conflict'}
            responses[response_id] = usage
            final_usage = payload.get('turn_token_usage')
        if kind == 'event_msg' and payload.get('type') in ('task_complete', 'turn_aborted'):
            result.update(status='cancelled' if payload['type'] == 'turn_aborted' else
                          'failed' if payload.get('error') else 'completed',
                          observed_at=frame.get('timestamp'), reason='usage_unavailable')
            break
    else:
        return result
    if result['status'] != 'completed' or not isinstance(final_usage, dict) or not responses:
        return result
    # Validate measured turn totals against deduplicated response-level counters.
    fields = {'input_tokens': 'input_tokens', 'output_tokens': 'output_tokens',
              'cache_read_input_tokens': 'cached_input_tokens',
              'cache_creation_input_tokens': 'cache_write_input_tokens',
              'reasoning_output_tokens': 'reasoning_output_tokens'}
    values = {}
    for target, key in fields.items():
        value = final_usage.get(key)
        if value is not None:
            if type(value) is not int or value < 0:
                return {**result, 'reason': 'invalid_usage'}
            counters = [r.get(key) for r in responses.values()]
            if any(type(n) is not int or n < 0 for n in counters) or sum(counters) != value:
                return {**result, 'reason': 'incomplete_usage_range'}
        values[target] = value
    if values['input_tokens'] is None or values['output_tokens'] is None:
        return result
    model = next(iter(models)) if len(models) == 1 else model_hint if not models else None
    receipt = CodexTurnReceipt(session_id=session_id,
        source_sha256=hashlib.sha256(raw[:consumed]).hexdigest(),
        provenance='codex_desktop_usage_record', runtime_model=model,
        runtime_provider=header.get('model_provider'), **values)
    return {**result, 'usage': receipt, 'reason': 'measured_turn'}


def _state(path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=0.3)
    conn.execute('CREATE TABLE IF NOT EXISTS turns (session_id TEXT, turn_id TEXT, '
                 'data TEXT NOT NULL, PRIMARY KEY(session_id,turn_id))')
    return conn


def capture_hook(payload, *, codex_home, state_path):
    """Checkpoint structural fields only; prompt/tool bodies are never retained."""
    event = payload.get('hook_event_name')
    session, turn = payload.get('session_id'), payload.get('turn_id')
    if not all(isinstance(s, str) and 0 < len(s) <= 200 for s in (session, turn)):
        return {'state': 'ignored', 'reason': 'turn_identity_unavailable'}
    now = datetime.now(timezone.utc).isoformat()
    with _state(state_path) as conn:
        row = conn.execute('SELECT data FROM turns WHERE session_id=? AND turn_id=?', (session, turn)).fetchone()
        if event == 'UserPromptSubmit':
            if row:
                return {'state': 'duplicate'}
            path = payload.get('transcript_path')
            if not isinstance(path, str):
                return {'state': 'ignored', 'reason': 'transcript_unavailable'}
            path, header = transcript_header(path, codex_home, session)
            if header.get('originator') != 'Codex Desktop':
                return {'state': 'ignored', 'reason': 'not_desktop'}
            cwd = payload.get('cwd')
            if not isinstance(cwd, str) or not cwd or len(cwd) > 1024:
                return {'state': 'ignored', 'reason': 'project_unavailable'}
            data = dict(path=str(path), offset=path.stat().st_size, project_root=cwd,
                model=payload.get('model'), started_at=now, ended_at=None,
                delegated=False, unknown_tool=False, interrupted=False, recorded=False,
                finalized=False, attempts=0)
        elif not row:
            return {'state': 'ignored', 'reason': 'no_forward_checkpoint'}
        else:
            data = json.loads(row[0])
            if event == 'PostToolUse':
                kind = tool_kind(str(payload.get('tool_name', '')))
                data['delegated'] |= kind == 'delegated'
                data['unknown_tool'] |= kind == 'unknown'
            elif event in ('Stop', 'Interrupt'):
                data['ended_at'] = data['ended_at'] or now
                data['interrupted'] |= event == 'Interrupt'
            else:
                return {'state': 'ignored'}
        conn.execute('INSERT INTO turns VALUES(?,?,?) ON CONFLICT(session_id,turn_id) '
                     'DO UPDATE SET data=excluded.data', (session, turn, json.dumps(data)))
    return {'state': 'queued'}


def flush_capture(*, dsn, codex_home, state_path, limit=8):
    """Replay only paths captured by hooks; never discover historical transcripts."""
    from bridge.direct_codex import record_direct_task
    from psycopg.conninfo import make_conninfo
    from service.queries.direct_execution import execution_metadata
    import psycopg
    with _state(state_path) as conn:
        rows = conn.execute("SELECT session_id,turn_id,data FROM turns "
            "WHERE json_extract(data,'$.ended_at') IS NOT NULL "
            "AND COALESCE(json_extract(data,'$.finalized'),0)=0 ORDER BY rowid DESC LIMIT ?",
            (limit,)).fetchall()
    results = []
    for session, turn, raw in rows:
        data = json.loads(raw)
        if not data['ended_at'] or data.get('finalized'):
            continue
        data['attempts'] = data.get('attempts', 0) + 1
        try:
            if data['delegated'] or data['unknown_tool']:
                data.update(finalized=True, outcome='delegated' if data['delegated'] else 'coverage_unknown')
            else:
                obs = read_desktop_turn(data['path'], codex_home=codex_home, session_id=session,
                    turn_id=turn, start_offset=data['offset'], model_hint=data['model'])
                if obs['reason'] in ('delegation_observed', 'tool_coverage_unknown', 'not_desktop'):
                    data.update(finalized=True, outcome=obs['reason'])
                elif obs['status'] == 'unknown':
                    data['outcome'] = obs['reason']
                    if data['attempts'] >= 8:
                        data['finalized'] = True  # A documented gap, never a fabricated completion.
                else:
                    execution = f'codex-desktop:{session}:{turn}'
                    observed_at = obs['observed_at'] or data['ended_at']
                    report = dict(execution_id=execution,
                        session_id=session, project_root=data['project_root'],
                        summary='Direct Codex desktop turn', status=obs['status'],
                        bypass_reason='not_reported', reason='No structured bypass reason was reported; no approval inferred.',
                        requested_model=data['model'], observed_at=observed_at)
                    work = 'direct:' + str(uuid.uuid5(uuid.NAMESPACE_URL, execution))
                    with psycopg.connect(make_conninfo(dsn, connect_timeout=2)) as pg:
                        existing = execution_metadata(pg, [work], datetime.now(timezone.utc))[work].get('direct_execution')
                    if existing and existing['bypass_reason'] != 'not_reported':
                        for key in ('bypass_reason', 'reason', 'summary', 'requested_model'):
                            report[key] = existing.get(key)
                    identity = {'report': report, 'usage': obs['usage'].model_dump() if obs['usage'] else None}
                    report['record_id'] = 'desktop:' + hashlib.sha256(
                        json.dumps(identity, sort_keys=True).encode()).hexdigest()
                    record_direct_task(make_conninfo(dsn, connect_timeout=2),
                        producer_id='bridge-monitor-parent', report=report, usage=obs['usage'],
                        source_turn_id=turn if obs['usage'] else None,
                        observation_layer='codex_desktop_hook')
                    data.update(recorded=True, outcome=obs['reason'], finalized=obs['usage'] is not None)
                    if data['attempts'] >= 8:
                        data['finalized'] = True
        except Exception as exc:
            # No exception text: it can contain input or database credentials.
            data['outcome'] = 'capture_deferred_' + type(exc).__name__
        with _state(state_path) as conn:
            conn.execute('UPDATE turns SET data=? WHERE session_id=? AND turn_id=?',
                         (json.dumps(data), session, turn))
        results.append(dict(session_id=session, turn_id=turn, outcome=data.get('outcome'), recorded=data['recorded']))
    return results
