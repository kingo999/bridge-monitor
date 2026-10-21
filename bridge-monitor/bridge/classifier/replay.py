"""File-based replay of labelled cases through the real client, contract and policy.

No database, no dispatch. Raw recommendations and policy verdicts are scored separately;
a blocked recommendation is never relabelled and counted as a model success.
"""

import hashlib
import json
import math
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path

from bridge.classifier.client import OllamaClassifier
from bridge.classifier.interpret import interpret
from bridge.classifier.scenarios import (CATALOGUE_VERSION, catalogue_sha256, compare_to_scenarios,
                                         prepare_scenario_input, rubric_for_identity,
                                         scenario_provenance, GUIDED_PROFILE_IDS, metadata_for_identity)
from bridge.classifier.rubric import RUBRIC_SHA256
from contracts.classifier import ClassifierInput, ReferenceLabel, POLICY_VERSION


def _reference(case: dict) -> ReferenceLabel:
    return ReferenceLabel(category=case.get("category"), preferred_route=case["preferred_route"],
                          acceptable_routes=case["acceptable_routes"],
                          critical=bool(case.get("critical")))


def schedule(case_ids: list[str], seeds: list[int], base: int = 20261018) -> list[dict]:
    """Per-pass shuffles match the original benchmark (seed base + pass index)."""
    blocks = []
    for index, seed in enumerate(seeds):
        ids = list(case_ids)
        random.Random(base + index).shuffle(ids)
        blocks.append({"pass": index, "seed": seed, "case_ids": ids})
    return blocks


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def score(record: dict, case: dict) -> dict:
    ref = _reference(case)
    rec = record.get("recommendation")
    valid = record["state"] == "completed" and rec is not None
    return {"valid": valid,
            "route_acceptable": bool(valid and rec["route"] in ref.acceptable_routes),
            "route_preferred": bool(valid and rec["route"] == ref.preferred_route),
            "category_acceptable": bool(valid and ref.category and rec["category"] in
                                        case.get("acceptable_categories", [ref.category])),
            "policy_allowed": record["assessment"]["allowed"] is True,
            "allowed_and_acceptable": bool(valid and rec["route"] in ref.acceptable_routes
                                           and record["assessment"]["allowed"]),
            "unsafe_recommendation": bool(valid and record["assessment"]["allowed"] is False),
            "critical_under_escalation": bool(ref.critical and (
                not valid or rec["route"] in ("tools", "general_local", "code_local")))}


