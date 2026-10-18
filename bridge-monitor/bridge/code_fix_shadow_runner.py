"""Matched offline code-fix shadow trial; no live result delivery or database writes."""

import hashlib
import json
import os
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from bridge.local_first import LocalFirstSettings, ModelReply, parse_answer, route
from bridge.local_first_runner import _save


PROMPT_PREFIX = ("Fix the following single Python file for the stated task. "
                 "Return only one JSON object with exactly one key, answer, "
                 "whose string value is the complete replacement Python source. "
                 "Do not use tools.\n\n")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()


def _parent_prompt(case: dict, source: str | None, state: str) -> str:
    encoded = (source or "").encode("utf-8", errors="replace")
    task_label = ("Task SHA-256: " + hashlib.sha256(case["task"].encode("utf-8")).hexdigest()
        if "provenance" in case else "Task: " + case["task"])
    return ("You are a read-only GPT usage proxy. Do not call tools. In one "
            "sentence, acknowledge the trusted test result. The worker's raw "
            "source is deliberately omitted from this proxy.\n\n" +
            task_label + "\nTest state: " + state +
            "\nSource bytes: " + str(len(encoded)) +
            "\nSource SHA-256: " + hashlib.sha256(encoded).hexdigest())


def validate_natural_case(case: dict, settings: dict) -> None:
    """Reject pre-cohort or known retrospective inputs before any model dispatch."""
    if not isinstance(case, dict) or "provenance" not in case:
        raise ValueError("natural case provenance required")
    try:
        cutoff = datetime.fromisoformat(settings["cohort_start_at"])
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cohort start needs timezone")
        exclusions = {key: set(settings[key]) for key in
            ("excluded_case_ids", "excluded_baseline_sha256", "excluded_task_sha256")}
        if not all(exclusions.values()):
            raise ValueError("retrospective exclusions required")
    except (KeyError, TypeError) as exc:
        raise ValueError("natural cohort cutoff and retrospective exclusions required") from exc
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,79}", case["id"]):
        raise ValueError("natural case ID must be a safe slug")
    if (case["id"] in exclusions["excluded_case_ids"] or
            hashlib.sha256(case["baseline_source"].encode("utf-8")).hexdigest()
            in exclusions["excluded_baseline_sha256"] or
            hashlib.sha256(case["task"].encode("utf-8")).hexdigest()
            in exclusions["excluded_task_sha256"]):
        raise ValueError("excluded retrospective case cannot enter natural cohort")
    provenance = case["provenance"]
    keys = {"origin_ref", "captured_at", "baseline_ref", "test_ref"}
    if (not isinstance(provenance, dict) or set(provenance) != keys or
            not all(isinstance(provenance[key], str) and provenance[key].strip()
                    for key in keys)):
        raise ValueError("natural case provenance fields invalid")
    try:
        captured = datetime.fromisoformat(provenance["captured_at"])
    except ValueError as exc:
        raise ValueError("natural case provenance time invalid") from exc
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError("natural case provenance time needs timezone")
    if not cutoff < captured <= datetime.now(timezone.utc):
        raise ValueError("natural case capture time outside cohort window")


