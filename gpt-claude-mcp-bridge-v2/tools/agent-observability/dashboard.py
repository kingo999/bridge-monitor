#!/usr/bin/env python3
"""Read-only local dashboard for v2 bridge telemetry (OBS-01).

Serves fixed GET endpoints on 127.0.0.1 only. It reads the allow-listed telemetry
and in-flight files, never writes bridge state, never runs a CLI or model.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import obs_accounting  # noqa: E402
import obs_reader  # noqa: E402

BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8089
SNAPSHOT_SCHEMA = "obs-01/1"
MAX_CALLS_SERVED = 500
SMALL_SAMPLE = 5
CACHE_SECONDS = 1.0
DEFAULT_STATE_DIR = ".gpt-architect-bridge-v2"

SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; "
                                "connect-src 'self'; base-uri 'none'; form-action 'none'; "
                                "frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
}

GAP_TEXT = {
    "file_head_truncated": "a telemetry file exceeded the read bound and its oldest part was not read",
    "oversized_line": "an oversized line was skipped",
    "malformed_line": "a malformed line was skipped",
    "partial_trailing_line": "a partial trailing line (possibly mid-write) was skipped",
    "non_object_line": "a non-object line was skipped",
    "record_missing_call_id": "a record without a usable call ID was skipped",
    "rotation_gap": "a rotated segment is missing",
    "scan_budget_exceeded": "the scan budget was exhausted, so older telemetry was not read",
}
READ_PROBLEM_STATES = ("refused_link", "refused_not_regular", "unreadable", "changed")


def default_state_dir():
    return str(Path.home() / DEFAULT_STATE_DIR)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _notice(level, code, message):
    return {"level": level, "code": code, "message": message}


def _notices(state, calls, inflight_state):
    tel = state["telemetry"]
    out = []
    if state["state_dir_state"] != "ok":
        out.append(_notice("error", "state_dir_" + state["state_dir_state"],
                           "Bridge state directory is unavailable (%s). Nothing was read."
                           % state["state_dir_state"]))
        return out
    states = {f["name"]: f["state"] for f in tel["files"]}
    distinct = set(states.values())
    if distinct == {"missing"}:
        out.append(_notice("warning", "no_telemetry", "No telemetry files found yet."))
    elif len(distinct) == 1 and distinct <= set(READ_PROBLEM_STATES):
        out.append(_notice("error", "telemetry_dir_" + distinct.pop(),
                           "The telemetry directory was refused or unreadable; no telemetry was read."))
    else:
        for f in tel["files"]:
            if f["state"] in READ_PROBLEM_STATES:
                out.append(_notice("error", "file_" + f["state"],
                                   "%s was refused or unreadable (%s); records may be missing."
                                   % (f["name"], f["state"])))
            elif f["state"] == "truncated":
                out.append(_notice("warning", "file_truncated",
                                   "%s exceeded a read bound; only part was read." % f["name"]))
            elif f["state"] == "not_read":
                out.append(_notice("warning", "file_not_read",
                                   "%s was not read because the scan budget was exhausted." % f["name"]))
    if (distinct & {"ok", "truncated"}) and not calls:
        out.append(_notice("warning", "no_records",
                           "Telemetry files exist but contain no readable records."))
    if tel["gap_total"]:
        kinds = Counter(g["code"] for g in tel["gaps"])
        parts = ["%s (%s)" % (GAP_TEXT.get(c, c), n) for c, n in sorted(kinds.items())]
        out.append(_notice("warning", "telemetry_gaps",
                           "The accounting stream is incomplete: %d gap(s) - %s. Per-call estimates "
                           "after a gap are suppressed." % (tel["gap_total"], "; ".join(parts))))
    if tel["conflict_total"]:
        out.append(_notice("error", "conflicting_call_ids",
                           "%d call ID(s) appear with conflicting records; accounting is "
                           "invalidated across them." % tel["conflict_total"]))
    if tel["duplicates_identical"]:
        out.append(_notice("info", "duplicates",
                           "%d identical duplicate record(s) were counted once."
                           % tel["duplicates_identical"]))
    if not tel["stable_read"]:
        out.append(_notice("warning", "unstable_read",
                           "Telemetry changed during the read; continuity across files is not "
                           "established. Refresh to retry."))
    if tel["unterminated_but_complete"]:
        out.append(_notice("info", "unterminated_line",
                           "%d complete record(s) had no trailing newline (possibly still being "
                           "written)." % tel["unterminated_but_complete"]))
    if any(c["outcome_class"] == "unknown" and c["estimate"]["reasons"]
           and c["estimate"]["reasons"][0]["code"] == "unsupported_schema" for c in calls):
        out.append(_notice("warning", "unsupported_schema",
                           "Records with an unsupported telemetry schema were found; their usage "
                           "basis is unknown."))
    if any(r["code"] == "unsupported_cli" for c in calls for r in c["estimate"]["reasons"]):
        out.append(_notice("warning", "unsupported_cli",
                           "Records from an unsupported CLI version were found; their accounting "
                           "basis is unknown."))
    if inflight_state["state"] not in ("ok", "missing"):
        out.append(_notice("error", "inflight_" + inflight_state["state"],
                           "In-flight markers could not be read (%s)." % inflight_state["state"]))
    if inflight_state["problems"] or inflight_state["truncated"]:
        out.append(_notice("warning", "inflight_partial",
                           "Some in-flight markers were unreadable (%d) or beyond the bound (%s)."
                           % (inflight_state["problems"], "yes" if inflight_state["truncated"] else "no")))
    return out


def _incomplete_reasons(state, calls):
    """Codes explaining why the source cannot be called a complete accounting stream."""
    tel, inflight = state["telemetry"], state["inflight"]
    reasons = []
    if state["state_dir_state"] != "ok":
        return ["state_dir_" + state["state_dir_state"]]
    states = [f["state"] for f in tel["files"]]
    if all(s == "missing" for s in states):
        reasons.append("no_telemetry")
    elif not calls:
        reasons.append("no_records")
    for s in sorted(set(states) - {"ok", "missing"}):
        reasons.append("telemetry_file_" + s)
    if tel["gap_total"]:
        reasons.append("telemetry_gaps")
    if tel["conflict_total"]:
        reasons.append("conflicting_call_ids")
    if tel["scan_truncated"]:
        reasons.append("scan_budget_exceeded")
    if not tel["stable_read"]:
        reasons.append("unstable_read")
    if inflight["state"] not in ("ok", "missing"):
        reasons.append("inflight_" + inflight["state"])
    if inflight["problems"]:
        reasons.append("inflight_unreadable_markers")
    if inflight["truncated"]:
        reasons.append("inflight_truncated")
    return reasons


def _sort_key(call):
    ts = obs_reader.parse_timestamp(call["started_at"])
    return (ts.timestamp() if ts else float("-inf"), call["stream_index"])


def build_snapshot(state_dir, now=None, label="custom"):
    """Return the JSON-serialisable snapshot. Pure read; never raises on bad data."""
    now = now or datetime.now(timezone.utc)
    state = obs_reader.read_state(state_dir)
    acc = obs_accounting.compute(state, now)
    calls = acc["calls"]
    for i, call in enumerate(calls):
        call["stream_index"] = i

    notices = _notices(state, calls, state["inflight"])
    incomplete = _incomplete_reasons(state, calls)
    if incomplete and not any(n["level"] in ("error", "warning") for n in notices):
        notices.append(_notice("warning", "source_incomplete",
                               "The source cannot be confirmed complete (%s)." % ", ".join(incomplete)))
    if state["telemetry"]["scan_truncated"]:
        notices.append(_notice("warning", "scan_budget_exceeded",
                               "The scan budget was exhausted: older telemetry was not read and "
                               "estimates before that point are suppressed."))
    statuses = Counter(c["estimate"]["status"] for c in calls)
    classes = Counter(c["outcome_class"] for c in calls)
    eligible = statuses["eligible_estimate"] + statuses["fresh_baseline_estimate"]
    runs = [c["durations"]["run_ms"] for c in calls
            if c["outcome_class"] == "success" and c["durations"]["run_ms"] is not None]
    if 0 < eligible < SMALL_SAMPLE:
        notices.append(_notice("info", "small_sample",
                               "Only %d call(s) have an interval estimate; treat patterns with "
                               "caution." % eligible))
    if calls and eligible == 0:
        notices.append(_notice("info", "no_estimates",
                               "No call has a trustworthy per-call estimate; raw "
                               "counters are shown as observed."))

    ordered = sorted(calls, key=_sort_key, reverse=True)
    shown = ordered[:MAX_CALLS_SERVED]
    return {
        "schema": SNAPSHOT_SCHEMA,
        "generated_at": _iso(now),
        "source": {
            "state_dir": label,
            "state_dir_state": state["state_dir_state"],
            "files": state["telemetry"]["files"],
            "inflight": {k: state["inflight"][k] for k in ("state", "skipped", "problems", "truncated")},
            "stable_read": state["telemetry"]["stable_read"],
            "gap_total": state["telemetry"]["gap_total"],
            "incomplete_reasons": incomplete,
            "complete": not incomplete,
        },
        "notices": notices,
        "summary": {
            "calls_total": len(calls),
            "calls_served": len(shown),
            "by_outcome": dict(classes),
            "by_estimate_status": dict(statuses),
            "estimates": eligible,
            "small_sample": 0 < eligible < SMALL_SAMPLE,
            "median_run_ms_success": int(statistics.median(runs)) if runs else None,
            "run_ms_samples": len(runs),
        },
        "limitations": obs_accounting.LIMITATIONS,
        "calls": shown,
        "sessions": acc["sessions"],
        "inflight": acc["inflight"],
    }


# ---------------------------------------------------------------------- server

def _load_assets():
    static = os.path.join(HERE, "static")
    spec = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/static/style.css": ("style.css", "text/css; charset=utf-8"),
    }
    assets = {}
    for route, (fname, ctype) in spec.items():
        with open(os.path.join(static, fname), "rb") as fh:
            assets[route] = (fh.read(), ctype)
    return assets


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port, state_dir, label="custom", assets=None):
        self.state_dir = state_dir
        self.label = label
        self.assets = assets if assets is not None else _load_assets()
        self._cache_lock = threading.Lock()
        self._cached = None
        super().__init__((BIND_HOST, port), _Handler)

    def snapshot_bytes(self):
        with self._cache_lock:
            now = time.monotonic()
            if self._cached is None or now - self._cached[0] > CACHE_SECONDS:
                snap = build_snapshot(self.state_dir, label=self.label)
                body = json.dumps(snap, ensure_ascii=True, separators=(",", ":")).encode("ascii")
                self._cached = (now, body)
            return self._cached[1]

    def allowed_hosts(self):
        port = self.server_address[1]
        return {"127.0.0.1:%d" % port, "localhost:%d" % port}


class _Handler(BaseHTTPRequestHandler):
    server_version = "obs-dashboard"
    sys_version = ""
    timeout = 10

    def __getattr__(self, name):
        # Every method other than GET (including HEAD/OPTIONS) is refused uniformly.
        if name.startswith("do_") and name != "do_GET":
            return self._method_not_allowed
        raise AttributeError(name)

    log_status = "-"

    def log_message(self, fmt, *args):  # never echo request targets
        sys.stderr.write("obs-dashboard: %s %s\n" % (getattr(self, "command", None) or "-", self.log_status))

    def end_headers(self):
        # Applies to every response, including the stdlib's own error pages.
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        super().end_headers()

    def _send(self, status, body, ctype, extra=None):
        self.log_status = status
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _plain(self, status, text, extra=None):
        self._send(status, (text + "\n").encode("ascii"), "text/plain; charset=utf-8", extra)

    def _method_not_allowed(self):
        # Drain a small request body so closing the socket does not reset the reply.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if 0 < length <= 65536:
            self.rfile.read(length)
        self.close_connection = True
        self._plain(405, "method not allowed", {"Allow": "GET"})

    def do_GET(self):
        # Repeated trust headers are ambiguous: refuse rather than pick one.
        hosts = self.headers.get_all("Host") or []
        origins = self.headers.get_all("Origin") or []
        fetch_sites = self.headers.get_all("Sec-Fetch-Site") or []
        if len(hosts) > 1 or len(origins) > 1 or len(fetch_sites) > 1:
            return self._plain(400, "ambiguous headers")
        host = (hosts[0] if hosts else "").strip().lower()
        if host not in self.server.allowed_hosts():
            return self._plain(403, "forbidden host")
        if origins:
            allowed = {"http://" + h for h in self.server.allowed_hosts()}
            if origins[0].strip().lower() not in allowed:
                return self._plain(403, "forbidden origin")
        if (fetch_sites[0] if fetch_sites else "same-origin").strip().lower() not in (
                "same-origin", "none"):
            return self._plain(403, "forbidden fetch site")

        # Exact match on the raw request target: no decoding, query or normalisation
        # (the stdlib rewrites a leading "//" in self.path, so use the request line).
        parts = self.requestline.split(" ")
        target = parts[1] if len(parts) == 3 else self.path
        if target == "/api/snapshot":
            return self._send(200, self.server.snapshot_bytes(), "application/json; charset=utf-8")
        asset = self.server.assets.get(target)
        if asset is None:
            return self._plain(404, "not found")
        return self._send(200, asset[0], asset[1])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only local dashboard for v2 bridge telemetry.")
    parser.add_argument("--state-dir", default=None,
                        help="bridge state directory (default: ~/%s)" % DEFAULT_STATE_DIR)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="localhost port (default %d); always binds 127.0.0.1" % DEFAULT_PORT)
    parser.add_argument("--snapshot-once", action="store_true",
                        help="print one snapshot as JSON and exit (no server)")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be 0-65535")
    state_dir = args.state_dir or default_state_dir()
    label = "custom (--state-dir)" if args.state_dir else "default (~/%s)" % DEFAULT_STATE_DIR

    if args.snapshot_once:
        json.dump(build_snapshot(state_dir, label=label), sys.stdout, indent=2, ensure_ascii=True)
        sys.stdout.write("\n")
        return 0
    try:
        server = DashboardServer(args.port, state_dir, label)
    except OSError as exc:
        sys.stderr.write("cannot bind %s:%d (%s)\n" % (BIND_HOST, args.port, exc.strerror or exc))
        return 2
    print("OBS-01 dashboard: http://%s:%d/  (source: %s; read-only; Ctrl+C to stop)"
          % (BIND_HOST, server.server_address[1], label))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
