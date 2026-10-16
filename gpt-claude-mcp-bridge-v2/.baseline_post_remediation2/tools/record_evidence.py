"""Bind verification results to the exact code they were produced from (F8).

    python -B tools/record_evidence.py fingerprint
    python -B tools/record_evidence.py policy
    python -B tools/record_evidence.py tests --label remediation_full

Every record carries: the combined SHA-256 of the v2 source tree at the time, the exact
command, Python/OS, the Codex CLI version, the sandbox backend reported by
``codex doctor --json``, and (for `policy`) the effective capability policy read from the
installed CLI.  A result recorded for one source hash is NOT evidence for different code.

Evidence classes used in the documentation:
  implementer_executed    produced by the implementer (Claude Code) executing something, bound to a
                          hash.  NOT verification by a second party: a reviewer cannot confirm it
                          from the record alone
  source_observation      read from the code/CLI (no execution)
  historical_report       from an earlier candidate; superseded, not evidence for current code
  assumption              not verified
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE = ROOT / "docs" / "evidence"
SOURCE_GLOBS = ("bridge/*.py", "tools/*.py", "tests/*.py", "tests/fixtures/*.json", "*.py",
                "requirements.in", "requirements.lock.txt", "README.md")


def source_files() -> list[Path]:
    seen: dict[str, Path] = {}
    for pattern in SOURCE_GLOBS:
        for p in sorted(ROOT.glob(pattern)):
            if p.is_file() and "__pycache__" not in p.parts:
                seen[p.relative_to(ROOT).as_posix()] = p
    return [seen[k] for k in sorted(seen)]


def fingerprint() -> dict:
    files = []
    combined = hashlib.sha256()
    for p in source_files():
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        rel = p.relative_to(ROOT).as_posix()
        files.append({"path": rel, "sha256": digest, "bytes": p.stat().st_size})
        combined.update(rel.encode() + b"\0" + digest.encode() + b"\0")
    return {"combined_sha256": combined.hexdigest(), "file_count": len(files), "files": files}


def _run(argv: list[str], timeout: int = 60) -> str:
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return (res.stdout + res.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return f"<unavailable: {exc.__class__.__name__}>"


def environment() -> dict:
    from bridge import codexcli

    info: dict = {"python": platform.python_version(), "platform": platform.platform(),
                  "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        cli = codexcli.resolve_cli(None, os.environ)
        env = codexcli.child_env(os.environ)
        info["codex_cli_version"] = codexcli.get_version(cli, env)
        info["codex_launcher"] = cli.kind
        doctor = codexcli.run_simple([*cli.base_argv, "doctor", "--json"], env, timeout_s=90)
        backend = re.search(r'"sandbox backend":\s*"([^"]+)"', doctor.stdout)
        provisioning = re.search(r'"sandbox provisioning":\s*"([^"]+)"', doctor.stdout)
        info["sandbox_backend"] = backend.group(1) if backend else "unknown"
        info["sandbox_provisioning_at_record_time"] = provisioning.group(1) if provisioning else "unknown"
    except Exception as exc:  # noqa: BLE001
        info["codex_cli_version"] = f"<unavailable: {exc.__class__.__name__}>"
    return info


def write(name: str, payload: dict) -> Path:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def cmd_fingerprint() -> None:
    fp = fingerprint()
    out = write("source_fingerprint.json", {**fp, "environment": environment(),
                                            "evidence_class": "source_observation"})
    print(f"{out}  combined={fp['combined_sha256']}  files={fp['file_count']}")


def cmd_policy() -> None:
    from bridge import codexcli

    cli = codexcli.resolve_cli(None, os.environ)
    env = codexcli.child_env(os.environ)
    tmp = Path(tempfile.mkdtemp(prefix="evidence_repo_"))
    try:
        for args in (["init", "-q"], ["config", "user.email", "e@example.invalid"], ["config", "user.name", "e"]):
            subprocess.run(["git", "-C", str(tmp), *args], check=True, capture_output=True)
        (tmp / "a.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp), "commit", "-q", "-m", "i"], check=True, capture_output=True)
        features = codexcli.load_features(cli, env)
        audit = codexcli.capability_audit(cli, env, tmp, frozenset(), features)
        payload = {
            "evidence_class": "implementer_executed",
            "what": "effective capability policy read from the installed CLI (model-free; no sandbox "
                    "preflight, no inference)",
            "command": "capability_audit(real CLI, disposable repo, empty MCP allow-list)",
            "source_combined_sha256": fingerprint()["combined_sha256"],
            "environment": environment(),
            "policy": audit.public(),
            "argv_overrides": audit.extra_argv(),
        }
        print(write("capability_policy_real_cli.json", payload))
    finally:
        import shutil, stat

        def force(func, path, _e):
            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(tmp, onerror=force)


def cmd_tests(label: str) -> None:
    command = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"]
    started = time.monotonic()
    res = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    text = res.stdout + res.stderr
    ran = re.search(r"Ran (\d+) tests? in ([\d.]+)s", text)
    outcome = "OK" if re.search(r"^OK", text, re.MULTILINE) else "FAILED"
    failing = re.findall(r"^(?:FAIL|ERROR): (\S+)", text, re.MULTILINE)
    skipped = re.search(r"skipped=(\d+)", text)
    payload = {
        "evidence_class": "implementer_executed",
        "what": "automated suite against the scripted fake Codex CLI (no model call, no subscription use)",
        "command": "python -B -m unittest discover -s tests -t . -v   (cwd = v2 root, venv python)",
        "source_combined_sha256": fingerprint()["combined_sha256"],
        "environment": environment(),
        "tests_run": int(ran.group(1)) if ran else None,
        "seconds": float(ran.group(2)) if ran else round(time.monotonic() - started, 1),
        "outcome": outcome,
        "skipped": int(skipped.group(1)) if skipped else 0,
        "failing": failing,
        "exit_code": res.returncode,
        "limits": ["fake CLI: event/tool shapes are fixtures, not captured live output",
                   "no live model call; no live sandbox behaviour is tested here",
                   "real-process tests (job object, tree kill, locks) run on this Windows machine only"],
    }
    print(write(f"tests_{label}.json", payload))
    print(f"{outcome}: {payload['tests_run']} tests in {payload['seconds']}s; failing={failing}")
    if outcome != "OK":
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fingerprint")
    sub.add_parser("policy")
    t = sub.add_parser("tests")
    t.add_argument("--label", default="run")
    ns = parser.parse_args()
    {"fingerprint": cmd_fingerprint, "policy": cmd_policy}.get(ns.cmd, lambda: cmd_tests(ns.label))()
