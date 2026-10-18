"""Offline matched holdout runner with pre-registered cases and exact receipts."""

import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from bridge.local_first import LocalFirstSettings, ModelReply, route, parse_answer


PROMPT_PREFIX = ("Answer the question accurately. Return only a JSON object with exactly "
                 "one key, answer, whose value is a string. Do not use tools.\n\n")


def _save(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    for attempt in range(4):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))


def run_cases(cases: list[dict], settings: LocalFirstSettings, receipt_path: str | Path,
              local_call: Callable[[str], ModelReply],
              paid_call: Callable[[str], ModelReply], *,
              run_metadata: dict[str, str] | None = None) -> dict:
    """Run each direct/local-first pair once; fail if a resumed fixture changes."""
    if len({case.get("id") for case in cases}) != len(cases):
        raise ValueError("holdout IDs must be unique")
    for case in cases:
        if (set(case) != {"id", "category", "question", "answer"} or
                not all(isinstance(value, str) and value.strip() for value in case.values())):
            raise ValueError("holdout cases need nonempty ID, category, question and answer")
    metadata = run_metadata or {}
    if not isinstance(metadata, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in metadata.items()):
        raise ValueError("run metadata must contain string keys and values")
    path = Path(receipt_path)
    expected = {"cases": cases, "settings": settings.model_dump(),
                "run_metadata": metadata}
    contract = {**expected, "prompt_prefix": PROMPT_PREFIX}
    expected["contract_sha256"] = hashlib.sha256(json.dumps(contract,
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    if path.exists():
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError("holdout changed after registration")
        if receipt.get("in_progress") is not None:
            raise ValueError("interrupted case may have dispatched; manual recovery required")
    else:
        receipt = {**expected, "created_at": datetime.now(timezone.utc).isoformat(),
                   "results": {}, "in_progress": None,
                   "method": "same prompt, exact pre-registered answer key; "
                   "local proof required, otherwise paid fallback"}
        _save(path, receipt)
    for index, case in enumerate(cases):
        if case["id"] in receipt["results"]:
            continue
        order = "direct_first" if index % 2 == 0 else "local_first"
        receipt["in_progress"] = {"case_id": case["id"], "arm_order": order,
                                  "direct": None, "local_first": None}
        _save(path, receipt)
        prompt = PROMPT_PREFIX + case["question"]
        def candidate():
            return route(case["id"], case["category"], prompt, settings,
                         lambda answer, expected=case["answer"]: answer.strip() == expected,
                         local_call, paid_call)
        if index % 2 == 0:
            direct = paid_call(prompt)
            receipt["in_progress"]["direct"] = asdict(direct)
            _save(path, receipt)
            outcome = candidate()
            receipt["in_progress"]["local_first"] = asdict(outcome)
            _save(path, receipt)
        else:
            outcome = candidate()
            receipt["in_progress"]["local_first"] = asdict(outcome)
            _save(path, receipt)
            direct = paid_call(prompt)
            receipt["in_progress"]["direct"] = asdict(direct)
            _save(path, receipt)
        direct_answer = parse_answer(direct.raw) if direct.state == "completed" else None
        receipt["results"][case["id"]] = {
            "prompt": prompt, "arm_order": order, "direct": asdict(direct),
            "direct_exact": direct_answer == case["answer"],
            "local_first": asdict(outcome),
            "local_first_exact": outcome.answer == case["answer"],
        }
        receipt["in_progress"] = None
        _save(path, receipt)
    return receipt
