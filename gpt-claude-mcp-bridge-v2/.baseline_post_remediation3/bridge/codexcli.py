"""Everything that talks to the installed Codex CLI, short of running a turn.

All subprocesses are started from argument arrays (never ``shell=True``), with
stdin closed or piped, stdout/stderr captured and bounded, and a hard timeout
that kills the whole process tree.  No function here logs in, logs out, or
touches credentials: authentication is checked only through
``codex login status`` and only its *classification* is kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence

from . import config, errors, execpath, gitsafe
from .redact import redact

TESTED_CLI_VERSION = "0.162.1"
#: Versions with evidence (fixtures + recorded live runs).  An *exact* match is required: a
#: sibling patch release is not presumed equivalent (its flags, event shapes, sandbox
#: helpers and feature set may differ).  Anything else needs an explicit human opt-in.
SUPPORTED_CLI_VERSIONS = ("0.162.1",)

#: env vars that would route Codex to API-key billing; removed from every child.
API_KEY_ENV_VARS = ("CODEX_API_KEY", "OPENAI_API_KEY")

#: Feature gates the bridge turns off on every call.  A gate in REQUIRED must exist in the
#: installed CLI (otherwise the call fails closed: "capability cannot be controlled").
REQUIRED_FEATURE_GATES = (
    "hooks",
    "plugins",
    "apps",
    "browser_use",
    "in_app_browser",
    "computer_use",
    "multi_agent",       # built-in delegation (spawn_agent, collaboration.*)
    "image_generation",  # built-in image generation
)
OPTIONAL_FEATURE_GATES = ("browser_use_external", "browser_use_full_cdp_access", "multi_agent_v2")
#: documented key in the CLI's own config schema (Config.web_search: disabled|cached|indexed|live).
WEB_SEARCH_OVERRIDE = ("-c", 'web_search="disabled"')

_SIMPLE_TIMEOUT_S = 45
_SIMPLE_MAX_BYTES = 4 * 1024 * 1024
_SAFE_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ----------------------------------------------------------------- process helpers


def child_env(base: Mapping[str, str]) -> dict[str, str]:
    """Environment for Codex children: the user's own, minus API-key routes."""
    return {k: v for k, v in base.items() if k.upper() not in API_KEY_ENV_VARS}


def api_key_vars_present(base: Mapping[str, str]) -> list[str]:
    return sorted(k for k in base if k.upper() in API_KEY_ENV_VARS)


def popen_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen[bytes], grace_s: float = 5.0) -> bool:
    """Terminate ``proc`` and all descendants.  Returns True when it is gone."""
    if proc.poll() is not None and sys.platform != "win32":
        return True
    try:
        if sys.platform == "win32":
            subprocess.run(
                [execpath.system_tool("taskkill"), "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
        else:
            import signal

            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        return False
    return True


def close_quietly(stream: Any, reader: threading.Thread | None = None) -> None:
    """Close a pipe once its reader is done (never block on a still-reading thread)."""
    if stream is None or (reader is not None and reader.is_alive()):
        return
    try:
        stream.close()
    except Exception:  # noqa: BLE001
        pass


@dataclass
class Simple:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def run_simple(argv: Sequence[str], env: Mapping[str, str], cwd: str | None = None,
               timeout_s: float = _SIMPLE_TIMEOUT_S) -> Simple:
    """Run a short, non-inference Codex subcommand with bounded capture."""
    proc = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=dict(env),
        **popen_kwargs(),
    )
    buffers: dict[str, bytearray] = {"out": bytearray(), "err": bytearray()}

    def drain(stream: Any, name: str) -> None:
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    return
                if len(buffers[name]) < _SIMPLE_MAX_BYTES:
                    buffers[name].extend(chunk[: _SIMPLE_MAX_BYTES - len(buffers[name])])
        except Exception:  # noqa: BLE001
            return

    threads = [
        threading.Thread(target=drain, args=(proc.stdout, "out"), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, "err"), daemon=True),
    ]
    for t in threads:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
    for t in threads:
        t.join(timeout=5)
    for stream, t in zip((proc.stdout, proc.stderr), threads):
        close_quietly(stream, t)
    return Simple(
        proc.returncode if proc.returncode is not None else -1,
        buffers["out"].decode("utf-8", errors="replace"),
        buffers["err"].decode("utf-8", errors="replace"),
        timed_out,
    )


# ----------------------------------------------------------------- CLI resolution


