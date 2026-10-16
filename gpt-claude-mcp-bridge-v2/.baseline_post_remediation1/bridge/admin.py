"""Local administration for v2 state.  Not exposed over MCP.

    bridge_admin.py status       [--repo PATH] [--all] [--json] [--sandbox]
    bridge_admin.py new          [--repo PATH] [--session NAME] [--model M ...]
    bridge_admin.py reset        --yes [--repo PATH] [--session NAME] [--model M ...]
    bridge_admin.py adopt        --thread ID --yes [--repo PATH] [--session NAME] [--model M]
    bridge_admin.py reconcile    --yes [--repo PATH] [--session NAME] [--model M ...]
    bridge_admin.py quarantine   --yes        (registry-wide; needs quiescence)
    bridge_admin.py reinit       --yes        (registry-wide; needs quiescence)
    bridge_admin.py restore-prev --yes        (registry-wide; needs quiescence)
    bridge_admin.py end-maintenance --yes

``reset`` retires the bridge's pointer (and a pending-persistence marker) only.  Nothing
here touches Codex history, the v1 state directory, Codex configuration or authentication,
and nothing here ever kills a process.  Locks coordinate cooperating processes that share
this v2 state directory; they do not cover v1 or a manual ``codex resume``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from . import codexcli, config, errors, state, telemetry


def _home(env: Mapping[str, str]) -> Path:
    return Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())


def _models(args: argparse.Namespace, env: Mapping[str, str]) -> list[str]:
    if args.model:
        for model in args.model:
            if not config._MODEL_RE.match(model):  # noqa: SLF001
                raise errors.BridgeError(errors.INVALID_CONFIGURATION, f"invalid model name {model!r}", code="bad_model")
        return sorted(set(args.model))
    return sorted({config.resolve_route(t, env).model for t in config.TOOL_NAMES})


def _context(args: argparse.Namespace, env: Mapping[str, str]):
    settings = config.load_settings(env)
    raw = args.repo or env.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    root, repo_key = state.canonical_repo(raw)
    state.validate_state_dir(settings.state_dir, root, _home(env))
    return settings, root, repo_key, state.Registry(settings.state_dir)


def _state_only(env: Mapping[str, str]):
    settings = config.load_settings(env)
    return settings, state.Registry(settings.state_dir)


def _require_quiescent(registry: state.Registry, reason: str) -> None:
    """Block new calls (maintenance flag), then require that no call holds a session lock."""
    registry.ensure_dirs()
    registry.begin_maintenance(reason)
    held = registry.active_sessions()
    if held:
        registry.end_maintenance()
        raise errors.BridgeError(
            errors.STATE_ERROR,
            f"Not quiescent: {len(held)} session(s) have a call in progress.",
            code="not_quiescent",
            recovery="Wait for in-flight calls to finish (or cancel them), then retry. Nothing was changed.",
        )


# --------------------------------------------------------------------------- status


def cmd_status(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    report: dict[str, Any] = {"ok": True, "problems": []}

    def problem(text: str) -> None:
        report["ok"] = False
        report["problems"].append(text)

    settings = config.load_settings(env)
    home = _home(env)
    report["state_dir"] = str(settings.state_dir)
    report["v1_state_dir"] = str(home / config.V1_STATE_DIRNAME)
    report["session_name"] = settings.session
    report["windows_sandbox_override"] = settings.windows_sandbox
    report["timeouts_s"] = {"run": settings.timeout_s, "queue": settings.queue_timeout_s}
    report["on_turn_infra_failure"] = settings.on_infra_failure

    routes = {t: config.resolve_route(t, env) for t in config.TOOL_NAMES}
    report["routes"] = {t: r.public() for t, r in routes.items()}

    root = repo_key = None
    try:
        raw = args.repo or env.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        root, repo_key = state.canonical_repo(raw)
        state.validate_state_dir(settings.state_dir, root, home)
        report["repo"] = {"path": str(root), "key": repo_key}
    except errors.BridgeError as exc:
        problem(str(exc))

    cli_report: dict[str, Any] = {"api_key_env_vars_scrubbed": codexcli.api_key_vars_present(env)}
    report["codex_cli"] = cli_report
    try:
        child = codexcli.child_env(env)
        cli = codexcli.resolve_cli(settings.codex_path_override, env)
        cli_report.update({"kind": cli.kind, "launcher": cli.display})
        version = codexcli.get_version(cli, child)
        cli_report["version"] = version
        cli_report["supported_versions"] = list(codexcli.SUPPORTED_CLI_VERSIONS)
        try:
            warning = codexcli.check_compat(version, settings.allow_untested_cli)
            cli_report["supported"] = warning is None
            cli_report["untested_allowed"] = warning is not None
        except errors.BridgeError as exc:
            cli_report["supported"] = False
            problem(str(exc))
        try:
            codexcli.check_auth(cli, child)
            cli_report["auth"] = "chatgpt"
        except errors.BridgeError as exc:
            cli_report["auth"] = exc.code
            problem(str(exc))
        catalog = codexcli.load_catalog(cli, child)
        for tool, route in routes.items():
            try:
                config.validate_route_against_catalog(route, catalog)
            except errors.BridgeError as exc:
                problem(f"{tool}: {exc}")
        if root is not None:
            features = codexcli.load_features(cli, child)
            audit = codexcli.capability_audit(cli, child, root, settings.mcp_allow, features)
            report["capabilities"] = audit.public()
            if args.sandbox:
                try:
                    report["sandbox_preflight"] = codexcli.sandbox_preflight(
                        cli, child, root, audit, settings.windows_sandbox
                    )
                except errors.BridgeError as exc:
                    report["sandbox_preflight"] = {"ok": False, "code": exc.code}
                    problem(str(exc))
    except errors.BridgeError as exc:
        problem(str(exc))

    registry = state.Registry(settings.state_dir)
    inst = registry.read_instance()
    report["instance"] = ({"id_prefix": inst["instance_id"][:8], "registry_established": bool(inst.get("registry_established"))}
                          if inst else None)
    maintenance = registry.maintenance()
    report["maintenance"] = maintenance
    if maintenance is not None:
        problem(f"maintenance flag present: {maintenance.get('reason')}")
    try:
        snap = registry.snapshot()
        entries = []
        for key, entry in sorted(snap["sessions"].items()):
            if not args.all and repo_key and not key.startswith(repo_key + ":"):
                continue
            entries.append({"key": key, **{k: entry.get(k) for k in
                            ("model", "session", "thread_id", "turns", "updated_at", "cli_version")}})
        report["registry"] = {
            "path": str(registry.path),
            "schema_version": snap["schema_version"],
            "entries": entries,
            "retired_count": len(snap.get("retired", [])),
        }
    except errors.BridgeError as exc:
        problem(str(exc))
        report["registry"] = {"path": str(registry.path), "error": exc.code}

    report["unreconciled"] = {
        "inflight": [{k: m.get(k) for k in ("file", "state", "thread_id", "ownership", "call_id", "since")}
                     for m in registry.list_markers("inflight")],
        "pending": [{k: m.get(k) for k in ("file", "thread_id", "reason", "since")}
                    for m in registry.list_markers("pending")],
    }
    for m in report["unreconciled"]["inflight"]:
        problem(f"in-flight marker {m['file']}: state={m['state']} thread={m['thread_id']}")
    for m in report["unreconciled"]["pending"]:
        problem(f"pending persistence {m['file']}: thread={m['thread_id']} ({m['reason']})")
    report["journal_tail"] = registry.journal.read()[-5:]

    tel = telemetry.Telemetry(settings.state_dir)
    report["telemetry"] = {
        "path": str(tel.path),
        "bytes": tel.path.stat().st_size if tel.path.exists() else 0,
        "rotated_files": sorted(p.name for p in tel.dir.glob("calls.*.jsonl")) if tel.dir.exists() else [],
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0 if report["ok"] else 1


def _print_human(report: Mapping[str, Any]) -> None:
    out = print
    out(f"state dir      : {report['state_dir']}  (v1 dir, untouched: {report['v1_state_dir']})")
    out(f"session name   : {report['session_name']}")
    out(f"windows sandbox: {report['windows_sandbox_override'] or '(Codex config decides)'}")
    out(f"timeouts (s)   : run={report['timeouts_s']['run']} queue={report['timeouts_s']['queue']}")
    out(f"on turn infra failure : {report['on_turn_infra_failure']}")
    for tool, route in report["routes"].items():
        out(
            f"route {tool:<22}: model={route['model']} [{route['model_source']}] "
            f"effort={route['effort']} [{route['effort_source']}]"
        )
    if "repo" in report:
        out(f"repository     : {report['repo']['path']}  key={report['repo']['key']}")
    cli = report["codex_cli"]
    out(
        f"codex cli      : {cli.get('version', '?')} via {cli.get('kind', '?')} "
        f"(supported: {', '.join(cli.get('supported_versions', []))}; this one supported={cli.get('supported', '?')})"
    )
    out(f"authentication : {cli.get('auth', '?')}")
    if cli.get("api_key_env_vars_scrubbed"):
        out(f"  API-key env vars present and removed from children: {', '.join(cli['api_key_env_vars_scrubbed'])}")
    cap = report.get("capabilities")
    if cap:
        out(f"disabled features    : {', '.join(cap['disabled_features']) or '-'}")
        out(f"disabled MCP servers : {', '.join(cap['disabled_mcp_servers']) or '-'}")
        out(f"ALLOWED MCP servers  : {', '.join(cap['allowed_mcp_servers']) or '-'}")
        out(f"hook sources found   : {', '.join(cap['hook_sources_found']) or '-'}")
        out("capability evidence (resolved = CLI effective-config readout; requested = no readout; "
            "uncontained = no control):")
        for c in cap["capabilities"]:
            out(f"  [{c['level']:<11}] {c['name']:<30} {c['control']}")
            if c.get("note"):
                out(f"                 {c['note']}")
    sbx = report.get("sandbox_preflight")
    if sbx is not None:
        out(f"sandbox preflight : {'ok' if sbx.get('ok') else 'FAILED (' + str(sbx.get('code')) + ')'}"
            + (f" (skipped: {sbx['skipped']})" if sbx.get("skipped") else "")
            + (f" source_read={sbx['source_read']}" if sbx.get("source_read") else "")
            + "  [readiness only; not proof about the next turn]")
    inst = report.get("instance")
    out(f"instance       : {'none yet (first initialisation pending)' if not inst else inst}")
    reg = report.get("registry", {})
    out(f"registry       : {reg.get('path')} (schema {reg.get('schema_version', '?')}, "
        f"retired={reg.get('retired_count', '?')})")
    for entry in reg.get("entries", []):
        out(f"  - model={entry['model']} session={entry['session']} thread={entry['thread_id']} "
            f"turns={entry['turns']} updated={entry['updated_at']}")
    unrec = report.get("unreconciled", {})
    for m in unrec.get("inflight", []):
        out(f"  ! in-flight/uncertain: {m}")
    for m in unrec.get("pending", []):
        out(f"  ! pending persistence: {m}")
    tel = report["telemetry"]
    out(f"telemetry      : {tel['path']} ({tel['bytes']} bytes, {len(tel['rotated_files'])} rotated)")
    if report["problems"]:
        out("PROBLEMS:")
        for text in report["problems"]:
            for line in text.splitlines():
                out("  " + line)
    else:
        out("no problems found")


# --------------------------------------------------------------------------- session commands


def _sessions(args, env):
    settings, root, repo_key, registry = _context(args, env)
    session = args.session or settings.session
    if not config._SESSION_RE.match(session):  # noqa: SLF001
        raise errors.BridgeError(errors.INVALID_CONFIGURATION, f"invalid session name {session!r}", code="bad_session")
    return settings, root, repo_key, registry, session


def cmd_new(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    settings, root, repo_key, registry, session = _sessions(args, env)
    registry.ensure_dirs()
    registry.assert_not_in_maintenance()
    for model in _models(args, env):
        ident = {"repo_key": repo_key, "repo_path": str(root), "model": model, "session": session}
        created = registry.register_new(state.session_key(repo_key, model, session), ident)
        print(f"{'created' if created else 'already exists'}: model={model} session={session}")
    print(
        f"Select it with GPT_ARCHITECT_SESSION={session} in the MCP server's environment. "
        "The Codex thread is created on the first call."
    )
    return 0


def _with_session_lock(registry, key, timeout=5.0):
    lock = registry.session_lock(key)
    try:
        lock.acquire(timeout)
    except state.LockTimeout:
        return None
    return lock


def cmd_reset(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if not args.yes:
        print("reset retires the bridge's session pointer and clears a pending-persistence marker "
              "(Codex history is kept). Re-run with --yes.")
        return 2
    settings, root, repo_key, registry, session = _sessions(args, env)
    registry.ensure_dirs()
    rc = 0
    for model in _models(args, env):
        key = state.session_key(repo_key, model, session)
        lock = _with_session_lock(registry, key)
        if lock is None:
            print(f"busy: a call is running on model={model} session={session}; not reset")
            rc = 1
            continue
        try:
            entry = registry.retire(key, "admin reset")
            pending = registry.read_marker("pending", key)
            if pending is not None:
                registry.clear_marker("pending", key)
                registry.journal.append("pending_discarded", key_hash=state.key_hash(key),
                                        thread_id=pending.get("thread_id"))
        finally:
            lock.release()
        if entry is None and pending is None:
            print(f"nothing to reset: model={model} session={session}")
        else:
            thread = (entry or {}).get("thread_id")
            print(
                f"retired pointer: model={model} session={session} thread={thread}"
                + (f"; discarded pending marker for thread {pending.get('thread_id')}" if pending else "")
                + ". Codex history was not touched"
                + (f" (inspect with `codex resume {thread}`)." if thread else ".")
            )
    return rc


def cmd_adopt(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if not args.yes:
        print("adopt points the bridge pointer at an existing Codex thread. Re-run with --yes.")
        return 2
    settings, root, repo_key, registry, session = _sessions(args, env)
    registry.ensure_dirs()
    models = _models(args, env)
    if len(models) != 1:
        raise errors.BridgeError(errors.INVALID_CONFIGURATION,
                                 "adopt needs exactly one model: pass --model", code="adopt_needs_model")
    model = models[0]
    key = state.session_key(repo_key, model, session)
    lock = _with_session_lock(registry, key)
    if lock is None:
        print(f"busy: a call is running on model={model} session={session}; not adopted")
        return 1
    try:
        ident = {"repo_key": repo_key, "repo_path": str(root), "model": model, "session": session}
        entry = registry.adopt_thread(key, ident, args.thread)
        registry.clear_marker("pending", key)
        registry.journal.append("thread_adopted", key_hash=state.key_hash(key), thread_id=args.thread)
    finally:
        lock.release()
    print(f"adopted: model={model} session={session} thread={entry['thread_id']} "
          "(Codex history was not touched; the bridge did not verify the thread exists).")
    return 0


def cmd_reconcile(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if not args.yes:
        print("reconcile clears an in-flight/uncertain marker after YOU have verified that no process of "
              "that call remains. The bridge never kills processes. Re-run with --yes.")
        return 2
    settings, root, repo_key, registry, session = _sessions(args, env)
    registry.ensure_dirs()
    rc = 0
    for model in _models(args, env):
        key = state.session_key(repo_key, model, session)
        lock = _with_session_lock(registry, key)
        if lock is None:
            print(f"busy: a call is running on model={model} session={session}; not reconciled")
            rc = 1
            continue
        try:
            marker = registry.read_marker("inflight", key)
            if marker is None:
                print(f"nothing to reconcile: model={model} session={session}")
                continue
            registry.clear_marker("inflight", key)
            registry.journal.append("operator_reconciled", key_hash=state.key_hash(key),
                                    call_id=marker.get("call_id"), thread_id=marker.get("thread_id"),
                                    previous_state=marker.get("state"), ownership=marker.get("ownership"))
            print(f"reconciled: model={model} session={session} thread={marker.get('thread_id')} "
                  f"(was {marker.get('state')}, ownership {marker.get('ownership')}). No process was touched.")
        finally:
            lock.release()
    return rc


# --------------------------------------------------------------------------- registry-wide commands


def cmd_quarantine(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if not args.yes:
        print("quarantine renames registry.json (never deletes it) and requires no call in progress. "
              "Re-run with --yes.")
        return 2
    settings, registry = _state_only(env)
    _require_quiescent(registry, "registry quarantine")
    try:
        target = registry.quarantine()
        if target:
            registry.journal.append("registry_quarantined", file=target.name)
    finally:
        registry.end_maintenance()
    inst = registry.read_instance()
    print(f"registry renamed to {target}" if target else "no registry file present")
    if target and inst and inst.get("registry_established"):
        print("The registry is now MISSING on an established state directory: calls will refuse "
              "(registry_lost) until you run `reinit --yes` (explicit empty registry) or "
              "`restore-prev --yes`. Existing threads can be re-attached with `adopt`.")
    return 0


def cmd_reinit(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if not args.yes:
        print("reinit creates an explicit EMPTY registry (existing threads stay in Codex history and must be "
              "re-attached with `adopt`). Requires no registry file and no call in progress. Re-run with --yes.")
        return 2
    settings, registry = _state_only(env)
    _require_quiescent(registry, "registry reinit")
    try:
        registry.reinit()
        registry.journal.append("registry_reinitialised")
    finally:
        registry.end_maintenance()
    print("empty registry created (instance identity preserved).")
    return 0


def cmd_restore_prev(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if not args.yes:
        print("restore-prev copies registry.prev.json back. Requires the registry to be absent "
              "(quarantine first) and no call in progress. Re-run with --yes.")
        return 2
    settings, registry = _state_only(env)
    _require_quiescent(registry, "registry restore-prev")
    try:
        if registry.path.exists():
            raise errors.BridgeError(errors.STATE_ERROR, "A registry file exists; quarantine it first.",
                                     code="registry_exists")
        ok = registry.restore_prev()
        if ok:
            registry.journal.append("registry_restored_prev")
    finally:
        registry.end_maintenance()
    print("restored registry.prev.json" if ok else "no valid registry.prev.json to restore")
    return 0 if ok else 1


def cmd_end_maintenance(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if not args.yes:
        print("end-maintenance removes a stale maintenance flag. Re-run with --yes.")
        return 2
    settings, registry = _state_only(env)
    registry.end_maintenance()
    print("maintenance flag cleared")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bridge_admin.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", help="repository root (default: CLAUDE_PROJECT_DIR or cwd)")

    p = sub.add_parser("status", help="show routes, CLI, auth, capability evidence and registry")
    common(p)
    p.add_argument("--all", action="store_true", help="list sessions for every repository")
    p.add_argument("--json", action="store_true")
    p.add_argument("--sandbox", action="store_true",
                   help="also run the model-free sandbox preflight in the repository (may grant the "
                        "sandbox users read ACEs on it, exactly as a real call would)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("new", help="create an empty named session pointer")
    common(p)
    p.add_argument("--session")
    p.add_argument("--model", action="append")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("reset", help="retire a session pointer (keeps Codex history)")
    common(p)
    p.add_argument("--session")
    p.add_argument("--model", action="append")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("adopt", help="point a session at an existing Codex thread (pending/lost recovery)")
    common(p)
    p.add_argument("--session")
    p.add_argument("--model", action="append")
    p.add_argument("--thread", required=True)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_adopt)

    p = sub.add_parser("reconcile", help="clear an uncertain in-flight marker after operator verification")
    common(p)
    p.add_argument("--session")
    p.add_argument("--model", action="append")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_reconcile)

    for name, func, helptext in (
        ("quarantine", cmd_quarantine, "rename registry.json out of the way (needs quiescence)"),
        ("reinit", cmd_reinit, "create an explicit empty registry (needs quiescence)"),
        ("restore-prev", cmd_restore_prev, "restore registry.prev.json (needs quiescence)"),
        ("end-maintenance", cmd_end_maintenance, "clear a stale maintenance flag"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--yes", action="store_true")
        p.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, env if env is not None else os.environ)
    except errors.BridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
