"""Check that the active environment matches requirements.lock.txt exactly.

    .venv\\Scripts\\python.exe tools/verify_lock.py

Exit code 0 = identical set of packages and versions; 1 = drift (listed).
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"pip", "setuptools", "wheel"}


def locked() -> dict[str, str]:
    pins = {}
    for line in (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", line)
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


def installed() -> dict[str, str]:
    out = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"],
        capture_output=True, text=True, check=True,
    ).stdout
    result = {}
    for line in out.splitlines():
        if "==" in line:
            name, version = line.split("==", 1)
            if name.lower() not in SKIP:
                result[name.lower().replace("_", "-")] = version.strip()
    return result


def main() -> int:
    want, have = locked(), installed()
    problems = []
    for name in sorted(set(want) | set(have)):
        if want.get(name) != have.get(name):
            problems.append(f"{name}: lock={want.get(name)} installed={have.get(name)}")
    if problems:
        print("DRIFT:\n  " + "\n  ".join(problems))
        return 1
    print(f"environment matches requirements.lock.txt ({len(want)} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