@dataclass(frozen=True)
class Cli:
    base_argv: tuple[str, ...]
    kind: str  # "node-script" | "native" | "shim" | "python-script"
    display: str


def resolve_cli(override: str | None = None, env: Mapping[str, str] | None = None) -> Cli:
    env = env if env is not None else os.environ
    if override:
        path = Path(override)
        if not path.is_file():
            raise errors.BridgeError(
                errors.CLI_MISSING,
                f"GPT_ARCHITECT_CODEX_PATH points at a missing file: {override}",
                code="override_missing",
                recovery="Fix or unset GPT_ARCHITECT_CODEX_PATH.",
            )
        if path.suffix.lower() == ".py":
            return Cli((sys.executable, str(path)), "python-script", str(path))
        found: str | None = str(path)
    else:
        # V6: never the current directory (the reviewed project) or anything inside it
        found = execpath.trusted_which(("codex", "codex.cmd"), env)
    if not found:
        shadowed = execpath.excluded_matches(("codex", "codex.cmd"), env)
        raise errors.BridgeError(
            errors.CLI_MISSING,
            "Codex CLI not found on PATH."
            + (f" A match exists only inside the project/current directory ({shadowed[0]}), which is never "
               "trusted for execution; set GPT_ARCHITECT_CODEX_PATH to that file if you intend to use it."
               if shadowed else ""),
            code="not_on_path",
            recovery=(
                "Install it (`npm install -g @openai/codex`), sign in with ChatGPT yourself, and make "
                "sure the PATH inherited by Claude's MCP server contains it. The bridge never installs or "
                "logs in."
            ),
        )
    suffix = Path(found).suffix.lower()
    if suffix in (".cmd", ".bat"):
        # npm shim -> run `node <pkg>/bin/codex.js` directly to avoid cmd.exe re-parsing arguments.
        shim_dir = Path(found).parent
        script = shim_dir / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = shim_dir / "node.exe"
        node_path = str(node) if node.is_file() else execpath.trusted_which(("node",), env)
        if script.is_file() and node_path:
            return Cli((node_path, str(script)), "node-script", f"{node_path} {script}")
        return Cli((found,), "shim", found)
    return Cli((found,), "native", found)


def _text(res: Simple) -> str:
    return (res.stdout + "\n" + res.stderr).strip()


def get_version(cli: Cli, env: Mapping[str, str]) -> str:
    res = run_simple([*cli.base_argv, "--version"], env)
    match = re.search(r"(\d+)\.(\d+)\.(\d+)(?:[-+][\w.]+)?", res.stdout + res.stderr)
    if res.returncode != 0 or not match:
        raise errors.BridgeError(
            errors.CLI_MISSING,
            "`codex --version` failed or printed an unrecognised version.",
            code="version_unreadable",
            recovery="Run `codex --version` in a terminal and repair the installation.",
        )
    return match.group(0)


def check_compat(version: str, allow_untested: bool) -> str | None:
    """Exact-version gate.  Returns a warning code when an untested CLI was explicitly allowed."""
    if version in SUPPORTED_CLI_VERSIONS:
        return None
    if allow_untested:
        return f"untested_cli_allowed:{version}"
    raise errors.BridgeError(
        errors.CLI_INCOMPATIBLE,
        f"Codex CLI {version} has no test evidence; supported: {', '.join(SUPPORTED_CLI_VERSIONS)}. "
        "Flags, event shapes, sandbox helpers and capability gates may differ between releases.",
        code="untested_cli",
        recovery=(
            "Re-run the fixture tests and the opt-in live checks against this exact version, then "
            "set GPT_ARCHITECT_ALLOW_UNTESTED_CLI=1 (recorded in telemetry and in every answer). "
            "The bridge does not upgrade or downgrade the CLI."
        ),
    )


# ----------------------------------------------------------------- authentication


def classify_auth(returncode: int, text: str) -> str:
    """Return 'chatgpt' | 'api_key' | 'missing' | 'unsupported'."""
    lowered = text.lower()
    if "not logged in" in lowered or "logged out" in lowered:
        return "missing"
    if "api key" in lowered or "api-key" in lowered or "apikey" in lowered:
        return "api_key"
    if returncode != 0:
        return "unsupported"
    if "chatgpt" in lowered:
        return "chatgpt"
    return "unsupported"


