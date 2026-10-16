"""Authenticated operator reconciliation of one held execution.

Example: python scripts/reconcile-execution.py --call-id ... --evidence-file receipt.json
Requires BRIDGE_MONITOR_DSN and BRIDGE_MONITOR_OPERATOR_TOKEN in the environment.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.runtime.recovery import reconcile_owner_and_recover


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--call-id", required=True)
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--actor-id", required=True)
    args = parser.parse_args()
    dsn = os.environ.get("BRIDGE_MONITOR_DSN")
    token = os.environ.get("BRIDGE_MONITOR_OPERATOR_TOKEN")
    if not dsn or not token:
        parser.error("BRIDGE_MONITOR_DSN and BRIDGE_MONITOR_OPERATOR_TOKEN are required")
    evidence = json.loads(Path(args.evidence_file).read_text(encoding="utf-8"))
    outcome = reconcile_owner_and_recover(dsn, args.call_id, evidence,
        actor_kind="operator", actor_id=args.actor_id, auth_token=token)
    print(json.dumps({"call_id": args.call_id, **outcome}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
