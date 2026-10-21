"""Record explicit direct-task metadata and, optionally, one supplied Codex CLI turn.

No transcript discovery, inference, routing or execution. Database credentials come
from the existing launcher settings or an explicitly named environment variable.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bridge.direct_codex import parse_codex_turn, record_direct_task  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path,
                        help="Explicit DirectExecutionReport JSON file")
    parser.add_argument("--receipt", type=Path, help="Explicit codex exec --json output file")
    parser.add_argument("--turn-index", type=int, help="Zero-based turn selection; required for multi-turn files")
    parser.add_argument("--source-turn-id", help="Stable unique turn identity in this session; reuse on retries")
    parser.add_argument("--dsn-env", help="Existing environment variable naming the target database")
    args = parser.parse_args()
    if bool(args.receipt) != bool(args.source_turn_id) or (args.turn_index is not None and not args.receipt):
        parser.error("receipt requires source-turn-id; turn-index requires receipt")
    try:
        if args.report.stat().st_size > 16384:
            raise ValueError("report exceeds 16 KiB")
        report = json.loads(args.report.read_text(encoding="utf-8-sig"))
        usage = None
        if args.receipt:
            if args.receipt.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("receipt exceeds 16 MiB")
            usage = parse_codex_turn(args.receipt.read_bytes(),
                                     turn_index=args.turn_index)
        if args.dsn_env:
            dsn = os.environ.get(args.dsn_env)
        else:
            settings = json.loads((PROJECT / "var/secrets/desktop-mcp.json").read_text())
            dsn = settings.get("dsn")
        if not dsn:
            raise ValueError("database settings unavailable")
        result = record_direct_task(dsn, producer_id="bridge-monitor-parent", report=report,
                                    usage=usage, source_turn_id=args.source_turn_id)
    except ValidationError:
        print("recording rejected: invalid report or receipt fields", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"recording rejected: {exc}", file=sys.stderr)
        return 2
    except Exception:
        # Database exception strings can contain connection credentials.
        print("recording failed; check database availability and migrations (no credentials logged)",
              file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
