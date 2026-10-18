"""Freeze a natural pre-solution Python fix and trusted test vectors."""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bridge.router.cases import register_case
from bridge.runtime.policy_startup import active_policy
from contracts.router import RouterCase


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True,
                        help="Project-local JSON snapshot captured before a solution exists")
    args = parser.parse_args()
    source = Path(args.case).resolve(strict=True)
    if not source.is_file() or not source.is_relative_to(PROJECT):
        raise ValueError("case input must be a project-local file")
    dsn = os.environ.get("BRIDGE_MONITOR_DSN")
    if not dsn:
        raise ValueError("BRIDGE_MONITOR_DSN is required")
    policy = active_policy(dsn)
    if not policy or policy.router.mode != "shadow":
        raise ValueError("reviewed shadow router policy must be active")
    case = RouterCase.model_validate_json(source.read_bytes())
    receipt = register_case(PROJECT, case, policy.router)
    print(json.dumps(receipt.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
