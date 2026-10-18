"""Matched offline code-fix shadow trial; no live result delivery or database writes."""

import hashlib
import json
import os
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
    return ("You are a read-only GPT usage proxy. Do not call tools. In one "
            "sentence, acknowledge the trusted test result. The worker's raw "
            "source is deliberately omitted from this proxy.\n\nTask: " +
            case["task"] + "\nTest state: " + state +
            "\nSource bytes: " + str(len(encoded)) +
            "\nSource SHA-256: " + hashlib.sha256(encoded).hexdigest())


def run_shadow_cases(cases: list[dict], settings: dict, receipt_path: str | Path,
                     verifier, local_call: Callable[[str], ModelReply],
                     paid_call: Callable[[str], ModelReply],
                     parent_call: Callable[[str], dict], *,
                     run_metadata: dict[str, str] | None = None) -> dict:
    if settings.get("mode") != "shadow":
        raise ValueError("explicit shadow mode required")
    if len({case.get("id") for case in cases}) != len(cases):
        raise ValueError("shadow case IDs must be unique")
    fields = {"id", "task", "baseline_source", "reference_source", "tests"}
    for case in cases:
        if (set(case) != fields or not all(isinstance(case[key], str) and
                case[key].strip() for key in fields - {"tests"}) or
                not isinstance(case["tests"], list) or not case["tests"]):
            raise ValueError("shadow case fields must include nonempty text and tests")
    metadata = run_metadata or {}
    if not isinstance(metadata, dict) or not all(isinstance(key, str) and
            isinstance(value, str) for key, value in metadata.items()):
        raise ValueError("run metadata must be string values")
    expected = {"cases": cases, "settings": settings, "run_metadata": metadata}
    contract = {**expected, "prompt_prefix": PROMPT_PREFIX,
                "parent_prompt_version": 2}
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
            paid_call, parent_call, expected)
    finally:
        os.close(lock_fd)
        lock.unlink()


def _run_locked(cases, settings, path, verifier, local_call, paid_call,
                parent_call, expected):
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
        prompt = PROMPT_PREFIX + "Task: " + case["task"] + "\nCurrent file:\n" + case["baseline_source"]
        order = "direct_first" if index % 2 == 0 else "local_first"
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

        if index % 2 == 0:
            direct_reply = paid_call(prompt)
            receipt["in_progress"]["direct"] = asdict(direct_reply)
            _save(path, receipt)
            outcome = candidate()
            receipt["in_progress"]["local_first"] = asdict(outcome)
            _save(path, receipt)
        else:
            outcome = candidate()
            receipt["in_progress"]["local_first"] = asdict(outcome)
            _save(path, receipt)
            direct_reply = paid_call(prompt)
            receipt["in_progress"]["direct"] = asdict(direct_reply)
            _save(path, receipt)

        direct_source = parse_answer(direct_reply.raw) if direct_reply.state == "completed" else None
        direct_check = verifier.verify(direct_source, case["tests"]) if direct_source else None
        final_check = (local_verification if outcome.route == "local" else
            verifier.verify(outcome.answer, case["tests"]) if outcome.answer else None)
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
