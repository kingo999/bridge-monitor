"""Per-call telemetry: rotating local JSONL, redacted by construction.

Records are assembled from an explicit allow-list of fields; prompt text,
source content, answers, credentials, error prose and repository paths never
enter a record.  ``write`` never raises: a telemetry failure must not turn an
already executed model turn into an error (and therefore never into a replay).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Mapping

from . import errors, state
from .ids import is_thread_id

log = logging.getLogger("architect_bridge.telemetry")  # stderr only; stdout is MCP

SCHEMA_VERSION = 1
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)

#: Allowed top-level keys.  Anything else is dropped before writing.
_ALLOWED = (
    "schema_version",
    "call_id",
    "tool",
    "started_at",
    "ended_at",
    "route",
    "session",
    "queue_ms",
    "run_ms",
    "outcome",
    "error_category",
    "error_code",
    "inference_may_have_started",
    "exit_code",
    "usage",
    "events",
    "prompt_chars",
    "answer_chars",
    "cli_version",
    "warnings",
    "capabilities",
    "process",
    "infra",
    "repo",
    "pointer_state",
    "history",
    "session_state",
)


def empty_usage() -> dict[str, int | None]:
    return {name: None for name in USAGE_FIELDS}


def normalise_usage(raw: Any) -> dict[str, int | None]:
    """Map a CLI-reported usage object to the fixed field set; missing -> None."""
    out = empty_usage()
    if isinstance(raw, Mapping):
        for name in USAGE_FIELDS:
            value = raw.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                out[name] = value
    return out


_TOKEN = re.compile(r"^[A-Za-z0-9_.:,|+@\-]{0,160}$")   # no whitespace, slashes, quotes or backslashes
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_INVALID = "<invalid>"


def _clean(value: Any, depth: int = 0) -> Any:
    """Recursively admit only numbers/bools/None and short opaque tokens (N14).

    Anything else - in particular a string that looks like a path, URL, sentence or source
    fragment - is replaced, never copied.  Dict keys must be tokens as well."""
    if depth > 4:
        return _INVALID
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if _TOKEN.fullmatch(value) else _INVALID
    if isinstance(value, Mapping):
        return {k: _clean(v, depth + 1) for k, v in list(value.items())[:64]
                if isinstance(k, str) and _TOKEN.fullmatch(k)}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in list(value)[:64]]
    return _INVALID


#: Typed top-level fields (V15): a value outside its own enum / format is dropped to None rather
#: than merely being "a short token".  Nested free-form structures still go through ``_clean``.
_ENUMS: dict[str, frozenset[str]] = {
    "tool": frozenset({"consult_architect", "review_implementation"}),
    "outcome": frozenset({"success", "success_partial_persistence", "success_session_in_doubt",
                          "error", "timeout", "cancelled"}),
    "error_category": frozenset(errors.ALL_CATEGORIES),
    "pointer_state": frozenset({"not_reached", "unchanged", "bound", "bind_failed", "pending_reconcile"}),
    "history": frozenset({"no_turn_started", "turn_may_exist"}),
    "session_state": frozenset({"ready", "in_doubt", "pending_reconcile"}),
}
_FORMATS: dict[str, "re.Pattern[str]"] = {
    "call_id": re.compile(r"[0-9a-f]{32}"),
    "error_code": re.compile(r"[A-Za-z0-9_.:-]{1,120}"),
    "cli_version": re.compile(r"[0-9A-Za-z._+\-]{1,40}"),
}


def sanitise(record: Mapping[str, Any]) -> dict[str, Any]:
    clean = {k: _clean(record[k]) for k in _ALLOWED if k in record}
    clean["schema_version"] = SCHEMA_VERSION
    for key, allowed in _ENUMS.items():
        value = clean.get(key)
        # R11: only a string can be a member (a list/dict is unhashable and would raise, discarding
        # the whole record); anything else is dropped to None like any other invalid value
        if key in clean and value is not None and not (isinstance(value, str) and value in allowed):
            clean[key] = None
    for key, pattern in _FORMATS.items():
        value = clean.get(key)
        if key in clean and value is not None and not (isinstance(value, str) and pattern.fullmatch(value)):
            clean[key] = None
    for key in ("started_at", "ended_at"):
        if key in clean and not (isinstance(clean[key], str) and _TS.fullmatch(clean[key])):
            clean[key] = None
    session = clean.get("session")
    if isinstance(session, dict):
        tid = record.get("session", {}).get("thread_id") if isinstance(record.get("session"), Mapping) else None
        session["thread_id"] = tid if is_thread_id(tid) else None   # a malformed id is dropped, not echoed
    return clean


class Telemetry:
    def __init__(self, state_dir: Path, max_bytes: int = 2_000_000, backups: int = 5) -> None:
        self.dir = state_dir / "telemetry"
        self.path = self.dir / "calls.jsonl"
        self._lock_path = self.dir / "telemetry.lock"
        self.max_bytes = max_bytes
        self.backups = backups

    def write(self, record: Mapping[str, Any]) -> bool:
        try:
            line = json.dumps(sanitise(record), separators=(",", ":"), sort_keys=True) + "\n"
            self.dir.mkdir(parents=True, exist_ok=True)
            lock = state.FileLock(self._lock_path)
            lock.acquire(5.0)
            try:
                self._rotate_if_needed(len(line.encode("utf-8")))
                with open(self.path, "ab") as handle:
                    handle.write(line.encode("utf-8"))
                    handle.flush()
            finally:
                lock.release()
            return True
        except Exception as exc:  # noqa: BLE001 - telemetry must never raise
            log.warning("telemetry write failed: %s", exc.__class__.__name__)
            return False

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if size + incoming <= self.max_bytes:
            return
        for index in range(self.backups, 0, -1):
            src = self.path if index == 1 else self.path.with_name(f"calls.{index - 1}.jsonl")
            dst = self.path.with_name(f"calls.{index}.jsonl")
            if src.exists():
                if index == self.backups and dst.exists():
                    dst.unlink()
                os.replace(src, dst)
