"""Codex command hook: structural capture only, with bounded, non-blocking replay."""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge.desktop_capture import capture_hook, flush_capture, tool_kind, _state  # noqa: E402


def diagnostic(state, event, outcome):
    """Bounded structural health only: never persist input, exception text or paths."""
    known = ('UserPromptSubmit', 'PostToolUse', 'Stop', 'Interrupt', 'SessionEnd')
    event = event if event in known else 'unknown'
    with _state(state) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS hook_diagnostics '
                     '(id INTEGER PRIMARY KEY, event TEXT, outcome TEXT)')
        conn.execute('INSERT INTO hook_diagnostics(event,outcome) VALUES(?,?)', (event, outcome))
        conn.execute('DELETE FROM hook_diagnostics WHERE id NOT IN '
                     '(SELECT id FROM hook_diagnostics ORDER BY id DESC LIMIT 32)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', required=True, type=Path)
    parser.add_argument('--state', type=Path, default=ROOT / 'var/spool/codex-desktop.sqlite')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--flush', action='store_true')
    parser.add_argument('--dsn-env')
    args = parser.parse_args()
    event = None
    try:
        state = args.state.resolve()
        if not state.is_relative_to(ROOT / 'var'):
            raise ValueError('state must be inside project var')
        if args.status:
            with _state(state) as conn:
                rows = [json.loads(row[0]) for row in conn.execute('SELECT data FROM turns')]
                has_health = conn.execute("SELECT 1 FROM sqlite_master WHERE name='hook_diagnostics'").fetchone()
                health = conn.execute('SELECT event,outcome FROM hook_diagnostics ORDER BY id DESC LIMIT 8').fetchall() if has_health else []
            print(json.dumps({'turns': len(rows), 'recorded': sum(bool(r['recorded']) for r in rows),
                'pending': sum(not r.get('finalized') for r in rows),
                'outcomes': {key: sum(r.get('outcome') == key for r in rows)
                    for key in sorted({r.get('outcome', 'pending') for r in rows})},
                'recent_hooks': health,
                'hook_trust': 'Must be reviewed in Codex; not changed by this script'}))
            return 0
        event = None
        if not args.flush:
            raw = sys.stdin.buffer.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('hook_input_too_large')
            payload = json.loads(raw)
            event = payload.get('hook_event_name')
            result = capture_hook(payload, codex_home=args.codex_home, state_path=state)
            outcome = result.get('reason', result['state'])
            name = payload.get('tool_name')
            if event == 'PostToolUse' and isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}', name):
                outcome += ':' + tool_kind(name) + ':' + name
            diagnostic(state, event, outcome)
        if args.flush or event in ('Stop', 'SessionEnd'):
            if args.dsn_env:
                import os
                dsn = os.environ.get(args.dsn_env)
            else:
                dsn = json.loads((ROOT / 'var/secrets/desktop-mcp.json').read_text()).get('dsn')
            if not dsn:
                raise ValueError('database_settings_unavailable')
            for attempt in range(3 if event == 'Stop' else 1):
                results = flush_capture(dsn=dsn, codex_home=args.codex_home,
                                        state_path=state, limit=1 if event else 8)
                if not any(r['outcome'] in ('awaiting_completion', 'partial_line') for r in results):
                    break
                time.sleep(0.1)
            if args.flush:
                print(json.dumps(results))
                return 0
    except Exception as exc:
        # Never print exception text, prompts, tool results, paths or credentials.
        if args.flush or args.status:
            print(json.dumps({'state': 'deferred', 'error_type': type(exc).__name__}))
            return 1
        try:
            diagnostic(state, event, 'error_' + type(exc).__name__)
        except Exception:
            pass
    print('{}')  # Advisory only: no control fields, no extra model context.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
