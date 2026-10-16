"""Local administration for v2 state.  Not exposed over MCP.

    bridge_admin.py status     [--repo PATH] [--all] [--json]
    bridge_admin.py new        [--repo PATH] [--session NAME] [--model M ...]
    bridge_admin.py reset      --yes [--repo PATH] [--session NAME] [--model M ...]
    bridge_admin.py quarantine --yes

``reset`` retires the bridge's pointer only.  It never touches Codex history,
the v1 state directory, Codex configuration or authentication.
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
        cli_report["tested_version"] = codexcli.TESTED_CLI_VERSION
        try:
            codexcli.check_compat(version, settings.allow_untested_cli)
            cli_report["compatible"] = True
        except errors.BridgeError as exc:
            cli_report["compatible"] = False
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
        f"(tested {cli.get('tested_version', '?')}, compatible={cli.get('compatible', '?')})"
    )
    out(f"authentication : {cli.get('auth', '?')}")
    if cli.get("api_key_env_vars_scrubbed"):
        out(f"  API-key env vars present and removed from children: {', '.join(cli['api_key_env_vars_scrubbed'])}")
    cap = report.get("capabilities")
    if cap:
        out(f"disabled features    : {', '.join(cap['disabled_features']) or '-'}")
        out(f"disabled MCP servers : {', '.join(cap['disabled_mcp_servers']) or '-'}")
        out(f"allowed MCP servers  : {', '.join(cap['allowed_mcp_servers']) or '-'}")
        out(f"hook sources found   : {', '.join(cap['hook_sources_found']) or '-'}")
        for item in cap["unverified"]:
            out(f"  unverified: {item}")
    sbx = report.get("sandbox_preflight")
    if sbx is not None:
        out(f"sandbox preflight : {'ok' if sbx.get('ok') else 'FAILED (' + str(sbx.get('code')) + ')'}"
            + (f" (skipped: {sbx['skipped']})" if sbx.get("skipped") else ""))
    reg = report.get("registry", {})
    out(f"registry       : {reg.get('path')} (schema {reg.get('schema_version', '?')}, "
        f"retired={reg.get('retired_count', '?')})")
    for entry in reg.get("entries", []):
        out(f"  - model={entry['model']} session={entry['session']} thread={entry['thread_id']} "
            f"turns={entry['turns']} updated={entry['updated_at']}")
    tel = report["telemetry"]
    out(f"telemetry      : {tel['path']} ({tel['bytes']} bytes, {len(tel['rotated_files'])} rotated)")
    if report["problems"]:
        out("PROBLEMS:")
        for text in report["problems"]:
            for line in text.splitlines():
                out("  " + line)
    else:
        out("no problems found")


def cmd_new(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    settings, root, repo_key, registry = _context(args, env)
    session = args.session or settings.session
    if not config._SESSION_RE.match(session):  # noqa: SLF001
        raise errors.BridgeError(errors.INVALID_CONFIGURATION, f"invalid session name {session!r}", code="bad_session")
    registry.ensure_dirs()
    for model in _models(args, env):
        ident = {"repo_key": repo_key, "repo_path": str(root), "model": model, "session": session}
        created = registry.register_new(state.session_key(repo_key, model, session), ident)
        print(f"{'created' if created else 'already exists'}: model={model} session={session}")
    print(
        f"Select it with GPT_ARCHITECT_SESSION={session} in the MCP server's environment. "
        "The Codex thread is created on the first call."
    )
    return 0


def cmd_reset(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if not args.yes:
        print("reset retires the bridge's session pointer (Codex history is kept). Re-run with --yes.")
        return 2
    settings, root, repo_key, registry = _context(args, env)
    session = args.session or settings.session
    rc = 0
    for model in _models(args, env):
        key = state.session_key(repo_key, model, session)
        lock = registry.session_lock(key)
        try:
            lock.acquire(5.0)
        except state.LockTimeout:
            print(f"busy: a call is running on model={model} session={session}; not reset")
            rc = 1
            continue
        try:
            entry = registry.retire(key, "admin reset")
        finally:
            lock.release()
        if entry is None:
            print(f"nothing to reset: model={model} session={session}")
        else:
            thread = entry.get("thread_id")
            print(
                f"retired pointer: model={model} session={session} thread={thread}. "
                "Codex history was not touched"
                + (f" (inspect with `codex resume {thread}`)." if thread else ".")
            )
    return rc


def cmd_quarantine(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if not args.yes:
        print("quarantine renames registry.json (never deletes it). Re-run with --yes.")
        return 2
    settings = config.load_settings(env)
    target = state.Registry(settings.state_dir).quarantine()
    print(f"registry renamed to {target}" if target else "no registry file present")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bridge_admin.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", help="repository root (default: CLAUDE_PROJECT_DIR or cwd)")

    p = sub.add_parser("status", help="show routes, CLI, auth, capabilities and registry")
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

    p = sub.add_parser("quarantine", help="rename a corrupt registry.json out of the way")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_quarantine)
    return parser


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, env if env is not None else os.environ)
    except errors.BridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