def check_auth(cli: Cli, env: Mapping[str, str]) -> None:
    res = run_simple([*cli.base_argv, "login", "status"], env)
    kind = classify_auth(res.returncode, _text(res))
    if res.timed_out:
        kind = "unsupported"
    if kind == "chatgpt":
        return
    messages = {
        "missing": (
            "auth_missing",
            "Codex reports no stored login.",
            "Run `codex login` yourself and choose ChatGPT sign-in. The bridge never logs in.",
        ),
        "api_key": (
            "auth_api_key",
            "Codex is authenticated with an API key, which would bill the API instead of your "
            "ChatGPT subscription allowance.",
            "Run `codex logout` then `codex login` (ChatGPT) yourself. The bridge refuses to fall back "
            "to API billing and never changes authentication.",
        ),
        "unsupported": (
            "auth_unsupported",
            f"`codex login status` returned an unrecognised or unsupported state "
            f"({len(_text(res))} chars of output not shown).",
            "Run `codex login status` in a terminal; ChatGPT sign-in is required.",
        ),
    }
    code, message, recovery = messages[kind]
    raise errors.BridgeError(errors.AUTHENTICATION, message, code=code, recovery=recovery)


# ----------------------------------------------------------------- catalog / features


def load_catalog(cli: Cli, env: Mapping[str, str]) -> dict[str, list[str]]:
    res = run_simple([*cli.base_argv, "debug", "models"], env)
    try:
        data = json.loads(res.stdout)
        models = data["models"] if isinstance(data, dict) else data
        catalog: dict[str, list[str]] = {}
        for item in models:
            levels = []
            for level in item.get("supported_reasoning_levels") or []:
                levels.append(level["effort"] if isinstance(level, dict) else str(level))
            catalog[str(item["slug"])] = levels
        if res.returncode != 0 or not catalog:
            raise ValueError("empty")
        return catalog
    except Exception:  # noqa: BLE001
        raise errors.BridgeError(
            errors.CLI_INCOMPATIBLE,
            "Could not read the model catalog from `codex debug models`; cannot validate the route.",
            code="catalog_unavailable",
            recovery="Run `codex debug models` in a terminal. No inference was attempted.",
        ) from None


_FEATURE_LINE = re.compile(
    r"^(?P<name>[a-z0-9_.]+)\s+(?P<stage>stable|experimental|under development|deprecated|removed)"
    r"\s+(?P<state>true|false)\s*$"
)


def parse_features(stdout: str) -> dict[str, bool]:
    """Strict parse of `codex features list`.  Any unrecognised line fails closed: the
    bridge refuses to reason about capability gates it cannot read unambiguously."""
    out: dict[str, bool] = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        match = _FEATURE_LINE.match(line.strip())
        if not match:
            raise errors.BridgeError(
                errors.CAPABILITY_AUDIT,
                "`codex features list` produced a line this bridge cannot parse "
                f"({len(line)} chars); the capability schema is not the one it was tested against.",
                code="features_schema_incompatible",
                recovery="Do not use this CLI build with the bridge until it is reviewed. "
                         "No inference was attempted.",
            )
        if match["name"] in out:
            raise errors.BridgeError(
                errors.CAPABILITY_AUDIT,
                f"`codex features list` lists {match['name']!r} more than once; which row wins is "
                "ambiguous, so the gate state cannot be trusted.",
                code="features_schema_incompatible",
                recovery="Do not use this CLI build with the bridge until it is reviewed. "
                         "No inference was attempted.",
            )
        out[match["name"]] = match["state"] == "true"
    return out


def load_features(cli: Cli, env: Mapping[str, str], extra: Sequence[str] = (),
                  cwd: str | None = None) -> dict[str, bool]:
    """Effective feature state *after* applying ``extra`` (--disable / -c) overrides."""
    res = run_simple([*cli.base_argv, "features", "list", *extra], env, cwd=cwd)
    if res.returncode != 0 or not res.stdout.strip():
        raise errors.BridgeError(
            errors.CLI_INCOMPATIBLE,
            "Could not read `codex features list`; cannot build a verified capability policy.",
            code="features_unavailable",
            recovery="Run `codex features list` in a terminal. No inference was attempted.",
        )
    features = parse_features(res.stdout)
    if not features:
        raise errors.BridgeError(errors.CAPABILITY_AUDIT, "`codex features list` returned no features.",
                                 code="features_schema_incompatible")
    return features


# ----------------------------------------------------------------- capability audit

