"""Inspect a proposed policy hash or perform an authenticated soft reset.

Only project-local policy/runtime files are accepted. The active policy stays
unchanged when validation fails or a setting requires process reconstruction.
"""

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge.runtime.policy_reload import reload_policy  # noqa: E402
from bridge.startup import RuntimeCapabilities  # noqa: E402
from contracts.policy import validate_policy  # noqa: E402


def _local_file(value: str) -> Path:
    path = Path(value).resolve(strict=True)
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError("policy and runtime files must be inside bridge-monitor")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--runtime", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--inspect", action="store_true")
    action.add_argument("--approved-hash")
    parser.add_argument("--operator-id")
    args = parser.parse_args(argv)
    try:
        with _local_file(args.policy).open("rb") as source:
            proposed = tomllib.load(source)
        with _local_file(args.runtime).open("rb") as source:
            runtime = RuntimeCapabilities.model_validate(tomllib.load(source))
        paid = proposed.get("fallback", {}).get("paid_model_profile", "")
        capabilities = {paid: {"context_tokens": runtime.claude.context_tokens}}
        validation = validate_policy(proposed, capabilities)
        if validation.snapshot is None:
            raise ValueError("; ".join(validation.errors))
        if args.inspect:
            snapshot = validation.snapshot
            print(json.dumps({"version": snapshot.version, "source_hash": snapshot.source_hash,
                              "review": snapshot.review.model_dump()}, sort_keys=True))
            return 0
        dsn = os.environ.get("BRIDGE_MONITOR_DSN")
        token = os.environ.get("BRIDGE_MONITOR_OPERATOR_TOKEN")
        if not dsn or not token or not args.operator_id:
            parser.error("BRIDGE_MONITOR_DSN, BRIDGE_MONITOR_OPERATOR_TOKEN and --operator-id are required")
        outcome = reload_policy(dsn, proposed, capabilities, approved_hash=args.approved_hash,
                                operator_id=args.operator_id, auth_token=token)
        print(json.dumps(outcome.__dict__, sort_keys=True))
        return 0 if outcome.status == "activated" else 2
    except (OSError, ValueError, PermissionError) as exc:
        print(f"policy control rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
