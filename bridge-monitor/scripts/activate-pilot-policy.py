"""Inspect or activate a reviewed project-local pilot policy.

Examples:
  python scripts/activate-pilot-policy.py --policy config/pilot-policy.toml \
      --runtime config/runtime-capabilities.toml --show-hash
  python scripts/activate-pilot-policy.py --policy config/pilot-policy.toml \
      --runtime config/runtime-capabilities.toml --approved-hash SHA256 \
      --operator-id OPERATOR
"""

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.runtime.policy_startup import activate_policy
from bridge.startup import RuntimeCapabilities
from contracts.policy import validate_policy


PROJECT = Path(__file__).resolve().parents[1]


def project_file(value: str) -> Path:
    path = Path(value).resolve(strict=True)
    if not path.is_relative_to(PROJECT) or not path.is_file():
        raise ValueError("policy and runtime files must be inside bridge-monitor")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--show-hash", action="store_true")
    parser.add_argument("--approved-hash")
    parser.add_argument("--operator-id")
    args = parser.parse_args(argv)
    if args.show_hash == bool(args.approved_hash):
        parser.error("choose exactly one of --show-hash or --approved-hash")
    try:
        with project_file(args.policy).open("rb") as source:
            proposed = tomllib.load(source)
        with project_file(args.runtime).open("rb") as source:
            runtime = RuntimeCapabilities.model_validate(tomllib.load(source))
        paid_model = proposed.get("fallback", {}).get("paid_model_profile", "")
        capabilities = {paid_model: {"context_tokens": runtime.claude.context_tokens}}
        validation = validate_policy(proposed, capabilities)
        if validation.snapshot is None:
            raise ValueError("; ".join(validation.errors))
        if args.show_hash:
            print(json.dumps({"version": validation.snapshot.version,
                              "source_hash": validation.snapshot.source_hash,
                              "review": validation.snapshot.review.model_dump()}, sort_keys=True))
            return 0
        dsn = os.environ.get("BRIDGE_MONITOR_DSN")
        if not dsn or not args.operator_id:
            parser.error("BRIDGE_MONITOR_DSN and --operator-id are required for activation")
        snapshot = activate_policy(dsn, proposed, capabilities,
            approved_hash=args.approved_hash, operator_id=args.operator_id)
        print(json.dumps({"version": snapshot.version, "source_hash": snapshot.source_hash,
                          "approval_reference": snapshot.review.approval_reference}, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"pilot policy rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
