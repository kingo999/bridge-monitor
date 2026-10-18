"""Disabled-by-default, verifier-gated local-first evaluation core.

No live bridge entry point imports this module. A caller must supply an independent
verifier; model formatting or self-reported confidence cannot authorize delivery.
"""

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field


class LocalFirstSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["disabled", "evaluation"] = "disabled"
    canary_percent: int = Field(default=0, ge=0, le=100)
    categories: list[str] = Field(default_factory=list)
    max_local_seconds: float = Field(default=30, gt=0)
    local_output_tokens: int = Field(default=256, gt=0)
    paid_timeout_seconds: int = Field(default=90, gt=0)
    paid_max_budget_usd: float = Field(default=0.10, gt=0)


@dataclass(frozen=True)
class ModelReply:
    state: str
    raw: str | None = None
    requested_model: str | None = None
    reported_model: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    uncertainty: str | None = None
    list_cost_usd: float | None = None
    cost_basis: str | None = None


@dataclass(frozen=True)
class RouteOutcome:
    route: Literal["local", "paid"]
    reason: str
    answer: str | None
    local: ModelReply | None
    paid: ModelReply | None
    local_answer: str | None = None
    paid_format_valid: bool = False


def load_settings(path: str | Path) -> LocalFirstSettings:
    with Path(path).open("rb") as source:
        return LocalFirstSettings.model_validate(tomllib.load(source))


def sampled(work_item_id: str, percent: int) -> bool:
    if not 0 <= percent <= 100:
        raise ValueError("sampling percentage outside 0..100")
    score = int.from_bytes(hashlib.sha256(work_item_id.encode("utf-8")).digest()[:8], "big")
    return score % 10000 < percent * 100


_FENCE = re.compile(r"\A```(?:json)?\s*\n([\s\S]*?)\n```\s*\Z", re.IGNORECASE)


def parse_answer(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    body = raw.strip()
    fenced = _FENCE.fullmatch(body)
    if fenced:
        body = fenced.group(1).strip()

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        parsed = json.loads(body, object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        return None
    if (not isinstance(parsed, dict) or set(parsed) != {"answer"} or
            not isinstance(parsed["answer"], str) or not parsed["answer"].strip()):
        return None
    return parsed["answer"].strip()


def route(work_item_id: str, category: str, prompt: str, settings: LocalFirstSettings,
          verifier: Callable[[str], bool] | None,
          local_call: Callable[[str], ModelReply],
          paid_call: Callable[[str], ModelReply]) -> RouteOutcome:
    """Call local at most once; any missing or failed proof goes to paid fallback."""
    reason = None
    if settings.mode != "evaluation":
        reason = "disabled"
    elif category not in settings.categories:
        reason = "category_ineligible"
    elif verifier is None:
        reason = "verifier_unavailable"
    elif not sampled(work_item_id, settings.canary_percent):
        reason = "not_sampled"

    local = None
    parsed_local = None
    if reason is None:
        try:
            local = local_call(prompt)
        except Exception as exc:
            local = ModelReply(state="uncertain", uncertainty=type(exc).__name__)
        if local.state != "completed":
            reason = "local_" + local.state
        else:
            parsed_local = parse_answer(local.raw)
            if parsed_local is None:
                reason = "invalid_local_format"
            else:
                try:
                    verified = verifier(parsed_local)
                except Exception:
                    reason = "verification_unavailable"
                else:
                    if verified is True:
                        return RouteOutcome("local", "verified", parsed_local,
                                            local, None, parsed_local)
                    reason = "verification_failed"

    try:
        paid = paid_call(prompt)
    except Exception as exc:
        paid = ModelReply(state="uncertain", uncertainty=type(exc).__name__)
    parsed_paid = parse_answer(paid.raw) if paid.state == "completed" else None
    return RouteOutcome("paid", reason or "local_unverified",
                        parsed_paid,
                        local, paid, parsed_local, parsed_paid is not None)