#: evidence levels, strongest first
LEVEL_RESOLVED = "resolved"        # the CLI's own effective-config readout shows the control applied
LEVEL_REQUESTED = "requested"      # passed to the CLI; no readout exists to confirm it
LEVEL_UNCONTAINED = "uncontained"  # no control exists / none used


@dataclass
class Audit:
    disable_features: list[str] = field(default_factory=list)
    server_overrides: list[str] = field(default_factory=list)  # ["-c", "mcp_servers.x.enabled=false", ...]
    config_overrides: list[str] = field(default_factory=list)  # ["-c", 'web_search="disabled"']
    disabled_servers: list[str] = field(default_factory=list)
    allowed_servers: list[str] = field(default_factory=list)
    hook_sources: list[str] = field(default_factory=list)
    project_config: list[str] = field(default_factory=list)
    capabilities: list[dict[str, str]] = field(default_factory=list)
    allow: frozenset[str] = frozenset()

    def extra_argv(self) -> list[str]:
        out: list[str] = []
        for name in self.disable_features:
            out += ["--disable", name]
        out += self.config_overrides
        out += self.server_overrides
        return out

    def feature_args(self) -> list[str]:
        out: list[str] = []
        for name in self.disable_features:
            out += ["--disable", name]
        return out

    def by_level(self, level: str) -> list[str]:
        return [c["name"] for c in self.capabilities if c["level"] == level]

    def note_allowed_enabled(self, names: Sequence[str]) -> list[str]:
        """Record allow-listed servers first seen enabled after the audit (V10); returns the new ones."""
        new = sorted(set(names) - set(self.allowed_servers))
        if new:
            self.allowed_servers = sorted(set(self.allowed_servers) | set(new))
            self.capabilities = [c for c in self.capabilities if c["name"] != "mcp_allowed_servers"]
            self.capabilities.append({
                "name": "mcp_allowed_servers", "control": "GPT_ARCHITECT_MCP_ALLOW (human exception)",
                "level": LEVEL_UNCONTAINED,
                "note": "allowed servers are NOT constrained by the read-only sandbox: "
                        + ", ".join(self.allowed_servers),
            })
        return new

    def telemetry_summary(self) -> dict[str, Any]:
        """Compact, content-free summary for per-call telemetry."""
        return {
            "resolved": self.by_level(LEVEL_RESOLVED),
            "requested": self.by_level(LEVEL_REQUESTED),
            "uncontained": self.by_level(LEVEL_UNCONTAINED),
            "mcp_disabled": len(self.disabled_servers),
            "mcp_allowed": list(self.allowed_servers),
        }

    def public(self) -> dict[str, Any]:
        return {
            "disabled_features": self.disable_features,
            "disabled_mcp_servers": self.disabled_servers,
            "allowed_mcp_servers": self.allowed_servers,
            "hook_sources_found": self.hook_sources,
            "project_config_found": self.project_config,
            "capabilities": self.capabilities,
        }


def _list_mcp(cli: Cli, env: Mapping[str, str], cwd: str, extra: Sequence[str]) -> list[dict[str, Any]]:
    res = run_simple([*cli.base_argv, "mcp", "list", "--json", *extra], env, cwd=cwd)

    def bad(code: str, why: str) -> errors.BridgeError:
        return errors.BridgeError(
            errors.CAPABILITY_AUDIT,
            f"Could not read the effective MCP server list (`codex mcp list --json`): {why}.",
            code=code,
            recovery="Run `codex mcp list --json` in the repository; no inference was attempted.",
        )

    try:
        data = json.loads(res.stdout)
    except Exception:  # noqa: BLE001
        raise bad("mcp_list_unavailable", "output is not JSON") from None
    if res.returncode != 0 or not isinstance(data, list):
        raise bad("mcp_list_unavailable", f"exit {res.returncode}")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data:
        # strict schema: a row without a boolean `enabled` or a string `name` fails closed
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]
                or not isinstance(item.get("enabled"), bool)):
            raise bad("mcp_schema_incompatible", "a row lacks string `name` / boolean `enabled`")
        if item["name"] in seen:
            raise bad("mcp_schema_incompatible", "duplicate server name")
        seen.add(item["name"])
        rows.append({"name": item["name"], "enabled": item["enabled"]})
    return rows


@dataclass(frozen=True)
class McpRecheck:
    """The effective MCP view *now*, under the audited overrides (V10: the complete snapshot,
    not just the part that is refused)."""

    unexpected: tuple[str, ...]        # enabled, neither disabled by the audit nor allow-listed
    allowed_enabled: tuple[str, ...]   # allow-listed servers that are enabled now (uncontained)


