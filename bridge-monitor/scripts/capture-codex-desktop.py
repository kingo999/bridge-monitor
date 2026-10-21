"""Codex command hook: structural capture only, with bounded, non-blocking replay."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge.desktop_capture import capture_hook, flush_capture, _state  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', required=True, type=Path)
    parser.add_argument('--state', type=Path, default=ROOT / 'var/spool/codex-desktop.sqlite')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--flush', action='store_true')
    parser.add_argument('--dsn-env')
    args = parser.parse_args()
    try:
        state = args.state.resolve()
        if not state.is_relative_to(ROOT / 'var'):
            raise ValueError('state must be inside project var')
        if args.status:
            with _state(state) as conn:
                rows = [json.loads(row[0]) for row in conn.execute('SELECT data FROM turns')]
            print(json.dumps({'turns': len(rows), 'recorded': sum(bool(r['recorded']) for r in rows),
                'pending': sum(not r.get('finalized') for r in rows),
                'outcomes': {key: sum(r.get('outcome') == key for r in rows)
                    for key in sorted({r.get('outcome', 'pending') for r in rows})},
                'hook_trust': 'Must be reviewed in Codex; not changed by this script'}))
            return 0
        event = None
        if not args.flush:
            raw = sys.stdin.buffer.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('hook_input_too_large')
            payload = json.loads(raw)
            event = payload.get('hook_event_name')
            capture_hook(payload, codex_home=args.codex_home, state_path=state)
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
    print('{}')  # Advisory only: no control fields, no extra model context.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