def run_replay(classifier: OllamaClassifier, cases: list[dict], seeds: list[int], out_dir: Path,
               *, label: str, cases_path: Path, runtime_note: dict | None = None) -> dict:
    ident = rubric_for_identity(classifier.profile.profile_id,
        None if classifier.profile.profile_id in GUIDED_PROFILE_IDS else RUBRIC_SHA256)
    kw = {"rubric": ident.text} if ident.guided else {}
    out_dir.mkdir(parents=True, exist_ok=False)  # fresh evidence directory only
    by_id = {c["id"]: c for c in cases}
    identity = classifier.check_identity()
    if not identity.ok:
        raise RuntimeError(f"classifier identity check failed: {identity.reason}")
    manifest = {"label": label, "started_at": datetime.now(timezone.utc).isoformat(),
                "cases_file": str(cases_path),
                "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
                "rubric_version": ident.version, "rubric_sha256": ident.sha256,
                "policy_version": metadata_for_identity(ident)['policy_version'] if ident.guided else POLICY_VERSION,
                **({"scenario_guided": True, "profile_id": ident.profile_id,
                    **metadata_for_identity(ident)}
                   if ident.guided else {}),
                "profile": classifier.profile.model_dump(), "model": classifier.model,
                "expected_digest": classifier.expected_digest,
                "observed_digest": identity.observed_digest,
                "runtime_version": identity.runtime_version, "seeds": seeds,
                "retry_policy": "none", "runtime": runtime_note or {}}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    records = []
    # One excluded warmup records model load separately from warm classification.
    warm_item = ClassifierInput(
        request="Rewrite this note clearly for review.", source="The observation is incomplete.",
        facts={"general_available": True, "code_available": True, "paid_allowed": True})
    warm = classifier.classify(prepare_scenario_input(warm_item, ident,
        context_tokens=classifier.profile.num_ctx, output_tokens=classifier.profile.num_predict)[0], seed=seeds[0], **kw)
    (out_dir / "warmup.json").write_text(json.dumps({
        "status": warm.status, "request_seconds": warm.request_seconds,
        "durations_ms": warm.durations_ms, "usage": warm.usage,
        "residency": classifier.residency().evidence()}, indent=2))
    with open(out_dir / "responses.jsonl", "x", encoding="utf-8") as sink:
        for block in schedule(list(by_id), seeds):
            for case_id in block["case_ids"]:
                case = by_id[case_id]
                item = ClassifierInput(request=case["request"], source=case.get("source", ""),
                                       facts=case["facts"])
                message, decision = prepare_scenario_input(item, ident,
                    context_tokens=classifier.profile.num_ctx, output_tokens=classifier.profile.num_predict)
                outcome = classifier.classify(message, seed=block["seed"], **kw)
                interp = interpret(outcome, expected_model=classifier.model, facts=item.trusted(),
                                   scenario_decision=decision)
                record = {"case_id": case_id, "pass": block["pass"], "seed": block["seed"],
                          "state": interp.state, "error_type": interp.error_type,
                          "validation_error": interp.validation_error,
                          "raw_response": outcome.raw_text,
                          "recommendation": interp.recommendation.model_dump()
                                            if interp.recommendation else None,
                          "assessment": interp.assessment.model_dump(),
                          "done_reason": outcome.done_reason,
                          "reported_model": outcome.reported_model,
                          "request_seconds": outcome.request_seconds,
                          "first_content_seconds": outcome.first_content_seconds,
                          "durations_ms": outcome.durations_ms, "usage": outcome.usage}
                if ident.guided:
                    record["scenario"] = {**scenario_provenance(ident, decision),
                                          "comparison": compare_to_scenarios(interp.recommendation, decision)}
                record["score"] = score(record, case)
                sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                sink.flush()
                records.append(record)
    manifest.update(finished_at=datetime.now(timezone.utc).isoformat(),
                    final_residency=classifier.residency().evidence())
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    summary = summarize(records, by_id)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def summarize(records: list[dict], cases: dict[str, dict]) -> dict:
    result = {"decisions": len(records), "by_pass": {}, "failures": [], "unsafe": []}
    for index in sorted({r["pass"] for r in records}):
        rows = [r for r in records if r["pass"] == index]
        result["by_pass"][index] = {
            "decisions": len(rows),
            "valid_contract": sum(r["score"]["valid"] for r in rows),
            "raw_route_acceptable": sum(r["score"]["route_acceptable"] for r in rows),
            "raw_route_preferred": sum(r["score"]["route_preferred"] for r in rows),
            "raw_category_acceptable": sum(r["score"]["category_acceptable"] for r in rows),
            "policy_allowed": sum(r["score"]["policy_allowed"] for r in rows),
            "allowed_and_acceptable": sum(r["score"]["allowed_and_acceptable"] for r in rows),
            "unsafe_recommendations": sum(r["score"]["unsafe_recommendation"] for r in rows),
            "critical_under_escalations": sum(r["score"]["critical_under_escalation"] for r in rows)}
    for r in records:
        case = cases[r["case_id"]]
        if not r["score"]["route_acceptable"]:
            result["failures"].append({"case_id": r["case_id"], "pass": r["pass"],
                "expected": case["acceptable_routes"], "recommended": (r["recommendation"] or {}).get("route"),
                "state": r["state"], "error_type": r["error_type"] or r["validation_error"],
                "policy_blocks": r["assessment"]["blocks"]})
        if r["score"]["unsafe_recommendation"]:
            result["unsafe"].append({"case_id": r["case_id"], "pass": r["pass"],
                "recommended": r["recommendation"]["route"], "blocks": r["assessment"]["blocks"]})
    seconds = [r["request_seconds"] for r in records if r["state"] == "completed" and r["request_seconds"]]
    first = [r["first_content_seconds"] for r in records if r["first_content_seconds"] is not None]
    result["latency_seconds"] = {"samples": len(seconds),
        "median": statistics.median(seconds) if seconds else None,
        "p95": percentile(seconds, 0.95), "min": min(seconds, default=None),
        "max": max(seconds, default=None),
        "first_content_median": statistics.median(first) if first else None}
    result["tokens"] = {"input": sum(r["usage"].get("input_tokens", 0) for r in records),
                        "output": sum(r["usage"].get("output_tokens", 0) for r in records),
                        "records_with_usage": sum(bool(r["usage"]) for r in records)}
    changes = []
    for case_id in cases:
        routes = {r["pass"]: (r["recommendation"] or {}).get("route") for r in records
                  if r["case_id"] == case_id}
        if len(set(routes.values())) > 1:
            changes.append({"case_id": case_id, "routes": routes})
    result["route_changes_between_passes"] = changes
    return result