def recheck_mcp(cli: Cli, env: Mapping[str, str], repo_root: Path, audit: "Audit") -> McpRecheck:
    """Re-read the effective MCP servers.  Narrows (does not eliminate) the audit-to-exec window:
    the CLI offers no deny-all, so a server added to the configuration between this read and
    exec's own config load is not covered.  Allow-listed servers that became enabled since the
    audit are returned so the caller can report them as uncontained exceptions."""
    rows = _list_mcp(cli, env, str(repo_root), [*audit.feature_args(), *audit.server_overrides])
    return McpRecheck(
        unexpected=tuple(sorted(r["name"] for r in rows if r["enabled"] and r["name"] not in audit.allow)),
        allowed_enabled=tuple(sorted(r["name"] for r in rows if r["enabled"] and r["name"] in audit.allow)),
    )


def _find_hook_sources(repo_root: Path, env: Mapping[str, str]) -> tuple[list[str], list[str]]:
    hook_files: list[str] = []
    project_cfg: list[str] = []
    codex_home = Path(env.get("CODEX_HOME") or (Path(env.get("USERPROFILE") or env.get("HOME") or Path.home()) / ".codex"))
    hook_table = re.compile(r"^\s*\[+\s*(hooks|plugin_hooks)\b|^\s*hooks\s*=", re.IGNORECASE | re.MULTILINE)
    for base, label in ((codex_home, "CODEX_HOME"), (repo_root / ".codex", "repo/.codex")):
        if not base.is_dir():
            continue
        for item in base.iterdir():
            if item.is_file() and "hook" in item.name.lower():
                hook_files.append(f"{label}/{item.name}")
            if item.is_dir() and "hook" in item.name.lower():
                hook_files.append(f"{label}/{item.name}/")
        cfg = base / "config.toml"
        if cfg.is_file():
            try:
                if hook_table.search(cfg.read_text(encoding="utf-8", errors="replace")):
                    hook_files.append(f"{label}/config.toml (hooks table)")
            except OSError:
                pass
            if label == "repo/.codex":
                project_cfg.append("repo/.codex/config.toml")
    return sorted(set(hook_files)), project_cfg


