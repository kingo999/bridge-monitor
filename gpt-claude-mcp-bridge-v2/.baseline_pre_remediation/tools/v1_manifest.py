"""Snapshot and compare the files v2 work must never change.

    python -B tools/v1_manifest.py snapshot  > before.json
    python -B tools/v1_manifest.py compare before.json

Content hashes are taken for v1's own source/README and for v1's session
records (``~/.gpt-architect-bridge``; thread IDs only). For the Codex
configuration and login files only size and mtime are read: credential
contents are never opened.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

V1_DIR = Path(r"C:\AI\gpt-claude-mcp-bridge")
V1_STATE = Path.home() / ".gpt-architect-bridge"
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stat(path: Path) -> dict | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns}


def snapshot() -> dict:
    result: dict = {"v1_files": {}, "v1_listing": [], "v1_state": {}, "codex_stat": {}}
    for name in ("architect_bridge.py", "README.md"):
        path = V1_DIR / name
        result["v1_files"][name] = _sha256(path) if path.exists() else None
    if V1_DIR.exists():
        result["v1_listing"] = sorted(p.name for p in V1_DIR.iterdir())
    venv = V1_DIR / ".venv"
    if venv.exists():
        # file count + total size is a cheap tripwire for venv modification
        count = size = 0
        for root, _dirs, files in os.walk(venv):
            for name in files:
                count += 1
                try:
                    size += (Path(root) / name).stat().st_size
                except OSError:
                    pass
        result["v1_venv"] = {"files": count, "bytes": size}
    if V1_STATE.exists():
        for path in sorted(V1_STATE.iterdir()):
            if path.is_file():
                result["v1_state"][path.name] = _sha256(path)
    for name in ("config.toml", "auth.json"):
        result["codex_stat"][name] = _stat(CODEX_HOME / name)
    return result


def compare(before_path: Path) -> int:
    before = json.loads(before_path.read_text(encoding="utf-8-sig"))
    after = snapshot()
    problems = []
    for key in before:
        if before[key] != after.get(key):
            problems.append(key)
    if problems:
        print("CHANGED:", ", ".join(problems))
        return 1
    print("v1 files, v1 venv tripwire, v1 session records and Codex config/auth stat: UNCHANGED")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "snapshot":
        print(json.dumps(snapshot(), indent=2, sort_keys=True))
    elif len(sys.argv) == 3 and sys.argv[1] == "compare":
        sys.exit(compare(Path(sys.argv[2])))
    else:
        print(__doc__)
        sys.exit(2)
