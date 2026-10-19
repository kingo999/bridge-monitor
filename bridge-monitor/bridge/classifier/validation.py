"""Strict, non-repairing validation of the classifier's complete response."""

import json

from contracts.classifier import Recommendation
from bridge.classifier.rubric import CATEGORIES, MAX_REASON_CHARS, ROUTES

REQUIRED_FIELDS = frozenset({"category", "route", "reason"})


class ClassifierContractError(ValueError):
    """The response is not an acceptable recommendation. ``code`` is stable."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _no_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ClassifierContractError("duplicate_key", key)
        obj[key] = value
    return obj


def _reject_constant(name):
    raise ClassifierContractError("not_json", f"non-standard constant {name}")


def parse_recommendation(text: str) -> Recommendation:
    """Accept exactly one JSON object with exactly the three required fields.

    No prose, Markdown fence, extra field, duplicate key, coercion, truncation or repair.
    """
    if not isinstance(text, str) or not text.strip():
        raise ClassifierContractError("empty_response")
    if text.lstrip().startswith(("```", "~~~")):
        raise ClassifierContractError("markdown_wrapper")
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicate_keys,
                           parse_constant=_reject_constant)
    except ClassifierContractError:
        raise
    except json.JSONDecodeError as exc:
        code = "trailing_content" if exc.msg.startswith("Extra data") else "not_json"
        raise ClassifierContractError(code, exc.msg) from exc
    if not isinstance(value, dict):
        raise ClassifierContractError("not_object")
    missing = REQUIRED_FIELDS - set(value)
    if missing:
        raise ClassifierContractError("missing_field", ",".join(sorted(missing)))
    extra = set(value) - REQUIRED_FIELDS
    if extra:
        raise ClassifierContractError("extra_field", ",".join(sorted(extra)))
    for name in REQUIRED_FIELDS:
        if not isinstance(value[name], str):
            raise ClassifierContractError("wrong_type", name)
    if value["category"] not in CATEGORIES:
        raise ClassifierContractError("unknown_category", value["category"][:40])
    if value["route"] not in ROUTES:
        raise ClassifierContractError("unknown_route", value["route"][:40])
    if not value["reason"].strip():
        raise ClassifierContractError("empty_reason")
    if len(value["reason"]) > MAX_REASON_CHARS:
        raise ClassifierContractError("reason_too_long", str(len(value["reason"])))
    return Recommendation(category=value["category"], route=value["route"],
                          reason=value["reason"])
