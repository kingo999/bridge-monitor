"""Run a pre-registered local-first holdout outside production bridge telemetry."""

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path

import subprocess  # shared module object used by the existing CLI regression test

import httpx

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bridge.experiment_transports import (make_qwen_adapter, qwen_call,  # noqa: E402
                                          sonnet_call)
from bridge.local_first import load_settings  # noqa: E402
from bridge.local_first_runner import run_cases  # noqa: E402
from contracts.policy import PolicyInput  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", type=Path,
                        default=PROJECT / "config" / "local-first-holdout-20261018.toml")
    parser.add_argument("--cases", type=Path,
                        default=PROJECT / "tests" / "fixtures" / "local-first-holdout-20261018.json")
    parser.add_argument("--receipt", type=Path,
                        default=PROJECT / "var" / "probes" / "local-first-holdout-v2-20261018.json")
    parser.add_argument("--policy", type=Path,
                        default=PROJECT / "config" / "pilot-policy.skynet-agent-proposal.toml")
    parser.add_argument("--capabilities", type=Path,
                        default=PROJECT / "config" / "runtime-capabilities.codex-pilot.toml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for path in (args.settings, args.cases, args.receipt, args.policy, args.capabilities):
        if not path.resolve().is_relative_to(PROJECT):
            raise SystemExit("all trial files must be under the bridge-monitor project")
    settings = load_settings(args.settings)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    policy = PolicyInput.model_validate(tomllib.loads(args.policy.read_text(encoding="utf-8")))
    capabilities = tomllib.loads(args.capabilities.read_text(encoding="utf-8"))
    tracked_inputs = {"settings_sha256": args.settings, "fixture_sha256": args.cases,
        "policy_file_sha256": args.policy, "capabilities_sha256": args.capabilities,
        "router_sha256": PROJECT / "bridge" / "local_first.py",
        "runner_sha256": PROJECT / "bridge" / "local_first_runner.py",
        "script_sha256": Path(__file__)}
    metadata = {key: hashlib.sha256(path.read_bytes()).hexdigest()
                for key, path in tracked_inputs.items()}
    metadata.update({"qwen_model": policy.deployment.qwen_model_profile,
        "qwen_digest": policy.deployment.qwen_model_digest,
        "paid_model": policy.fallback.paid_model_profile})
    if settings.mode != "evaluation" or settings.canary_percent != 100:
        raise SystemExit("holdout requires explicit evaluation mode and 100% sample")
    if not policy.deployment.qwen_agent_endpoint:
        raise SystemExit("holdout requires the authenticated Skynet agent")
    if args.dry_run:
        print(json.dumps({"cases": len(cases), "settings": settings.model_dump(),
                          "qwen_model": policy.deployment.qwen_model_profile,
                          "paid_model": policy.fallback.paid_model_profile,
                          "receipt": str(args.receipt)}, sort_keys=True))
        return 0
    with httpx.Client() as client:
        adapter = make_qwen_adapter(client, policy=policy, capabilities=capabilities,
            output_tokens=settings.local_output_tokens,
            max_seconds=settings.max_local_seconds)
        preflight = adapter.preflight()
        if not preflight.ready:
            raise SystemExit("Skynet preflight failed: " + preflight.reason)
        receipt = run_cases(cases, settings, args.receipt,
            lambda prompt: qwen_call(prompt, adapter=adapter),
            lambda prompt: sonnet_call(prompt,
                model=policy.fallback.paid_model_profile, settings=settings),
            run_metadata=metadata)
    results = receipt["results"]
    accepted = sum(item["local_first"]["route"] == "local" for item in results.values())
    print(json.dumps({"completed": len(results), "local_accepted": accepted,
                      "paid_fallbacks": sum(item["local_first"]["local"] is not None and
                                            item["local_first"]["paid"] is not None
                                            for item in results.values()),
                      "receipt": str(args.receipt)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