def capability_audit(cli: Cli, env: Mapping[str, str], repo_root: Path, allow: frozenset[str],
                     features: Mapping[str, bool]) -> Audit:
    """Build, then *verify against the CLI's own resolved config*, the capability policy.

    Fails closed on: a missing required control, a schema it cannot parse, a feature gate
    that is still on after the override, an MCP server still enabled after the override.
    Everything it cannot verify is recorded as ``requested`` / ``uncontained`` rather than
    claimed.
    """
    audit = Audit()
    cwd = str(repo_root)

    missing = [g for g in REQUIRED_FEATURE_GATES if g not in features]
    if missing:
        raise errors.BridgeError(
            errors.CAPABILITY_AUDIT,
            "This Codex CLI has no control for required capabilities: " + ", ".join(missing) + ". "
            "They cannot be disabled, so the capability policy cannot be enforced.",
            code="required_control_missing",
            recovery="Use the tested CLI version, or have the bridge reviewed against this one. "
                     "No inference was attempted.",
        )
    audit.disable_features = [g for g in (*REQUIRED_FEATURE_GATES, *OPTIONAL_FEATURE_GATES) if g in features]
    audit.config_overrides = list(WEB_SEARCH_OVERRIDE)
    audit.hook_sources, audit.project_config = _find_hook_sources(repo_root, env)

    feature_args: list[str] = []
    for name in audit.disable_features:
        feature_args += ["--disable", name]

    # (1) feature gates: verified from the CLI's effective-state readout, cwd = repository
    effective = load_features(cli, env, extra=feature_args, cwd=cwd)
    still_on = [g for g in audit.disable_features if effective.get(g) is not False]
    if still_on:
        raise errors.BridgeError(
            errors.CAPABILITY_AUDIT,
            "Feature gates remain enabled after the bridge's overrides: " + ", ".join(still_on),
            code="feature_disable_not_effective",
            recovery="A project or managed configuration may be re-enabling them. "
                     "No inference was attempted.",
        )

    # (2) MCP servers: disable every configured server not allow-listed, then re-read
    servers = _list_mcp(cli, env, cwd, feature_args)
    for row in servers:
        name = row["name"]
        if not row["enabled"]:
            continue
        if name in allow:
            continue  # reported from the final readout below (N8), not from this snapshot
        if not _SAFE_SERVER_NAME.fullmatch(name):
            raise errors.BridgeError(
                errors.CAPABILITY_AUDIT,
                f"MCP server name {name!r} cannot be expressed safely as a config override.",
                code="server_name_unsafe",
                recovery="Rename or remove that server in your Codex configuration.",
            )
        audit.server_overrides += ["-c", f"mcp_servers.{name}.enabled=false"]
        audit.disabled_servers.append(name)
    after = _list_mcp(cli, env, cwd, [*feature_args, *audit.server_overrides])
    leaking = sorted(r["name"] for r in after if r["enabled"] and r["name"] not in allow)
    if leaking:
        raise errors.BridgeError(
            errors.CAPABILITY_AUDIT,
            "MCP servers remain enabled after the bridge's overrides: " + ", ".join(leaking),
            code="mcp_still_enabled",
            recovery=(
                "Disable them in your Codex configuration, or add them to GPT_ARCHITECT_MCP_ALLOW "
                "only if you have verified they are read-only. No inference was attempted."
            ),
        )
    audit.allow = frozenset(allow)
    audit.allowed_servers = sorted(r["name"] for r in after if r["enabled"] and r["name"] in allow)
    unknown_allowed = sorted(a for a in allow if a not in {r["name"] for r in after})

    # (3) classify by evidence level (what is *known*, not what is hoped)
    def cap(name: str, control: str, level: str, note: str = "") -> None:
        audit.capabilities.append({"name": name, "control": control, "level": level, "note": note})

    cap("mcp_servers", "mcp_servers.<name>.enabled=false per configured server", LEVEL_RESOLVED,
        f"snapshot-scoped: {len(audit.disabled_servers)} disabled at audit time; the CLI has no deny-all "
        "mechanism, so a server added to the configuration after the audit is not covered "
        "(rechecked immediately before exec and after the turn)"
        + (f"; allow-list names not configured: {', '.join(unknown_allowed)}" if unknown_allowed else ""))
    if audit.allowed_servers:
        cap("mcp_allowed_servers", "GPT_ARCHITECT_MCP_ALLOW (human exception)", LEVEL_UNCONTAINED,
            "allowed servers are NOT constrained by the read-only sandbox: " + ", ".join(audit.allowed_servers))
    cap("hooks", "feature gate: hooks", LEVEL_RESOLVED,
        "hook definitions found: " + (", ".join(audit.hook_sources) or "none"))
    cap("plugins_and_apps", "feature gates: plugins, apps", LEVEL_RESOLVED)
    cap("browser_and_computer_use", "feature gates: browser_use*, in_app_browser, computer_use", LEVEL_RESOLVED)
    cap("delegation", "feature gates: multi_agent (+ multi_agent_v2 when present)", LEVEL_RESOLVED,
        "resolved = CLI effective-state readout; removal of the tools at runtime needs a live tool listing")
    cap("image_generation", "feature gate: image_generation", LEVEL_RESOLVED,
        "same caveat as delegation")
    cap("web_search", 'config key web_search="disabled"', LEVEL_REQUESTED,
        "key exists in the CLI's config schema; the CLI offers no readout for it and the `web__run` "
        "tool observed in live runs has not been mapped to this key")
    cap("shell_and_apply_patch_writes", "sandbox_mode=read-only + approval_policy=never (argv-pinned)",
        LEVEL_REQUESTED, "argv content is guarded at build time. This audit does not establish "
                         "effectiveness; native write rejection has not been live-verified for the "
                         "current source (attack results in docs/history are for earlier candidates)")
    cap("view_image_goals_sleep_skills", "none available/used", LEVEL_UNCONTAINED,
        "view_image can read local image files; skills under ~/.codex and ~/.agents inject "
        "instructions; neither is a repository write route")
    return audit


# ----------------------------------------------------------------- sandbox preflight

SANDBOX_PREFLIGHT_TIMEOUT_S = 60
_MAX_SOURCE_PROBE_BYTES = 64 * 1024
_SANDBOX_RECOVERY = (
    "The Windows sandbox refresh runs for every new workspace and fails while a node_repl.exe "
    "(ChatGPT / Codex desktop app, or a Codex background daemon) holds its runtime file open. "
    "Quit those apps, run `codex app-server daemon stop`, confirm `Get-Process node_repl` is empty, "
    "then retry. Details: ~/.codex/.sandbox/sandbox.<date>.log. No model call was made."
)


