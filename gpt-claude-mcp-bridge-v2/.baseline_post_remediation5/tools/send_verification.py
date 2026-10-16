"""Send the PREPARED verification prompt to GPT via the Codex CLI (human-authorised, one attempt).

    .venv\\Scripts\\python.exe -B tools\\send_verification.py --i-understand-this-uses-subscription-allowance

This is the sender that earlier rounds ran as a one-off script outside the package; it is part of the
reviewed source now (S14) so that its validity predicate is the package's own, not a private copy.

Same method as every earlier round: the v2 candidate's own argv builder, capability policy (read-only
sandbox, approval_policy="never", MCP/plugins/hooks/apps/browser/computer-use/delegation/image/web
controls) and runner, against a neutral disposable repository, with an EMPTY MCP allow-list.  The
prompt is self-contained.  NEVER retried: a failed or timed-out call is reported, not replayed.

It refuses unless the manifest says `prepared_not_sent`, the prompt file matches the manifest's hash
and size, and every evidence record is bound to the current source hash.  A review is called valid
only when `runner.turn_problems` finds nothing (clean exit, completed turn, no error after
completion, stdout read to EOF, no truncated evidence, no infrastructure failure, stable thread
identity) AND an answer exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import codexcli, config, errors, gitsafe, runner  # noqa: E402
from tools import make_verification_prompt as mvp  # noqa: E402

TIMEOUT_S = 2700
OUT = ROOT / "docs" / "gpt_verification"


def _force_rm(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def review_is_valid(result: "runner.RunResult", answer: str) -> tuple[bool, list[str]]:
    """(valid, problems): the single predicate for whether a returned review can be relied on."""
    problems = runner.turn_problems(result)
    if not answer.strip():
        problems.append("no_answer")
    return (not problems), problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--i-understand-this-uses-subscription-allowance", action="store_true", dest="ack")
    ns = parser.parse_args(argv)
    if not ns.ack:
        print("Refusing to run: pass --i-understand-this-uses-subscription-allowance", file=sys.stderr)
        return 2

    manifest = json.loads((OUT / "verification_manifest.json").read_text(encoding="utf-8"))
    raw = (OUT / "verification_prompt.md").read_bytes()
    if manifest.get("status") != "prepared_not_sent":
        print("REFUSING: manifest status is not prepared_not_sent:", manifest.get("status"))
        return 2
    if hashlib.sha256(raw).hexdigest() != manifest["prompt_sha256"] or len(raw) != manifest["prompt_bytes"]:
        print("REFUSING: the prompt file does not match its manifest")
        return 2
    source_sha = mvp.current_source_hash()
    problems = mvp.evidence_problems(ROOT, source_sha)
    if problems or manifest["source_combined_sha256"] != source_sha:
        print("REFUSING: evidence/source mismatch:", problems or "manifest source hash differs")
        return 2
    prompt = raw.decode("utf-8")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    print(f"sending: {len(prompt):,} chars ({len(raw):,} bytes), prompt sha256 {manifest['prompt_sha256'][:16]}..., "
          f"source {source_sha[:16]}...", flush=True)

    env = codexcli.child_env(os.environ)
    cli = codexcli.resolve_cli(None, os.environ)
    version = codexcli.get_version(cli, env)
    codexcli.check_compat(version, False)          # exact-version gate, before any inference
    codexcli.check_auth(cli, env)                  # ChatGPT auth gate, before any inference
    route = config.resolve_route(config.CONSULT, os.environ)
    config.validate_route_against_catalog(route, codexcli.load_catalog(cli, env))

    git = gitsafe.trusted_git(ROOT)
    if not git:
        print("REFUSING: no trusted git executable on PATH (the current directory is never searched)")
        return 2
    genv = gitsafe.safe_git_env(os.environ)

    work = Path(tempfile.mkdtemp(prefix="gpt_verify_"))
    try:
        repo = work / "neutral_repo"
        repo.mkdir()
        for a in (["init", "-q"], ["config", "user.email", "r@example.invalid"], ["config", "user.name", "r"]):
            subprocess.run([git, "-C", str(repo), *a], check=True, capture_output=True, env=genv)
        (repo / "README.md").write_text("neutral working directory for a self-contained review\n", encoding="utf-8")
        subprocess.run([git, "-C", str(repo), "add", "-A"], check=True, capture_output=True, env=genv)
        subprocess.run([git, "-C", str(repo), "-c", "user.email=r@e.i", "-c", "user.name=r", "commit", "-q",
                        "-m", "i"], check=True, capture_output=True, env=genv)

        features = codexcli.load_features(cli, env)
        audit = codexcli.capability_audit(cli, env, repo, frozenset(), features)   # empty allow-list
        answer_file = str(work / "answer.txt")
        built = codexcli.build_argv(cli, model=route.model, effort=route.effort, output_file=answer_file,
                                    audit=audit, resume_thread=None, windows_sandbox=None)
        print("route:", route.model, route.effort, "| cli", version, "| mcp disabled:", audit.disabled_servers,
              flush=True)

        started = time.time()
        result = runner.run_turn(built, prompt, env, str(repo), TIMEOUT_S, threading.Event())
        ev = result.events
        answer_path = Path(answer_file)
        answer = answer_path.read_text(encoding="utf-8", errors="replace") if answer_path.is_file() else ""
        valid, why = review_is_valid(result, answer)
        meta = {
            "sent_at": stamp, "human_authorised": True, "route": route.public(), "codex_cli": version,
            "source_combined_sha256": source_sha, "prompt_sha256": manifest["prompt_sha256"],
            "prompt_bytes": manifest["prompt_bytes"], "prompt_files": len(manifest["files"]),
            "exit_code": result.returncode, "timed_out": result.timed_out, "cancelled": result.cancelled,
            "turn_completed": ev.turn_completed, "thread_id": ev.thread_id,
            "usage": ev.usage, "events_total": ev.total,
            "command_or_tool_items": ev.command_items, "infra_failed_items": ev.infra_failed_items,
            "errors_before_completion": ev.errors_before_completion,
            "errors_after_completion": ev.errors_after_completion,
            "stdout_eof": ev.stdout_eof, "reader_failed": ev.reader_failed,
            "evidence_truncated_items": ev.evidence_truncated_items,
            "process": {"ownership": result.ownership, "termination": result.termination,
                        "reaped_descendants": result.reaped_descendants, "active_after": result.active_after},
            "capability_policy": audit.telemetry_summary(),
            "seconds": round(time.time() - started, 1),
            "valid_review": valid, "invalid_reasons": why, "retried": False,
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"request_{stamp}.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
        if answer.strip():
            tag = "" if valid else " INVALID: " + ", ".join(why) + ";"
            (OUT / f"gpt_verification_{stamp}.md").write_text(
                f"<!--{tag} GPT's verification review, verbatim. Advisory; NOT approval. "
                f"Metadata: request_{stamp}.json -->\n\n" + answer.strip() + "\n", encoding="utf-8")
        print(json.dumps({k: meta[k] for k in ("exit_code", "timed_out", "turn_completed", "thread_id", "usage",
                                              "command_or_tool_items", "infra_failed_items",
                                              "errors_before_completion", "errors_after_completion",
                                              "stdout_eof", "process", "seconds", "valid_review",
                                              "invalid_reasons")}, indent=1), flush=True)
        print("answer chars:", len(answer), "| stamp:", stamp, flush=True)
        if result.stderr_tail.strip():
            print("stderr tail:", result.stderr_tail.strip()[-400:], flush=True)
        return 0 if valid else 1
    finally:
        shutil.rmtree(work, onerror=_force_rm)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except errors.BridgeError as exc:
        print(str(exc))
        sys.exit(2)
