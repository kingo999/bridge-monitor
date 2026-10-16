"""Regenerate requirements.lock.txt (hash-pinned) from the *currently installed* venv.

    .venv\\Scripts\\python.exe tools/make_lock.py

Steps: ``pip freeze`` the venv -> ``pip download --no-deps --only-binary=:all:``
every pin into a temporary directory -> write ``name==version --hash=sha256:...``.
The result is specific to this platform/Python (see header) and is installed with
``pip install --require-hashes --no-deps -r requirements.lock.txt``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "requirements.lock.txt"
SKIP = {"pip", "setuptools", "wheel"}
WIN_ONLY = {"pywin32"}


def freeze() -> list[tuple[str, str]]:
    out = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"],
        capture_output=True, text=True, check=True,
    ).stdout
    pins = []
    for line in out.splitlines():
        if "==" in line:
            name, version = line.strip().split("==", 1)
            if name.lower() not in SKIP:
                pins.append((name, version))
    return sorted(pins, key=lambda p: p[0].lower())


def main() -> None:
    pins = freeze()
    lines = [
        "# Hash-pinned lock for the tested v2 stack.  Regenerate with tools/make_lock.py.",
        f"# Platform: {platform.system()} {platform.machine()} (win_amd64 wheels)  Python: {platform.python_version()}",
        "# Direct requirement: mcp==1.30.0 (see requirements.in).  Install with:",
        "#   python -m pip install --require-hashes --no-deps -r requirements.lock.txt",
        "# Tested Codex CLI: 0.162.1 (not a Python dependency; checked at runtime).",
    ]
    with tempfile.TemporaryDirectory(prefix="v2_lock_") as tmp:
        for name, version in pins:
            subprocess.run(
                [sys.executable, "-m", "pip", "download", "--disable-pip-version-check", "--quiet",
                 "--no-deps", "--only-binary=:all:", "-d", tmp, f"{name}=={version}"],
                check=True,
            )
        wheels = {p.name.lower(): p for p in Path(tmp).iterdir()}
        for name, version in pins:
            prefix = name.lower().replace("-", "_") + "-" + version.lower() + "-"
            # wheel filenames already encode the distribution name with underscores
            matches = [p for n, p in wheels.items() if n.startswith(prefix)]
            if len(matches) != 1:
                raise SystemExit(f"cannot match wheel for {name}=={version}: {matches}")
            digest = hashlib.sha256(matches[0].read_bytes()).hexdigest()
            marker = " ; sys_platform == \"win32\"" if name.lower() in WIN_ONLY else ""
            lines.append(f"{name}=={version}{marker} \\")
            lines.append(f"    --hash=sha256:{digest}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT} with {len(pins)} pins", file=sys.stderr)


if __name__ == "__main__":
    main()
