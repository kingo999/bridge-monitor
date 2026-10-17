"""Preview or apply the active policy's content retention to one project database."""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.runtime.policy_startup import active_policy
from bridge.runtime.reconciliation import _authenticate
from service.maintenance.retention import apply_retention


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dsn = os.environ.get("BRIDGE_MONITOR_DSN")
    token = os.environ.get("BRIDGE_MONITOR_OPERATOR_TOKEN")
    if not dsn or not token:
        parser.error("BRIDGE_MONITOR_DSN and BRIDGE_MONITOR_OPERATOR_TOKEN are required")
    _authenticate("operator", args.actor_id, token)
    policy = active_policy(dsn)
    if not policy:
        parser.error("an active reviewed policy is required")
    cutoff = datetime.now(timezone.utc) - timedelta(days=policy.deployment.retention_days)
    if args.apply:
        outcome = apply_retention(dsn, retention_days=policy.deployment.retention_days)
    else:
        with psycopg.connect(dsn) as conn:
            bodies = conn.execute("SELECT count(*) FROM content_objects "
                "WHERE created_at<%s AND raw_bytes IS NOT NULL AND capture_layer<>'registry'",
                (cutoff,)).fetchone()[0]
            publications = conn.execute("SELECT count(*) FROM publication_stream "
                "WHERE published_at<%s", (cutoff,)).fetchone()[0]
        outcome = {"preview": True, "cutoff_at": cutoff,
                   "candidate_content_upper_bound": bodies,
                   "candidate_publications": publications}
    print(json.dumps(outcome, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