def run_shadow_cases(cases: list[dict], settings: dict, receipt_path: str | Path,
                     verifier, local_call: Callable[[str], ModelReply],
                     paid_call: Callable[[str], ModelReply],
                     parent_call: Callable[[str], dict], *,
                     run_metadata: dict[str, str] | None = None,
                     prompt_prefix: str = PROMPT_PREFIX) -> dict:
    if settings.get("mode") != "shadow":
        raise ValueError("explicit shadow mode required")
    if not isinstance(prompt_prefix, str) or not prompt_prefix.strip():
        raise ValueError("prompt prefix must be nonempty text")
    natural = settings.get("cohort") == "natural_shadow"
    if natural and len(cases) != 1:
        raise ValueError("natural shadow receipts require exactly one case")
    if len({case.get("id") for case in cases}) != len(cases):
        raise ValueError("shadow case IDs must be unique")
    fields = {"id", "task", "baseline_source", "reference_source", "tests"}
    for case in cases:
        if natural and "provenance" not in case:
            raise ValueError("natural case provenance required")
        if (set(case) != fields | ({"provenance"} if natural else set()) or
                not all(isinstance(case[key], str) and
                case[key].strip() for key in fields - {"tests"}) or
                not isinstance(case["tests"], list) or not case["tests"]):
            raise ValueError("shadow case fields must include nonempty text and tests")
        if natural:
            validate_natural_case(case, settings)
    metadata = run_metadata or {}
    if not isinstance(metadata, dict) or not all(isinstance(key, str) and
            isinstance(value, str) for key, value in metadata.items()):
        raise ValueError("run metadata must be string values")
    expected = {"cases": cases, "settings": settings, "run_metadata": metadata,
                "prompt_prefix": prompt_prefix}
    contract = {**expected, "parent_prompt_version": 3}
    expected["contract_sha256"] = hashlib.sha256(json.dumps(contract,
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    path = Path(receipt_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("shadow receipt is locked by another run") from exc
    try:
        return _run_locked(cases, settings, path, verifier, local_call,
            paid_call, parent_call, expected, prompt_prefix)
    finally:
        os.close(lock_fd)
        lock.unlink()


def _run_locked(cases, settings, path, verifier, local_call, paid_call,
                parent_call, expected, prompt_prefix):
    if path.exists():
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError("shadow contract changed after registration")
        if receipt.get("in_progress") is not None:
            raise ValueError("interrupted shadow case may have dispatched; manual recovery required")
        if set(receipt.get("results", {})) != set(receipt.get("result_sha256", {})) or any(
                receipt["result_sha256"][key] != _digest(value)
                for key, value in receipt["results"].items()):
            raise ValueError("completed shadow result digest mismatch")
    else:
        validations = {}
        for case in cases:
            checked = verifier.validate_case(case["baseline_source"],
                case["reference_source"], case["tests"])
            if checked.state != "ready":
                raise ValueError("case " + case["id"] + ": " + checked.reason)
            validations[case["id"]] = asdict(checked)
        receipt = {**expected, "created_at": datetime.now(timezone.utc).isoformat(),
                   "validations": validations, "results": {}, "result_sha256": {},
                   "in_progress": None,
                   "cohort": settings.get("cohort", "retrospective_shadow")}
        _save(path, receipt)

    route_settings = LocalFirstSettings(mode="evaluation", canary_percent=100,
        categories=["code_fix"])
    for index, case in enumerate(cases):
        if case["id"] in receipt["results"]:
            continue
        prompt = prompt_prefix + "Task: " + case["task"] + "\nCurrent file:\n" + case["baseline_source"]
        order_index = (hashlib.sha256(case["provenance"]["origin_ref"].encode()).digest()[0] % 2
            if settings.get("cohort") == "natural_shadow" else index % 2)
        order = "direct_first" if order_index == 0 else "local_first"
        receipt["in_progress"] = {"case_id": case["id"], "arm_order": order,
            "prompt": prompt, "direct": None, "local_first": None,
            "parent": {}, "parent_prompts": {}}
        _save(path, receipt)
        local_verification = None

        def check_local(source: str) -> bool:
            nonlocal local_verification
            local_verification = verifier.verify(source, case["tests"])
            return local_verification.state == "pass"

        def candidate():
            return route(case["id"], "code_fix", prompt, route_settings,
                check_local, local_call, paid_call)

        def direct_arm():
            started = time.monotonic()
            direct_reply = paid_call(prompt)
            receipt["in_progress"]["direct"] = asdict(direct_reply)
            _save(path, receipt)
            direct_source = (parse_answer(direct_reply.raw)
                if direct_reply.state == "completed" else None)
            direct_check = (verifier.verify(direct_source, case["tests"])
                if direct_source else None)
            return direct_reply, direct_source, direct_check, round(time.monotonic() - started, 3)

        def local_arm():
            started = time.monotonic()
            outcome = candidate()
            receipt["in_progress"]["local_first"] = asdict(outcome)
            _save(path, receipt)
            final_check = (local_verification if outcome.route == "local" else
                verifier.verify(outcome.answer, case["tests"])
                if outcome.answer else None)
            return outcome, final_check, round(time.monotonic() - started, 3)

        if order == "direct_first":
            direct_reply, direct_source, direct_check, direct_seconds = direct_arm()
            outcome, final_check, local_seconds = local_arm()
        else:
            outcome, final_check, local_seconds = local_arm()
            direct_reply, direct_source, direct_check, direct_seconds = direct_arm()
        delivered_source = outcome.answer
        parent = {}
        parent_prompts = {}
        for arm, source, checked in (("direct", direct_source, direct_check),
                                     ("local_first", delivered_source, final_check)):
            parent_prompts[arm] = _parent_prompt(case, source,
                checked.state if checked else "unavailable")
            receipt["in_progress"]["parent_prompts"][arm] = parent_prompts[arm]
            _save(path, receipt)
            try:
                parent[arm] = parent_call(parent_prompts[arm])
            except Exception as exc:
                parent[arm] = {"state": "uncertain", "reason": type(exc).__name__,
                               "usage": None}
            receipt["in_progress"]["parent"][arm] = parent[arm]
            _save(path, receipt)
        receipt["results"][case["id"]] = {"prompt": prompt, "arm_order": order,
            "arm_elapsed_seconds": {"direct": direct_seconds,
                                    "local_first": local_seconds},
            "direct": {"route": "paid", "paid": asdict(direct_reply)},
            "direct_verification": asdict(direct_check) if direct_check else None,
            "local_first": asdict(outcome),
            "local_verification": asdict(local_verification) if local_verification else None,
            "local_final_verification": asdict(final_check) if final_check else None,
            "parent": parent, "parent_prompts": parent_prompts}
        receipt["result_sha256"][case["id"]] = _digest(receipt["results"][case["id"]])
        receipt["in_progress"] = None
        _save(path, receipt)
    return receipt
