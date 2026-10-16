"""Repository state fingerprint (F6/N11): detect that the repository changed during a review.

A review is a snapshot question about a *moving* tree (session locks serialise bridge
calls, they do not freeze the repository).  The fingerprint combines

* ``HEAD`` (or the literal ``UNBORN``),
* ``git status --porcelain=v1 -z --untracked-files=all`` (paths + XY state),
* a streamed hash of the **staged** diff (``diff --cached``: index vs HEAD, or vs the empty
  tree on an unborn repository),
* a streamed hash of the **unstaged** diff (``diff``: worktree vs index),
* size + mtime of every untracked path (their content is not in either diff).

Staged and unstaged content are hashed separately so that a change to staged bytes that
leaves status and worktree bytes untouched (``MM`` -> ``MM``) is still seen, and an unborn
repository is covered.  Any truncated or incomplete read makes the result *unavailable*,
never comparable.  Only digests leave this module.  All git runs through ``gitsafe`` (no
filters/textconv/fsmonitor helpers, no index writes).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import threading
from typing import Any, Mapping

from . import gitsafe

_STATUS_CAP = 8 * 1024 * 1024


def _run_capture(argv: list[str], env: Mapping[str, str], timeout_s: float, hasher: Any = None,
                 cap: int | None = None) -> tuple[int, bytes, bool]:
    """(returncode, captured bytes (None-hasher mode), complete).  complete=False on truncation
    or when the reader did not finish."""
    cap = _STATUS_CAP if cap is None else cap
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env=dict(env))
    kept = bytearray()
    state = {"truncated": False}

    def pump() -> None:
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(65536), b""):
            if hasher is not None:
                hasher.update(chunk)
            elif len(kept) + len(chunk) > cap:
                state["truncated"] = True      # keep draining so git can exit, but do not trust the prefix
            else:
                kept.extend(chunk)

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        t.join(timeout=2)
        if not t.is_alive() and proc.stdout is not None:
            proc.stdout.close()
        raise
    t.join(timeout=10)
    complete = (not t.is_alive()) and not state["truncated"]
    if not t.is_alive() and proc.stdout is not None:
        proc.stdout.close()
    return proc.returncode or 0, bytes(kept), complete


def repo_fingerprint(root: Path, env: Mapping[str, str], timeout_s: float = 40.0) -> dict[str, Any]:
    """Return {"ok": bool, "head": str|None, "digest": 64-hex|None, "reason": str|None}."""
    out: dict[str, Any] = {"ok": False, "head": None, "digest": None, "reason": None}
    if not gitsafe.supported():
        out["reason"] = "git_too_old_for_safe_mode"
        return out
    genv = gitsafe.safe_git_env(env)

    def git(*args: str) -> list[str]:
        return gitsafe.safe_git_argv(root, *args)

    try:
        rc, head_raw, ok = _run_capture(git("rev-parse", "--verify", "-q", "HEAD"), genv, timeout_s)
        head = head_raw.decode("ascii", errors="replace").strip() if (rc == 0 and ok) else "UNBORN"
        out["head"] = head

        rc, status, ok = _run_capture(git("status", "--porcelain=v1", "-z", "--untracked-files=all"), genv, timeout_s)
        if rc != 0:
            out["reason"] = "status_failed"
            return out
        if not ok:
            out["reason"] = "status_truncated_or_incomplete"
            return out

        digest = hashlib.sha256()
        digest.update(head.encode("ascii", errors="replace") + b"\0")
        digest.update(status + b"\0")

        for label, args in (("cached", ("diff", "--cached", "--no-ext-diff", "--no-textconv", "--binary", "--no-color")),
                            ("worktree", ("diff", "--no-ext-diff", "--no-textconv", "--binary", "--no-color"))):
            digest.update(label.encode() + b"\0")
            rc, _, ok = _run_capture(git(*args), genv, timeout_s, hasher=digest)
            if rc not in (0, 1):
                out["reason"] = f"{label}_diff_failed"
                return out
            if not ok:
                out["reason"] = f"{label}_diff_incomplete"
                return out
            digest.update(b"\0")

        for entry in status.split(b"\0"):
            if entry.startswith(b"?? "):
                rel = entry[3:].decode("utf-8", errors="surrogateescape")
                try:
                    info = (root / rel).stat()
                    digest.update(f"{rel}\0{info.st_size}\0{info.st_mtime_ns}\0".encode("utf-8", "surrogateescape"))
                except OSError:
                    digest.update(f"{rel}\0unstatable\0".encode("utf-8", "surrogateescape"))
        out["digest"] = digest.hexdigest()
        out["ok"] = True
    except subprocess.TimeoutExpired:
        out["reason"] = "timeout"
    except OSError:
        out["reason"] = "git_unavailable"
    return out


def compare(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Content-free comparison result for telemetry / warnings."""
    comparable = bool(before.get("ok") and after.get("ok"))
    changed = comparable and (before["digest"] != after["digest"])
    return {
        "comparable": comparable,
        "changed": bool(changed),
        "head_changed": bool(comparable and before.get("head") != after.get("head")),
        "before": (before.get("digest") or "")[:16] or None,
        "after": (after.get("digest") or "")[:16] or None,
        "head_before": (before.get("head") or "")[:12] or None,
        "head_after": (after.get("head") or "")[:12] or None,
        "reason": None if comparable else (before.get("reason") or after.get("reason") or "unavailable"),
    }
