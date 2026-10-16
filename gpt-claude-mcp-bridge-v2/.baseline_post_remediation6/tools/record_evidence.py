"""Bind verification results to the exact code they were produced from (F8).

    python -B tools/record_evidence.py fingerprint
    python -B tools/record_evidence.py policy
    python -B tools/record_evidence.py tests --label remediation_full
    python -B tools/record_evidence.py integrity      # v1 manifest comparison + lock verification output
    python -B tools/record_evidence.py cli [--rollout PATH]   # model-free CLI help/schema observations

Every record carries: the combined SHA-256 of the v2 source tree at the time, the exact
command, Python/OS, the Codex CLI version, the sandbox backend reported by
``codex doctor --json``, and (for `policy`) the effective capability policy read from the
installed CLI.  A result recorded for one source hash is NOT evidence for different code.

A test record is only valid when the tests ran from a STAGED COPY of the source set (R9: a change
to the live tree during the run cannot affect what was tested) whose fingerprint equals the live
tree's fingerprint taken before AND after the run.  The staged files are made READ-ONLY for the run
and the staged copy is fingerprinted AGAIN after the run, before it is deleted (S9): a staged module
that was modified during the run is detected, not assumed unchanged.  Endpoint equality of the live
tree is not proof of its continuous identity; the staged copy is what the tests executed.  What this
still does not stage is the environment: PATH, %TEMP%, the venv, the installed CLI.  A difference
marks the record INVALID and fails the command; the raw test output is retained next to the record
(``tests_<label>.log``, hash in the record).

Evidence classes used in the documentation:
  implementer_executed    produced by the implementer (Claude Code) EXECUTING something (including
                          model-free CLI commands), bound to a hash.  NOT verification by a second
                          party: a reviewer cannot confirm it from the record alone
  source_observation      read from the code or documentation only (nothing executed).  A record
                          that ALSO carries `environment` metadata says so in
                          `environment_evidence_class` (that part was produced by executing CLI
                          commands)
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


def source_files(root: Path = ROOT) -> list[Path]:
    seen: dict[str, Path] = {}
    for pattern in SOURCE_GLOBS:
        for p in sorted(root.glob(pattern)):
            if p.is_file() and "__pycache__" not in p.parts:
                seen[p.relative_to(root).as_posix()] = p
    return [seen[k] for k in sorted(seen)]


def fingerprint(root: Path = ROOT) -> dict:
    files = []
    combined = hashlib.sha256()
    for p in source_files(root):
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        rel = p.relative_to(root).as_posix()
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


def write(name: str, payload: dict, evidence: Path | None = None) -> Path:
    evidence = evidence if evidence is not None else EVIDENCE
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def cmd_fingerprint() -> None:
    fp = fingerprint()
    out = write("source_fingerprint.json", {**fp, "environment": environment(),
                                            "evidence_class": "source_observation",
                                            "evidence_class_scope": "files / combined_sha256: read from the "
                                                                    "source tree (nothing executed)",
                                            "environment_evidence_class": "implementer_executed",
                                            "environment_note": "`environment` was produced by EXECUTING "
                                                                "model-free CLI commands (--version, doctor)"})
    print(f"{out}  combined={fp['combined_sha256']}  files={fp['file_count']}")


def cmd_policy() -> None:
    from bridge import codexcli, gitsafe

    cli = codexcli.resolve_cli(None, os.environ)
    env = codexcli.child_env(os.environ)
    git = gitsafe.trusted_git()
    if git is None:
        raise SystemExit("no trusted git executable on PATH (the current directory is never searched)")
    tmp = Path(tempfile.mkdtemp(prefix="evidence_repo_"))
    try:
        for args in (["init", "-q"], ["config", "user.email", "e@example.invalid"], ["config", "user.name", "e"]):
            subprocess.run([git, "-C", str(tmp), *args], check=True, capture_output=True)
        (tmp / "a.txt").write_text("x", encoding="utf-8")
        subprocess.run([git, "-C", str(tmp), "add", "-A"], check=True, capture_output=True)
        subprocess.run([git, "-C", str(tmp), "commit", "-q", "-m", "i"], check=True, capture_output=True)
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


def run_problems(*, before: str, after: str, exit_code: int, skipped: int, failing: list[str],
                 tests_run: int | None, staged: str | None = None,
                 staged_after: str | None = None) -> list[str]:
    """Why a test run cannot be recorded as passing evidence ([] = valid).  Pure, so it is tested."""
    problems: list[str] = []
    if before != after:
        problems.append("the source tree changed while the tests ran (before/after fingerprints differ)")
    if staged is not None and staged != before:
        problems.append("the staged copy that was executed differs from the live tree's fingerprint")
    if staged is not None and staged_after != staged:
        problems.append("the staged copy was modified (or could not be re-fingerprinted) during the run "
                        "(post-run staged fingerprint differs)")
    if exit_code != 0:
        problems.append(f"the test process exited {exit_code}")
    if skipped:
        problems.append(f"{skipped} test(s) were skipped (the declared policy is zero skips)")
    if failing:
        problems.append(f"{len(failing)} failing test(s)")
    if not tests_run:
        problems.append("no test count was reported")
    return problems


def cmd_tests(label: str, *, root: Path | None = None, evidence: Path | None = None) -> None:
    """Record a test run from a staged, read-only copy of ``root``'s source set.  ``root``/``evidence``
    default to this repository; they are parameters so the recorder itself can be tested (S9)."""
    import shutil

    import stat

    root = root if root is not None else ROOT
    evidence = evidence if evidence is not None else EVIDENCE
    command = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"]
    before = fingerprint(root)["combined_sha256"]
    stage = Path(tempfile.mkdtemp(prefix="stage_tests_"))
    staged_after: str | None = None
    try:
        for src in source_files(root):
            target = stage / src.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            os.chmod(target, stat.S_IREAD)         # S9: read-only for the run
        staged = fingerprint(stage)["combined_sha256"]
        started = time.monotonic()
        res = subprocess.run(command, cwd=str(stage), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        try:                                       # S9: fingerprinted AGAIN before the stage is deleted
            staged_after = fingerprint(stage)["combined_sha256"]
        except OSError:
            staged_after = None
    finally:
        def _force(func, path, _exc):
            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(stage, onerror=_force)
    after = fingerprint(root)["combined_sha256"]
    text = res.stdout + res.stderr
    ran = re.search(r"Ran (\d+) tests? in ([\d.]+)s", text)
    ok_line = bool(re.search(r"^OK", text, re.MULTILINE))
    failing = re.findall(r"^(?:FAIL|ERROR): (\S+)", text, re.MULTILINE)
    skipped_m = re.search(r"skipped=(\d+)", text)
    skipped = int(skipped_m.group(1)) if skipped_m else 0
    tests_run = int(ran.group(1)) if ran else None
    problems = run_problems(before=before, after=after, exit_code=res.returncode, skipped=skipped,
                            failing=failing, tests_run=tests_run, staged=staged, staged_after=staged_after)
    # INVALID = the provenance of the run is broken (live tree changed, stage differs from the live tree,
    # or the stage changed during the run); FAILED = the tests themselves did not pass (T12).
    provenance_broken = before != after or staged != before or staged_after != staged
    outcome = "OK" if (ok_line and not problems) else ("INVALID" if provenance_broken else "FAILED")
    evidence.mkdir(parents=True, exist_ok=True)
    log_name = f"tests_{label}.log"
    log_bytes = text.encode("utf-8", errors="replace")
    (evidence / log_name).write_bytes(log_bytes)
    payload = {
        "evidence_class": "implementer_executed",
        "what": "automated suite against the scripted fake Codex CLI (no model call, no subscription use)",
        "command": "python -B -m unittest discover -s tests -t . -v   (cwd = a staged copy of the source "
                   "set, venv python)",
        "executed_from": "staged_copy",
        "staged_copy_read_only": True,
        "source_combined_sha256": before,
        "source_sha256_staged_copy": staged,
        "source_sha256_staged_copy_after_run": staged_after,
        "source_sha256_after_run": after,
        "environment": environment(),
        "tests_run": tests_run,
        "seconds": float(ran.group(2)) if ran else round(time.monotonic() - started, 1),
        "outcome": outcome,
        "skipped": skipped,
        "failing": failing,
        "exit_code": res.returncode,
        "problems": problems,
        "raw_log": log_name,
        "raw_log_sha256": hashlib.sha256(log_bytes).hexdigest(),
        "raw_log_bytes": len(log_bytes),
        "limits": ["fake CLI: event/tool shapes are fixtures, not captured live output",
                   "no live model call; no live sandbox behaviour is tested here",
                   "real-process tests (job object, tree kill, locks) run on this Windows machine only"],
    }
    print(write(f"tests_{label}.json", payload, evidence))
    print(f"{outcome}: {tests_run} tests in {payload['seconds']}s; failing={failing}; problems={problems}")
    if outcome != "OK":
        sys.exit(1)


def cmd_integrity() -> None:
    """Retain the raw output of the v1/auth integrity comparison and the lock verification (V13:
    the package otherwise only *reported* these)."""
    py = sys.executable
    baseline = ROOT / "docs" / "v1_baseline_before.json"
    compare = subprocess.run([py, "-B", str(ROOT / "tools" / "v1_manifest.py"), "compare", str(baseline)],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=str(ROOT))
    lock = subprocess.run([py, "-B", str(ROOT / "tools" / "verify_lock.py")], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, cwd=str(ROOT))
    payload = {
        "evidence_class": "implementer_executed",
        "what": "raw output of tools/v1_manifest.py compare (v1 + Codex auth/config stat integrity) and "
                "tools/verify_lock.py (installed packages vs requirements.lock.txt)",
        "source_combined_sha256": fingerprint()["combined_sha256"],
        "environment": environment(),
        "v1_manifest_compare": {"exit_code": compare.returncode, "output": (compare.stdout + compare.stderr)[-6000:]},
        "verify_lock": {"exit_code": lock.returncode, "output": (lock.stdout + lock.stderr)[-3000:]},
        "limits": ["size/mtime only: content equality of config.toml/auth.json cannot be shown (never opened)"],
    }
    print(write("integrity.json", payload))


def _rollout_structure(path: Path) -> dict:
    """Structure only (record types and tool names): no prompt, answer or file content."""
    types: dict[str, int] = {}
    tool_names: dict[str, int] = {}
    nested = 0
    rows = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rows += 1
        payload = row.get("payload") if isinstance(row, dict) else None
        if not isinstance(payload, dict):
            continue
        kind = str(payload.get("type"))
        types[kind] = types.get(kind, 0) + 1
        if kind in ("custom_tool_call", "function_call"):
            name = str(payload.get("name"))
            tool_names[name] = tool_names.get(name, 0) + 1
            if name == "apply_patch":
                nested += 1
    return {"rows": rows, "payload_types": types, "tool_call_names": tool_names,
            "records_named_apply_patch": nested}


def normalise_mcp_list(text: str) -> list[dict] | None:
    """Only (name, enabled) of each server, sorted - never transports, commands, args or env (those
    are the user's local configuration).  None when the text is not the expected JSON list."""
    try:
        rows = json.loads(text)
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    out = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            return None
        out.append({"name": row["name"], "enabled": bool(row.get("enabled"))})
    return sorted(out, key=lambda r: r["name"])


def cmd_cli(rollout: str | None) -> None:
    """Model-free observations of the installed CLI that the documentation relies on.

    Retained data is limited to public CLI help/schema text (scrubbed of local identifiers), a
    login *classification* (never the raw status line) and normalised MCP (name, enabled) lists -
    raw ``mcp list`` output contains the user's local runtime paths and environment and is never
    stored."""
    from bridge import codexcli
    from tools.privacy import scrub_local

    cli = codexcli.resolve_cli(None, os.environ)
    env = codexcli.child_env(os.environ)
    asked = [["--version"], ["features", "list"], ["mcp", "--help"], ["mcp", "list", "--help"],
             ["exec", "--help"], ["exec", "resume", "--help"], ["sandbox", "--help"]]
    outputs = {}
    for args in asked:
        res = codexcli.run_simple([*cli.base_argv, *args], env, timeout_s=60)
        outputs[" ".join(args)] = {"exit_code": res.returncode,
                                   "output": scrub_local((res.stdout + res.stderr)[-6000:])}
    login = codexcli.run_simple([*cli.base_argv, "login", "status"], env, timeout_s=60)
    outputs["login status"] = {"exit_code": login.returncode,
                               "classified_as": ("chatgpt" if "chatgpt" in (login.stdout + login.stderr).lower()
                                                 else "other_or_not_logged_in"),
                               "output": "(raw status line deliberately not retained)"}
    # the claim "no deny-all": the same MCP list with and without an empty mcp_servers override
    probe = tempfile.mkdtemp(prefix="evidence_cli_")
    try:
        base = codexcli.run_simple([*cli.base_argv, "mcp", "list", "--json"], env, cwd=probe, timeout_s=60)
        over = codexcli.run_simple([*cli.base_argv, "mcp", "list", "--json", "-c", "mcp_servers={}"], env,
                                   cwd=probe, timeout_s=60)
        iuc = codexcli.run_simple([*cli.base_argv, "mcp", "list", "--json", "--ignore-user-config"], env,
                                  cwd=probe, timeout_s=60)
    finally:
        import shutil

        shutil.rmtree(probe, ignore_errors=True)
    base_view, over_view = normalise_mcp_list(base.stdout), normalise_mcp_list(over.stdout)
    probe_result = {
        "claim": "-c mcp_servers={} has no effect on the effective server list (no supported deny-all)",
        "compared": "sorted (name, enabled) pairs parsed from `codex mcp list --json`; transports, commands "
                    "and environments are deliberately not retained",
        "baseline_exit": base.returncode, "override_exit": over.returncode,
        "baseline_servers": base_view, "override_servers": over_view,
        "same_effective_view": (base_view is not None and base_view == over_view),
        "any_server_enabled_with_override": bool(over_view and any(r["enabled"] for r in over_view)),
        "ignore_user_config_on_mcp_list": {
            "claim": "`--ignore-user-config` (an `exec` flag) cannot be observed through `mcp list`",
            "exit": iuc.returncode,
            "rejected_as_unexpected_argument": "unexpected argument" in (iuc.stdout + iuc.stderr),
        },
    }
    payload = {
        "evidence_class": "implementer_executed",
        "kind": "model_free_cli_observation",
        "what": "model-free CLI help/schema outputs the README relies on (commands were EXECUTED; no "
                "inference, no sandbox preflight)",
        "source_combined_sha256": fingerprint()["combined_sha256"],
        "environment": environment(),
        "commands": outputs,
        "mcp_deny_all_probe": probe_result,
    }
    if rollout:
        payload["rollout_structure"] = {
            "claim": "Codex persists no separate nested apply_patch record (only the exec wrapper call)",
            "note": "historical rollout of an earlier live attack run; structure only, no content",
            **_rollout_structure(Path(rollout)),
        }
    print(write("cli_observations.json", payload))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fingerprint")
    sub.add_parser("policy")
    sub.add_parser("integrity")
    c = sub.add_parser("cli")
    c.add_argument("--rollout", default=None, help="a Codex session rollout JSONL (structure is summarised)")
    t = sub.add_parser("tests")
    t.add_argument("--label", default="run")
    ns = parser.parse_args()
    if ns.cmd == "tests":
        cmd_tests(ns.label)
    elif ns.cmd == "cli":
        cmd_cli(ns.rollout)
    else:
        {"fingerprint": cmd_fingerprint, "policy": cmd_policy, "integrity": cmd_integrity}[ns.cmd]()
