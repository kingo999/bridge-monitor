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
from typing import Any, Mapping

from . import state

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


def sanitise(record: Mapping[str, Any]) -> dict[str, Any]:
    clean = {k: record[k] for k in _ALLOWED if k in record}
    clean["schema_version"] = SCHEMA_VERSION
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
