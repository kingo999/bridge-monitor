"""Bounded, read-only reader for v2 bridge telemetry and in-flight markers.

Produces a content-free observation: an ordered list of projected records and
barriers (places where accounting continuity cannot be assumed), plus source
status. It never writes, never lists anything outside the fixed allow-list and
never echoes paths, prompts, answers or arbitrary nested prose.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from datetime import datetime, timezone

MAX_TELEMETRY_FILE_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 256 * 1024
MAX_RECORDS = 20000        # raw records parsed per snapshot (newest first)
MAX_EVENTS = 30000         # records + barriers produced per snapshot
MAX_LINES_SCANNED = 200000  # physical lines visited per snapshot, blank lines included
MAX_GAP_DETAILS = 50
MAX_MARKER_BYTES = 16 * 1024
MAX_MARKERS = 200
MAX_TEXT = 120
MAX_WARNINGS = 5
MAX_COUNTER = 10**13
READ_ATTEMPTS = 3

TELEMETRY_FILES = ("calls.5.jsonl", "calls.4.jsonl", "calls.3.jsonl",
                   "calls.2.jsonl", "calls.1.jsonl", "calls.jsonl")  # oldest first

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_IDENT = re.compile(r"^[A-Za-z0-9._:\-]{1,80}$")
_NAME = re.compile(r"^[\w.\- ]{1,64}$")
_CALL_ID = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$")
_MARKER_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,100}\.json$")
_REASON = re.compile(r"^[a-z0-9_]{1,48}$")


class SourceProblem(Exception):
    def __init__(self, state: str):
        super().__init__(state)
        self.state = state  # missing | refused_link | refused_not_regular | unreadable | changed


# --------------------------------------------------------------------- helpers

def parse_timestamp(value):
    """Return an aware UTC datetime or None. Naive or out-of-range values are invalid."""
    if not isinstance(value, str) or len(value) > 40 or not _TS.match(value):
        return None
    try:
        dt = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if dt.tzinfo is None or not 2000 <= dt.year <= 2100:
        return None
    return dt.astimezone(timezone.utc)


def _counter(value):
    return value if type(value) is int and 0 <= value <= MAX_COUNTER else None


def _bool(value):
    return value if type(value) is bool else None


def _ident(value, pattern=_IDENT):
    return value if isinstance(value, str) and pattern.match(value) else None


def safe_text(value, limit=MAX_TEXT):
    """Bounded display text with control/format characters neutralised."""
    if not isinstance(value, str):
        return None
    out = []
    for ch in value[:limit + 1]:
        cat = unicodedata.category(ch)
        out.append("?" if cat[0] == "C" or cat in ("Zl", "Zp") else ch)
    text = "".join(out)
    return text[:limit] + "…" if len(text) > limit else text


def _is_link_like(st):
    """Symlinks and any Windows reparse point (junctions, mount points, ...)."""
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _check_ancestors(path):
    """Refuse links/reparse points among the absolute lexical ancestors of *path*.

    The path is made absolute without resolving anything, so a link in the chain
    is seen rather than followed.
    """
    current = os.path.abspath(os.fspath(path))
    while True:
        parent = os.path.dirname(current)
        if parent == current:  # filesystem or drive root
            return
        current = parent
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            raise SourceProblem("missing")
        except OSError:
            raise SourceProblem("unreadable")
        if _is_link_like(st):
            raise SourceProblem("refused_link")


def _lstat_checked(path, want_dir=False):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise SourceProblem("missing")
    except OSError:
        raise SourceProblem("unreadable")
    if _is_link_like(st):
        raise SourceProblem("refused_link")
    if want_dir and not stat.S_ISDIR(st.st_mode):
        raise SourceProblem("refused_not_regular")
    if not want_dir and not stat.S_ISREG(st.st_mode):
        raise SourceProblem("refused_not_regular")
    return st


def _read_bounded(path, max_bytes):
    """Return (data, size, head_truncated, tail_cut, fingerprint) for a regular file."""
    st = _lstat_checked(path)
    try:
        with open(path, "rb") as fh:
            fst = os.fstat(fh.fileno())
            if not stat.S_ISREG(fst.st_mode):
                raise SourceProblem("refused_not_regular")
            if st.st_ino and fst.st_ino and (st.st_ino, st.st_dev) != (fst.st_ino, fst.st_dev):
                raise SourceProblem("changed")
            size = fst.st_size
            head = False
            if size > max_bytes:
                head = True
                fh.seek(size - max_bytes - 1)
                data = fh.read(max_bytes + 2)
                prev, data = data[:1], data[1:]
                if prev != b"\n":  # landed mid-line: drop the partial first line
                    nl = data.find(b"\n")
                    data = b"" if nl < 0 else data[nl + 1:]
            else:
                data = fh.read(max_bytes + 1)
            tail_cut = len(data) > max_bytes
            if tail_cut:
                data = data[:max_bytes]
            return data, size, head, tail_cut, (st.st_ino, st.st_size, st.st_mtime_ns)
    except SourceProblem:
        raise
    except OSError:
        raise SourceProblem("unreadable")


def _strict_loads(text):
    def reject(_c):
        raise ValueError("non-finite number")
    return json.loads(text, parse_constant=reject)


def _canonical_digest(obj):
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("ascii")).hexdigest()


# ------------------------------------------------------------------ projection

def project_record(raw):
    """Allow-list projection of one schema-1 record. Unknown fields are only counted."""
    route = raw.get("route") if isinstance(raw.get("route"), dict) else {}
    sess = raw.get("session") if isinstance(raw.get("session"), dict) else {}
    usage_raw = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
    repo = raw.get("repo") if isinstance(raw.get("repo"), dict) else {}

    usage = {}
    usage_invalid = []
    for key in ("input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_output_tokens", "total_tokens"):
        value = _counter(usage_raw.get(key)) if usage_raw else None
        if usage_raw and usage_raw.get(key) is not None and value is None:
            usage_invalid.append(key)
        usage[key] = value
    warnings = raw.get("warnings") if isinstance(raw.get("warnings"), list) else []

    started = raw.get("started_at")
    ended = raw.get("ended_at")
    rec = {
        "schema_version": raw.get("schema_version") if type(raw.get("schema_version")) is int else None,
        "call_id": _ident(raw.get("call_id"), _CALL_ID),
        "tool": _ident(raw.get("tool")),
        "started_at": started if parse_timestamp(started) else None,
        "ended_at": ended if parse_timestamp(ended) else None,
        "route": {
            "model": _ident(route.get("model")),
            "effort": _ident(route.get("effort")),
            "model_source": _ident(route.get("model_source")),
            "effort_source": _ident(route.get("effort_source")),
        },
        "session": {
            "name": _ident(sess.get("name"), _NAME),
            "repo_key": _ident(sess.get("repo_key")),
            "thread_id": _ident(sess.get("thread_id")),
            "resumed": _bool(sess.get("resumed")),
        },
        "queue_ms": _counter(raw.get("queue_ms")),
        "run_ms": _counter(raw.get("run_ms")),
        "outcome": _ident(raw.get("outcome")),
        "error_category": safe_text(raw.get("error_category"), 64),
        "error_code": safe_text(raw.get("error_code"), 64),
        "inference_may_have_started": _bool(raw.get("inference_may_have_started")),
        "exit_code": raw.get("exit_code") if type(raw.get("exit_code")) is int
        and -(2**31) <= raw.get("exit_code") < 2**31 else None,
        "usage_present": usage_raw is not None,
        "usage": usage,
        "usage_invalid": usage_invalid,
        "cli_version": _ident(raw.get("cli_version")),
        "warnings": [t for t in (safe_text(w, 160) for w in warnings[:MAX_WARNINGS]) if t is not None],
        "warnings_total": len(warnings),
        "prompt_chars": _counter(raw.get("prompt_chars")),
        "answer_chars": _counter(raw.get("answer_chars")),
        "repo": {
            "changed": _bool(repo.get("changed")),
            "comparable": _bool(repo.get("comparable")),
            "head_changed": _bool(repo.get("head_changed")),
        },
        "pointer_state": _ident(raw.get("pointer_state")),
        "history": _ident(raw.get("history")),
        "session_state": _ident(raw.get("session_state")),
    }
    return rec


def _minimal_projection(raw):
    """Display-only projection for records whose schema we do not understand."""
    return {
        "schema_version": raw.get("schema_version") if type(raw.get("schema_version")) is int else None,
        "call_id": _ident(raw.get("call_id"), _CALL_ID),
        "started_at": raw.get("started_at") if parse_timestamp(raw.get("started_at")) else None,
    }


# --------------------------------------------------------------- telemetry read

def _scan_file(name, data, head_truncated, events_out, gaps, counters):
    """Scan one telemetry file newest line first under the snapshot budget.

    Appends the file's events to *events_out* in forward (oldest first) order.
    Returns (records_parsed, complete); complete is False when the budget ran out
    before the start of the file was reached.
    """
    local = []  # collected newest first, reversed on exit

    def spend(kind):
        counters["events"] += 1
        counters["gap_total"] += kind == "barrier"
        return counters["events"] <= MAX_EVENTS

    def gap(code, line):
        if not spend("barrier"):
            return False
        if len(gaps) < MAX_GAP_DETAILS:
            gaps.append({"code": code, "file": name, "line": line})
        local.append({"kind": "barrier", "scope": "global", "reason": code, "file": name})
        return True

    parsed = 0
    complete = True
    number = data.count(b"\n") + (0 if not data or data.endswith(b"\n") else 1)
    end = len(data) - 1 if data.endswith(b"\n") else len(data)
    first = True
    while True:
        counters["lines"] += 1
        if counters["lines"] > MAX_LINES_SCANNED:
            complete = False
            break
        start = data.rfind(b"\n", 0, end) + 1
        line = data[start:end]
        trailing_partial = first and not data.endswith(b"\n") and line != b""
        first = False
        line_no = number
        number -= 1
        if line.endswith(b"\r"):
            line = line[:-1]
        if line.strip():
            if len(line) > MAX_LINE_BYTES:
                ok = gap("oversized_line", line_no)
            else:
                try:
                    raw = _strict_loads(line.decode("utf-8"))
                except (ValueError, RecursionError, UnicodeDecodeError):
                    raw = None
                    ok = gap("partial_trailing_line" if trailing_partial else "malformed_line", line_no)
                else:
                    if not isinstance(raw, dict):
                        ok = gap("non_object_line", line_no)
                    elif counters["records"] >= MAX_RECORDS or not spend("record"):
                        ok = False
                    else:
                        counters["records"] += 1
                        if trailing_partial:
                            counters["unterminated_but_complete"] += 1
                        parsed += 1
                        local.append({"kind": "raw", "file": name, "line": line_no, "raw": raw})
                        ok = True
            if not ok:
                complete = False
                break
        if start == 0:
            break
        end = start - 1
    if complete and head_truncated:
        gap("file_head_truncated", None)
    events_out.extend(reversed(local))
    return parsed, complete


def _read_telemetry_once(telemetry_dir):
    files_by_name, gaps = {}, []
    per_file = {}  # name -> that file's events, oldest first
    counters = {"gap_total": 0, "unterminated_but_complete": 0,
                "events": 0, "records": 0, "lines": 0}
    fingerprints = {}
    present = []
    scan_truncated = False
    dir_state = None
    try:
        _lstat_checked(telemetry_dir, want_dir=True)
    except SourceProblem as exc:
        dir_state = exc.state
    prefix = []  # events that precede every file
    if dir_state not in (None, "missing"):
        counters["gap_total"] += 1
        gaps.append({"code": "telemetry_dir_" + dir_state, "file": None, "line": None})
        prefix.append({"kind": "barrier", "scope": "global",
                       "reason": "telemetry_dir_" + dir_state, "file": None})

    # Newest file first so a budget cut always drops the oldest history.
    for name in reversed(TELEMETRY_FILES):
        info = {"name": name, "state": "missing", "bytes": None, "lines": 0, "records": 0}
        files_by_name[name] = info
        if dir_state is not None:
            info["state"] = dir_state
            continue
        if scan_truncated:
            info["state"] = "not_read"
            continue
        path = os.path.join(telemetry_dir, name)
        try:
            data, size, head, tail_cut, fp = _read_bounded(path, MAX_TELEMETRY_FILE_BYTES)
        except SourceProblem as exc:
            info["state"] = exc.state
            if exc.state != "missing":
                counters["gap_total"] += 1
                if len(gaps) < MAX_GAP_DETAILS:
                    gaps.append({"code": "file_" + exc.state, "file": name, "line": None})
                per_file[name] = [{"kind": "barrier", "scope": "global",
                                   "reason": "file_" + exc.state, "file": name}]
            continue
        fingerprints[name] = fp
        present.append(name)
        info["bytes"] = size
        info["state"] = "truncated" if head or tail_cut else "ok"
        info["lines"] = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        events = [{"kind": "boundary", "file": name}]  # start of this file's events
        info["records"], complete = _scan_file(name, data, head, events, gaps, counters)
        per_file[name] = events
        if not complete:
            scan_truncated = True
            info["state"] = "truncated"

    files = [files_by_name[n] for n in TELEMETRY_FILES]
    raw_events = list(prefix)
    if scan_truncated:
        counters["gap_total"] += 1
        gaps.append({"code": "scan_budget_exceeded", "file": None, "line": None})
        raw_events.append({"kind": "barrier", "scope": "global",
                           "reason": "scan_budget_exceeded", "file": None})
    for name in TELEMETRY_FILES:
        raw_events.extend(per_file.get(name, []))

    # Rotation continuity: every run of consecutive missing files between the oldest
    # and newest present file is an unseen segment of the stream and gets its own
    # barrier, placed immediately before the next present file. A missing newest
    # file is only reported through its file state (a rotation just happened).
    idxs = sorted(TELEMETRY_FILES.index(n) for n in present)
    if idxs:
        segments = []
        for i in range(idxs[0], idxs[-1]):
            if i in idxs:
                continue
            if segments and segments[-1][-1] == i - 1:
                segments[-1].append(i)
            else:
                segments.append([i])
        for seg in segments:
            counters["gap_total"] += 1
            if len(gaps) < MAX_GAP_DETAILS:
                gaps.append({"code": "rotation_gap", "file": TELEMETRY_FILES[seg[0]], "line": None})
        for seg in reversed(segments):  # latest position first keeps earlier positions valid
            pos = next(k for k, ev in enumerate(raw_events)
                       if ev.get("kind") == "boundary"
                       and TELEMETRY_FILES.index(ev["file"]) > seg[-1])
            raw_events.insert(pos, {"kind": "barrier", "scope": "global",
                                    "reason": "rotation_gap", "file": TELEMETRY_FILES[seg[0]]})
    return files, raw_events, gaps, counters, fingerprints, present


def _stat_fingerprints(telemetry_dir, names):
    out = {}
    for name in names:
        try:
            st = os.lstat(os.path.join(telemetry_dir, name))
            out[name] = (st.st_ino, st.st_size, st.st_mtime_ns)
        except OSError:
            out[name] = None
    return out


def read_telemetry(state_dir):
    telemetry_dir = os.path.join(state_dir, "telemetry")
    stable = False
    for _ in range(READ_ATTEMPTS):
        files, raw_events, gaps, counters, fps, present = _read_telemetry_once(telemetry_dir)
        if _stat_fingerprints(telemetry_dir, present) == fps:
            stable = True
            break

    # Dedupe / conflict detection on call_id, preserving stream order.
    digests = {}
    for ev in raw_events:
        if ev.get("kind") != "raw":
            continue
        cid = _ident(ev["raw"].get("call_id"), _CALL_ID)
        ev["call_id"] = cid
        if cid is not None:
            digests.setdefault(cid, set()).add(_canonical_digest(ev["raw"]))
    conflicts = {cid for cid, d in digests.items() if len(d) > 1}

    events = []
    seen = set()
    duplicates = 0
    for ev in raw_events:
        kind = ev.get("kind")
        if kind == "boundary":
            if not stable:
                events.append({"kind": "barrier", "scope": "global",
                               "reason": "source_changed_during_read", "file": ev["file"]})
            continue
        if kind == "barrier":
            events.append(ev)
            continue
        cid, raw = ev["call_id"], ev["raw"]
        if cid is None:
            counters["gap_total"] += 1
            if len(gaps) < MAX_GAP_DETAILS:
                gaps.append({"code": "record_missing_call_id", "file": ev["file"], "line": ev["line"]})
            events.append({"kind": "barrier", "scope": "global",
                           "reason": "record_missing_call_id", "file": ev["file"]})
            continue
        if cid in conflicts:
            events.append({"kind": "barrier", "scope": "global", "reason": "conflicting_call_id",
                           "file": ev["file"]})
            if cid not in seen:
                seen.add(cid)
                events.append({"kind": "conflict", "call_id": cid, "file": ev["file"]})
            continue
        if cid in seen:
            duplicates += 1
            continue
        seen.add(cid)
        if raw.get("schema_version") == 1 and type(raw.get("schema_version")) is int:
            rec = project_record(raw)
            supported = True
        else:
            rec = _minimal_projection(raw)
            supported = False
        events.append({"kind": "record", "file": ev["file"], "line": ev["line"],
                       "supported_schema": supported, "rec": rec})

    scan_truncated = any(g["code"] == "scan_budget_exceeded" for g in gaps)
    return {
        "events": events,
        "files": files,
        "gaps": gaps,
        "gap_total": counters["gap_total"],
        "duplicates_identical": duplicates,
        "conflict_ids": sorted(conflicts)[:20],
        "conflict_total": len(conflicts),
        "scan_truncated": scan_truncated,
        "stable_read": stable,
        "unterminated_but_complete": counters["unterminated_but_complete"],
    }


# ----------------------------------------------------------------- in-flight

def _project_marker(raw):
    state = _ident(raw.get("state"))
    reason = raw.get("reason")
    return {
        "call_id": _ident(raw.get("call_id"), _CALL_ID),
        "state": state,
        "phase": _ident(raw.get("phase")),
        "ownership": _ident(raw.get("ownership")),
        "model": _ident(raw.get("model")),
        "thread_id": _ident(raw.get("thread_id")),
        "resumed": _bool(raw.get("resumed")),
        "started_at": raw.get("started_at") if parse_timestamp(raw.get("started_at")) else None,
        "since": raw.get("since") if parse_timestamp(raw.get("since")) else None,
        "reason": reason if isinstance(reason, str) and _REASON.match(reason)
        else ("unrecognized" if reason is not None else None),
    }


def read_inflight(state_dir):
    inflight_dir = os.path.join(state_dir, "inflight")
    result = {"state": "ok", "markers": [], "skipped": 0, "problems": 0, "truncated": False}
    try:
        _lstat_checked(inflight_dir, want_dir=True)
    except SourceProblem as exc:
        result["state"] = exc.state
        return result
    try:
        with os.scandir(inflight_dir) as it:
            entries = []
            for entry in it:
                entries.append(entry)
                if len(entries) > MAX_MARKERS * 4:
                    result["truncated"] = True
                    break
    except OSError:
        result["state"] = "unreadable"
        return result
    names = sorted(e.name for e in entries)
    for name in names:
        if not _MARKER_NAME.match(name):
            result["skipped"] += 1
            continue
        if len(result["markers"]) >= MAX_MARKERS:
            result["truncated"] = True
            break
        try:
            data, size, head, tail_cut, _fp = _read_bounded(os.path.join(inflight_dir, name),
                                                            MAX_MARKER_BYTES)
            if head or tail_cut:
                raise SourceProblem("oversized")
            raw = _strict_loads(data.decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
        except (SourceProblem, ValueError, RecursionError, UnicodeDecodeError):
            result["problems"] += 1
            continue
        result["markers"].append(_project_marker(raw))
    return result


def read_state(state_dir):
    """Read telemetry and in-flight markers under *state_dir* (never writes)."""
    state_dir = os.fspath(state_dir)
    try:
        _check_ancestors(state_dir)  # a link anywhere above the state dir is refused
        _lstat_checked(state_dir, want_dir=True)
        root_state = "ok"
    except SourceProblem as exc:
        root_state = exc.state
    if root_state != "ok":
        empty = {"events": [], "files": [{"name": n, "state": root_state, "bytes": None,
                                          "lines": 0, "records": 0} for n in TELEMETRY_FILES],
                 "gaps": [], "gap_total": 0, "duplicates_identical": 0, "conflict_ids": [],
                 "conflict_total": 0, "scan_truncated": False, "stable_read": True,
                 "unterminated_but_complete": 0}
        return {"state_dir_state": root_state, "telemetry": empty,
                "inflight": {"state": root_state, "markers": [], "skipped": 0,
                             "problems": 0, "truncated": False}}
    return {"state_dir_state": "ok", "telemetry": read_telemetry(state_dir),
            "inflight": read_inflight(state_dir)}
