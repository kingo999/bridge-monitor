"""Run a pre-registered local-first holdout outside production bridge telemetry."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bridge.adapters.qwen_agent import QwenAgentAdapter  # noqa: E402
from bridge.local_first import ModelReply, load_settings  # noqa: E402
from bridge.local_first_runner import run_cases  # noqa: E402
from contracts.policy import PolicyInput  # noqa: E402


class Sink:
    def emit(self, *_args, **_kwargs):
        return True


def sonnet_call(prompt: str, *, model: str, settings) -> ModelReply:
    command = ["claude", "--print", "--output-format", "json", "--model", model,
        "--no-session-persistence", "--restricted", "--strict-mcp-config",
        "--settings", '{"disableAllHooks":true}', "--disable-slash-commands",
        "--permission-mode", "dontAsk", "--permission-prompts", "none", "--tools", "",
        "--disallowedTools", "Bash", "PowerShell", "Edit", "Write", "NotebookEdit",
        "Agent", "Task", "ToolSearch", "WebFetch", "--max-budget-usd",
        str(settings.paid_max_budget_usd)]
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    started = time.monotonic()
    try:
        process = subprocess.run(command, input=prompt, text=True, capture_output=True,
            encoding="utf-8", errors="replace", cwd=PROJECT, env=env,
            timeout=settings.paid_timeout_seconds)
        elapsed = round(time.monotonic() - started, 3)
        if process.returncode:
            return ModelReply(state="failed", requested_model=model,
                uncertainty=f"claude_cli_exit_{process.returncode}", elapsed_seconds=elapsed)
        frame = json.loads(process.stdout)
        model_usage = frame.get("modelUsage") or {}
        reported = next(iter(model_usage)) if len(model_usage) == 1 else None
        usage = frame.get("usage") or {}
        counts = {key: usage[key] for key in ("input_tokens", "cache_read_input_tokens",
                  "cache_creation_input_tokens", "output_tokens")
                  if isinstance(usage.get(key), int) and usage[key] >= 0}
        model_record = model_usage.get(reported, {}) if reported else {}
        list_cost = frame.get("total_cost_usd")
        return ModelReply(state="failed" if frame.get("is_error") else "completed",
            raw=frame.get("result"), requested_model=model, reported_model=reported,
            usage=counts, elapsed_seconds=elapsed,
            list_cost_usd=list_cost if isinstance(list_cost, (int, float)) else None,
            cost_basis=model_record.get("costBasis"))
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        return ModelReply(state="uncertain", requested_model=model,
            uncertainty=type(exc).__name__,
            elapsed_seconds=round(time.monotonic() - started, 3))


def qwen_call(prompt: str, *, adapter: QwenAgentAdapter) -> ModelReply:
    call_id = str(uuid.uuid4())
    lease = SimpleNamespace(call_id=call_id, lease_id=str(uuid.uuid4()),
                            fencing_token=1, policy_version="offline-local-first")
    started = time.monotonic()
    result = adapter.run(lease, prompt, Sink())
    return ModelReply(state=result.state, raw=result.answer,
        requested_model=adapter.model, reported_model=result.reported_model,
        usage=result.usage, uncertainty=result.uncertainty,
        elapsed_seconds=round(time.monotonic() - started, 3))


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
    token = os.environ.get(policy.deployment.qwen_agent_token_ref.removeprefix("env:"))
    if not token:
        token = (PROJECT / "var" / "secrets" / "skynet-agent-token").read_text(
            encoding="utf-8").strip()
    if not token:
        raise SystemExit("agent credential unavailable")
    deployment = policy.deployment
    with httpx.Client() as client:
        adapter = QwenAgentAdapter(client, endpoint=deployment.qwen_agent_endpoint,
            token=token, model=deployment.qwen_model_profile,
            expected_digest=deployment.qwen_model_digest,
            context_tokens=capabilities["qwen"]["context_tokens"],
            output_tokens=min(settings.local_output_tokens, deployment.qwen_output_tokens),
            timeout_seconds=min(settings.max_local_seconds, policy.limits.qwen_execution_seconds),
            first_output_seconds=min(deployment.qwen_first_output_seconds,
                                     settings.max_local_seconds / 2),
            idle_output_seconds=min(deployment.qwen_idle_output_seconds,
                                    settings.max_local_seconds / 2),
            ack_seconds=deployment.qwen_ack_seconds,
            poll_seconds=deployment.qwen_poll_seconds,
            poll_timeout_seconds=deployment.qwen_poll_timeout_seconds,
            poll_failures=deployment.qwen_poll_failures,
            keep_alive=deployment.qwen_keep_alive,
            temperature=deployment.qwen_temperature)
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
