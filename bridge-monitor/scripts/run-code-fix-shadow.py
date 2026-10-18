"""Run an offline matched code-fix shadow trial; never deliver local output live."""

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bridge.code_fix_shadow_runner import (PROMPT_PREFIX, run_shadow_cases,  # noqa: E402
                                           validate_natural_case)
from bridge.code_fix_verifier import DockerVerifier, VerifierSettings  # noqa: E402
from bridge.experiment_transports import (codex_parent_call, make_qwen_adapter,  # noqa: E402
                                          qwen_call, sonnet_call)
from bridge.shadow_accounting import summarize_shadow  # noqa: E402
from bridge.shadow_cohort import check_new_case  # noqa: E402
from contracts.policy import PolicyInput  # noqa: E402


class ShadowSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["disabled", "shadow"] = "disabled"
    cohort: Literal["retrospective_shadow", "natural_shadow"] = "retrospective_shadow"
    max_cases: int = Field(default=2, ge=1, le=100)
    target_cases: int = Field(default=30, ge=1, le=1000)
    cohort_start_at: str | None = None
    excluded_case_ids: list[str] = Field(default_factory=list)
    excluded_baseline_sha256: list[str] = Field(default_factory=list)
    excluded_task_sha256: list[str] = Field(default_factory=list)
    prompt_prefix: str = Field(default=PROMPT_PREFIX, min_length=1)
    parent_model: str = Field(min_length=1)
    parent_timeout_seconds: float = Field(default=120, gt=0)
    local_output_tokens: int = Field(default=2048, gt=0)
    local_timeout_seconds: float = Field(default=120, gt=0)
    paid_timeout_seconds: float = Field(default=120, gt=0)
    paid_max_budget_usd: float = Field(default=0.10, gt=0)
    verifier: VerifierSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", type=Path,
        default=PROJECT / "config" / "code-fix-shadow-trial-20261018.toml")
    parser.add_argument("--cases", type=Path,
        default=PROJECT / "tests" / "fixtures" / "code-fix-shadow-20261018.json")
    parser.add_argument("--receipt", type=Path,
        default=PROJECT / "var" / "probes" / "code-fix-shadow-20261018.json")
    parser.add_argument("--policy", type=Path,
        default=PROJECT / "config" / "pilot-policy.skynet-agent-proposal.toml")
    parser.add_argument("--capabilities", type=Path,
        default=PROJECT / "config" / "runtime-capabilities.codex-pilot.toml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    for path in (args.settings, args.cases, args.receipt, args.policy, args.capabilities):
        if not path.resolve().is_relative_to(PROJECT):
            raise SystemExit("all shadow files must be under bridge-monitor")
    settings = ShadowSettings.model_validate(tomllib.loads(
        args.settings.read_text(encoding="utf-8")))
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    policy = PolicyInput.model_validate(tomllib.loads(args.policy.read_text(encoding="utf-8")))
    capabilities = tomllib.loads(args.capabilities.read_text(encoding="utf-8"))
    if settings.mode != "shadow" or len(cases) > settings.max_cases:
        raise SystemExit("explicit shadow mode and configured case limit required")
    if settings.cohort == "natural_shadow":
        if (len(cases) != 1 or not isinstance(cases[0], dict) or
                not isinstance(cases[0].get("provenance"), dict)):
            raise SystemExit("natural shadow requires one case with provenance")
        if args.receipt == parser.get_default("receipt"):
            raise SystemExit("natural shadow requires an explicit per-case receipt path")
        receipt_dir = PROJECT / "var" / "probes" / "natural-code-fix"
        if (args.receipt.resolve().parent != receipt_dir.resolve() or
                not args.receipt.name.endswith(".receipt.json")):
            raise SystemExit("natural receipt must be under var/probes/natural-code-fix as *.receipt.json")
        if args.receipt.name != cases[0]["id"] + ".receipt.json":
            raise SystemExit("natural receipt filename must match the case ID")
        try:
            validate_natural_case(cases[0], settings.model_dump())
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if not policy.deployment.qwen_agent_endpoint:
        raise SystemExit("authenticated Skynet agent required")
    inputs = {"settings_sha256": args.settings, "cases_sha256": args.cases,
        "policy_sha256": args.policy, "capabilities_sha256": args.capabilities,
        "script_sha256": Path(__file__),
        "verifier_sha256": PROJECT / "bridge" / "code_fix_verifier.py",
        "runner_sha256": PROJECT / "bridge" / "code_fix_shadow_runner.py",
        "accounting_sha256": PROJECT / "bridge" / "shadow_accounting.py",
        "transport_sha256": PROJECT / "bridge" / "experiment_transports.py"}
    inputs.update({"routing_sha256": PROJECT / "bridge" / "local_first.py",
        "persistence_sha256": PROJECT / "bridge" / "local_first_runner.py",
        "qwen_adapter_sha256": PROJECT / "bridge" / "adapters" / "qwen_agent.py",
        "policy_parser_sha256": PROJECT / "contracts" / "policy.py"})
    metadata = {key: hashlib.sha256(path.read_bytes()).hexdigest()
                for key, path in inputs.items()}
    metadata.update({"qwen_model": policy.deployment.qwen_model_profile,
        "qwen_digest": policy.deployment.qwen_model_digest,
        "paid_model": policy.fallback.paid_model_profile,
        "parent_model": settings.parent_model,
        "verifier_image": settings.verifier.image})
    if settings.cohort == "natural_shadow":
        existing_dir = PROJECT / "var" / "probes" / "natural-code-fix"
        existing = [path for path in sorted(existing_dir.glob("*.receipt.json"))
                    if path.resolve() != args.receipt.resolve()]
        try:
            check_new_case(cases[0], settings.model_dump(), settings.prompt_prefix,
                metadata, existing)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if args.dry_run:
        print(json.dumps({"cases": len(cases), "cohort": settings.cohort,
            "qwen_model": metadata["qwen_model"], "paid_model": metadata["paid_model"],
            "parent_model": metadata["parent_model"],
            "verifier_image": settings.verifier.image, "receipt": str(args.receipt)},
            sort_keys=True))
        return 0
    verifier = DockerVerifier(settings.verifier, PROJECT / "var" / "shadow" / "runs")
    with httpx.Client() as client:
        adapter = make_qwen_adapter(client, policy=policy, capabilities=capabilities,
            output_tokens=settings.local_output_tokens,
            max_seconds=settings.local_timeout_seconds)
        preflight = adapter.preflight()
        if not preflight.ready:
            raise SystemExit("Skynet preflight failed: " + preflight.reason)
        receipt = run_shadow_cases(cases, settings.model_dump(), args.receipt,
            verifier, lambda prompt: qwen_call(prompt, adapter=adapter),
            lambda prompt: sonnet_call(prompt,
                model=policy.fallback.paid_model_profile, settings=settings),
            lambda prompt: codex_parent_call(prompt, model=settings.parent_model,
                timeout_seconds=settings.parent_timeout_seconds),
            run_metadata=metadata, prompt_prefix=settings.prompt_prefix)
    summary = summarize_shadow(receipt["results"], cohort=settings.cohort)
    print(json.dumps({"summary": summary, "receipt": str(args.receipt)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