def running_lock_holders() -> list[int]:
    """PIDs of running node_repl.exe (best effort; Windows only; never raises)."""
    if sys.platform != "win32":
        return []
    try:
        res = subprocess.run(
            [execpath.system_tool("tasklist"), "/FI", "IMAGENAME eq node_repl.exe", "/FO", "CSV", "/NH"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False,
        )
        pids = []
        for line in res.stdout.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == "node_repl.exe" and parts[1].isdigit():
                pids.append(int(parts[1]))
        return pids
    except Exception:  # noqa: BLE001
        return []


_SOURCE_EXT = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".cs", ".rb",
    ".php", ".sh", ".ps1", ".sql", ".gradle", ".xml", ".json", ".toml", ".yaml", ".yml", ".md", ".txt",
})
_UNSAFE_NAME = re.compile(r"[\s&|<>^%!\"'()\[\]{};,=`$]|^[-/]")


def _probe_file(repo_root: Path, env: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Pick a small tracked file whose name is safe to hand to a program as data.

    Returns (relative posix path, None) or (None, skip_reason).  Prefers source-like files.
    Runs host-side git through ``gitsafe`` (no filters/textconv/fsmonitor)."""
    if gitsafe.trusted_git(repo_root) is None:
        return None, "no_trusted_git"
    if not gitsafe.supported(repo_root):
        return None, "git_too_old_for_safe_mode"
    try:
        res = subprocess.run(
            gitsafe.safe_git_argv(repo_root, "ls-files", "-z"),
            stdin=subprocess.DEVNULL, capture_output=True, timeout=20, check=False,
            env=gitsafe.safe_git_env(env),
        )
    except Exception:  # noqa: BLE001
        return None, "git_unavailable"
    if res.returncode != 0:
        return None, "git_ls_files_failed"
    best: tuple[tuple[int, int], str] | None = None
    unsafe = 0
    for raw in res.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            name = raw.decode("ascii")
        except UnicodeDecodeError:
            unsafe += 1
            continue
        if _UNSAFE_NAME.search(name):
            unsafe += 1
            continue
        try:
            size = (repo_root / name).stat().st_size
        except OSError:
            continue
        if 0 < size <= _MAX_SOURCE_PROBE_BYTES:
            key = (0 if Path(name).suffix.lower() in _SOURCE_EXT else 1, size)
            if best is None or key < best[0]:
                best = (key, name)
    if best is None:
        return None, "no_safe_small_tracked_file" + (f" ({unsafe} unsafe name(s) excluded)" if unsafe else "")
    return best[1], None


def sandbox_preflight(
    cli: Cli,
    env: Mapping[str, str],
    repo_root: Path,
    audit: "Audit",
    windows_sandbox: str | None,
) -> dict[str, Any]:
    """Model-free *readiness* check for the sandbox in this repository.

    Stages, each through ``codex sandbox`` with the call's own overrides and cwd:
    (1) ``cmd /c cd`` - a process can be started for this workspace;
    (2) ``git rev-parse --is-inside-work-tree`` - git can open the repository;
    (3) ``findstr`` over one small tracked file - a tracked file can actually be read (a
        single-file readability probe, not a proof about the other files).

    It is readiness only, never proof about the turn that follows: ``codex sandbox`` is not
    the same code path as ``codex exec``, state can change in between, and a passing
    ``git rev-parse`` says nothing about source readability (stage 3 exists for that, and
    even it samples one file).  The actual turn is inspected separately (runner.Events).
    No model is contacted and no allowance is used; Codex may grant its sandbox users read
    ACEs on ``repo_root``, as for any real call.  Windows only.
    """
    if sys.platform != "win32":
        return {"ok": True, "skipped": "unsupported_platform"}

    prefix = [*cli.base_argv, "sandbox", "-c", 'sandbox_mode="read-only"']
    if windows_sandbox:
        prefix += ["-c", f'windows.sandbox="{windows_sandbox}"']
    prefix += audit.extra_argv() + ["--"]
    cwd = str(repo_root)

    start = run_simple([*prefix, "cmd", "/c", "cd"], env, cwd=cwd, timeout_s=SANDBOX_PREFLIGHT_TIMEOUT_S)
    if start.timed_out or start.returncode != 0 or not start.stdout.strip():
        holders = running_lock_holders()
        excerpt = redact(_text(start))[-300:] or "no output"
        hint = f" node_repl.exe is running (PIDs {', '.join(map(str, holders))})." if holders else ""
        raise errors.BridgeError(
            errors.SANDBOX_UNAVAILABLE,
            "The Codex sandbox could not start a process for this repository"
            + (" (timed out)" if start.timed_out else f" (exit {start.returncode})")
            + f": {excerpt}.{hint}",
            code="sandbox_timeout" if start.timed_out else "sandbox_setup_failed",
            recovery=_SANDBOX_RECOVERY,
        )

    read = run_simple([*prefix, "git", "rev-parse", "--is-inside-work-tree"], env, cwd=cwd,
                      timeout_s=SANDBOX_PREFLIGHT_TIMEOUT_S)
    if read.timed_out or read.returncode != 0 or read.stdout.strip().lower() != "true":
        excerpt = redact(_text(read))[-300:] or "no output"
        raise errors.BridgeError(
            errors.SANDBOX_UNAVAILABLE,
            "The Codex sandbox starts but cannot open this repository "
            f"(exit {read.returncode}): {excerpt}",
            code="repo_unreadable",
            recovery=(
                "Make sure `git` is on PATH for the sandbox and the repository is readable by the "
                "sandbox account. No model call was made."
            ),
        )

    probe, skipped = _probe_file(repo_root, env)
    if probe is None:
        return {"ok": True, "stages": ["start", "git"], "source_read": f"skipped:{skipped}"}
    # `findstr` receives the name as data (no cmd.exe parsing, unlike `cmd /c type <name>`)
    source = run_simple([*prefix, "findstr", "/N", "/R", "^", probe.replace("/", "\\")], env, cwd=cwd,
                        timeout_s=SANDBOX_PREFLIGHT_TIMEOUT_S)
    if source.timed_out or source.returncode != 0:
        raise errors.BridgeError(
            errors.SANDBOX_UNAVAILABLE,
            "The Codex sandbox can open the repository but could not read a tracked source file "
            f"(exit {source.returncode}); `git rev-parse` success does not prove source readability, and "
            "this probe samples one small tracked file only.",
            code="source_file_unreadable",
            recovery="Check the sandbox account's read access to the working tree. "
                     "No model call was made.",
        )
    return {"ok": True, "stages": ["start", "git", "source_read"], "source_read": "ok"}


# ----------------------------------------------------------------- argv construction

FORBIDDEN_TOKENS = (
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--full-auto",
    "--yolo",
    "danger-full-access",
    "workspace-write",
    "--add-dir",
    "--search",
    "--approve-for-me",
)


def assert_read_only_argv(argv: Sequence[str], skip: int = 0) -> None:
    """Fail closed if the policy flags are absent or a bypass flag is present.

    ``skip`` leaves out the launcher prefix (interpreter + script path).
    """
    for token in argv[skip:]:
        for bad in FORBIDDEN_TOKENS:
            if bad in token:
                raise errors.BridgeError(
                    errors.INVALID_CONFIGURATION, f"refusing to run: argv contains {bad!r}", code="forbidden_flag"
                )
    joined = "\0".join(argv[skip:])
    if 'sandbox_mode="read-only"' not in joined or 'approval_policy="never"' not in joined:
        raise errors.BridgeError(
            errors.INVALID_CONFIGURATION,
            "refusing to run: read-only sandbox / non-interactive approval not both pinned",
            code="policy_missing",
        )


def build_argv(
    cli: Cli,
    *,
    model: str,
    effort: str,
    output_file: str,
    audit: Audit,
    resume_thread: str | None,
    windows_sandbox: str | None = None,
) -> list[str]:
    argv: list[str] = [*cli.base_argv, "exec"]
    if resume_thread:
        argv += ["resume", resume_thread, "--json"]
    else:
        argv += ["--json", "--sandbox", "read-only"]
    argv += ["--model", model]
    argv += ["-c", 'sandbox_mode="read-only"']
    argv += ["-c", 'approval_policy="never"']
    argv += ["-c", f'model_reasoning_effort="{effort}"']
    if windows_sandbox:  # explicit opt-in only; default respects the user's Codex config
        argv += ["-c", f'windows.sandbox="{windows_sandbox}"']
    argv += audit.extra_argv()
    argv += ["--output-last-message", output_file, "-"]
    assert_read_only_argv(argv, skip=len(cli.base_argv))
    return argv
