"""Repository state fingerprint (F6): detect that the repository changed during a review.

A review is a snapshot question about a *moving* tree (session locks serialise bridge
calls, they do not freeze the repository).  The fingerprint combines

* ``HEAD`` (or the literal ``UNBORN``),
* ``git status --porcelain=v1 -z --untracked-files=all`` (paths + XY state),
* a streamed hash of ``git diff --binary HEAD`` (tracked content, staged and unstaged),
* size + mtime of every untracked path (their content is not in the diff).

Only digests leave this module: no path list, diff text or source content is kept or
logged.  Git is run with ``--no-optional-locks`` so the bridge never writes ``.git/index``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import threading
from typing import Any, Mapping


def _run_capture(argv: list[str], env: Mapping[str, str], timeout_s: float, hasher: Any = None) -> tuple[int, bytes]:
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env=dict(env))
    kept = bytearray()

    def pump() -> None:
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(65536), b""):
            if hasher is not None:
                hasher.update(chunk)
            elif len(kept) < 8 * 1024 * 1024:
                kept.extend(chunk)

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        t.join(timeout=2)
        raise
    t.join(timeout=10)
    return proc.returncode or 0, bytes(kept)


def repo_fingerprint(root: Path, env: Mapping[str, str], timeout_s: float = 40.0) -> dict[str, Any]:
    """Return {"ok": bool, "head": str|None, "digest": 64-hex|None, "reason": str|None}."""
    out: dict[str, Any] = {"ok": False, "head": None, "digest": None, "reason": None}
    base = ["git", "--no-optional-locks", "-C", str(root)]
    genv = dict(env)
    genv["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        rc, head_raw = _run_capture([*base, "rev-parse", "--verify", "-q", "HEAD"], genv, timeout_s)
        head = head_raw.decode("ascii", errors="replace").strip() if rc == 0 else "UNBORN"
        out["head"] = head

        rc, status = _run_capture([*base, "status", "--porcelain=v1", "-z", "--untracked-files=all"], genv, timeout_s)
        if rc != 0:
            out["reason"] = "status_failed"
            return out

        digest = hashlib.sha256()
        digest.update(head.encode("ascii", errors="replace") + b"\0")
        digest.update(status + b"\0")

        if head != "UNBORN":
            rc, _ = _run_capture([*base, "diff", "--no-ext-diff", "--binary", "--no-color", "HEAD"], genv,
                                 timeout_s, hasher=digest)
            if rc not in (0, 1):
                out["reason"] = "diff_failed"
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
