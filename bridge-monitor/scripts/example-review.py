"""Record one authenticated, append-only human example-use review.

The JSON input names pack_version_id, example_id, reviewer_id, status,
permitted_use, exposure, scope, evidence and optional supersedes_id.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.ingest.example_reviews import record_example_review  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", required=True, help="JSON file inside bridge-monitor")
    args = parser.parse_args(argv)
    try:
        path = Path(args.review).resolve(strict=True)
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError("review file must stay inside bridge-monitor")
        payload = json.loads(path.read_text(encoding="utf-8"))
        dsn = os.environ.get("BRIDGE_MONITOR_DSN")
        token = os.environ.get("BRIDGE_MONITOR_OPERATOR_TOKEN")
        if not dsn or not token:
            raise ValueError("BRIDGE_MONITOR_DSN and BRIDGE_MONITOR_OPERATOR_TOKEN are required")
        receipt = record_example_review(dsn, payload["pack_version_id"], payload["example_id"],
            reviewer_id=payload["reviewer_id"], auth_token=token, status=payload["status"],
            permitted_use=payload["permitted_use"], exposure=payload["exposure"],
            scope=payload["scope"], evidence=payload["evidence"],
            supersedes_id=payload.get("supersedes_id"))
        print(json.dumps(receipt.__dict__, sort_keys=True))
        return 0
    except (KeyError, OSError, ValueError, PermissionError) as exc:
        print(f"example review rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
