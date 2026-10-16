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
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence

from . import config, errors
from .redact import redact

TESTED_CLI_SERIES = "0.162"  # major.minor the v2 test-suite and fixtures were written against
TESTED_CLI_VERSION = "0.162.1"

#: env vars that would route Codex to API-key billing; removed from every child.
API_KEY_ENV_VARS = ("CODEX_API_KEY", "OPENAI_API_KEY")

#: capabilities that can mutate state outside the sandbox or reach other tools.
DENY_FEATURES = (
    "hooks",
    "plugins",
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "in_app_browser",
    "computer_use",
)

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
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
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
        found = shutil.which("codex", path=env.get("PATH")) or shutil.which("codex.cmd", path=env.get("PATH"))
    if not found:
        raise errors.BridgeError(
            errors.CLI_MISSING,
            "Codex CLI not found on PATH.",
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
        node_path = str(node) if node.is_file() else shutil.which("node", path=env.get("PATH"))
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


def check_compat(version: str, allow_untested: bool) -> None:
    series = ".".join(version.split(".")[:2])
    if series == TESTED_CLI_SERIES or allow_untested:
        return
    raise errors.BridgeError(
        errors.CLI_INCOMPATIBLE,
        f"Codex CLI {version} is outside the tested series {TESTED_CLI_SERIES}.x "
        f"(tested {TESTED_CLI_VERSION}); flags and event formats may differ.",
        code="untested_cli",
        recovery=(
            "Re-run the fixture tests and the opt-in smoke test against this version, then set "
            "GPT_ARCHITECT_ALLOW_UNTESTED_CLI=1. The bridge does not upgrade or downgrade the CLI."
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


def load_features(cli: Cli, env: Mapping[str, str]) -> set[str]:
    res = run_simple([*cli.base_argv, "features", "list"], env)
    names = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if parts and re.match(r"^[a-z0-9_.]+$", parts[0]):
            names.add(parts[0])
    if res.returncode != 0 or not names:
        raise errors.BridgeError(
            errors.CLI_INCOMPATIBLE,
            "Could not read `codex features list`; cannot build a verified capability policy.",
            code="features_unavailable",
            recovery="Run `codex features list` in a terminal. No inference was attempted.",
        )
    return names


# ----------------------------------------------------------------- capability audit


@dataclass
class Audit:
    disable_features: list[str] = field(default_factory=list)
    server_overrides: list[str] = field(default_factory=list)  # ["-c", "mcp_servers.x.enabled=false", ...]
    disabled_servers: list[str] = field(default_factory=list)
    allowed_servers: list[str] = field(default_factory=list)
    hook_sources: list[str] = field(default_factory=list)
    project_config: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    def extra_argv(self) -> list[str]:
        out: list[str] = []
        for name in self.disable_features:
            out += ["--disable", name]
        out += self.server_overrides
        return out

    def public(self) -> dict[str, Any]:
        return {
            "disabled_features": self.disable_features,
            "disabled_mcp_servers": self.disabled_servers,
            "allowed_mcp_servers": self.allowed_servers,
            "hook_sources_found": self.hook_sources,
            "project_config_found": self.project_config,
            "unverified": self.unverified,
        }


def _list_mcp(cli: Cli, env: Mapping[str, str], cwd: str, extra: Sequence[str]) -> list[dict[str, Any]]:
    res = run_simple([*cli.base_argv, "mcp", "list", "--json", *extra], env, cwd=cwd)
    try:
        data = json.loads(res.stdout)
        if res.returncode != 0 or not isinstance(data, list):
            raise ValueError("bad")
        rows = []
        for item in data:
            rows.append({"name": str(item["name"]), "enabled": bool(item["enabled"])})
        return rows
    except Exception:  # noqa: BLE001
        raise errors.BridgeError(
            errors.CAPABILITY_AUDIT,
            "Could not read the effective MCP server list (`codex mcp list --json`).",
            code="mcp_list_unavailable",
            recovery="Run `codex mcp list --json` in the repository; no inference was attempted.",
        ) from None


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
                     features: set[str]) -> Audit:
    audit = Audit()
    audit.disable_features = [f for f in DENY_FEATURES if f in features]
    audit.hook_sources, audit.project_config = _find_hook_sources(repo_root, env)

    if audit.hook_sources and "hooks" not in features:
        raise errors.BridgeError(
            errors.CAPABILITY_AUDIT,
            "Hook definitions were found but this Codex CLI has no `hooks` feature flag to disable them: "
            + ", ".join(audit.hook_sources),
            code="hooks_not_disableable",
            recovery="Remove or relocate the hook definitions, or update the bridge after review.",
        )

    feature_args: list[str] = []
    for name in audit.disable_features:
        feature_args += ["--disable", name]

    cwd = str(repo_root)
    servers = _list_mcp(cli, env, cwd, feature_args)
    for row in servers:
        name = row["name"]
        if not row["enabled"]:
            continue
        if name in allow:
            audit.allowed_servers.append(name)
            audit.unverified.append(f"mcp:{name} allowed by GPT_ARCHITECT_MCP_ALLOW (not verified read-only)")
            continue
        if not _SAFE_SERVER_NAME.match(name):
            raise errors.BridgeError(
                errors.CAPABILITY_AUDIT,
                f"MCP server name {name!r} cannot be expressed safely as a config override.",
                code="server_name_unsafe",
                recovery="Rename or remove that server in your Codex configuration.",
            )
        audit.server_overrides += ["-c", f"mcp_servers.{name}.enabled=false"]
        audit.disabled_servers.append(name)

    # Verify the *effective* set with exactly the overrides that will be used.
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
    audit.unverified.append("hooks/apps/browser/computer-use disabled via --disable; effect only verifiable live")
    return audit


# ----------------------------------------------------------------- sandbox preflight

SANDBOX_PREFLIGHT_TIMEOUT_S = 60
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
            ["tasklist", "/FI", "IMAGENAME eq node_repl.exe", "/FO", "CSV", "/NH"],
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


def sandbox_preflight(
    cli: Cli,
    env: Mapping[str, str],
    repo_root: Path,
    audit: "Audit",
    windows_sandbox: str | None,
) -> dict[str, Any]:
    """Model-free proof that the sandbox can start a process in, and read, the repo.

    Uses ``codex sandbox`` (same backend, same config stack, same ``-c``/``--disable``
    overrides as the real call).  No model is contacted and no allowance is used.  A
    side effect shared with any real call: Codex may grant its sandbox users read
    access (ACEs) to ``repo_root``.  Windows only; other platforms are skipped.
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
            "The Codex sandbox starts but cannot read this repository "
            f"(exit {read.returncode}): {excerpt}",
            code="repo_unreadable",
            recovery=(
                "Keep the repository outside %TEMP% (the restricted token cannot read it), and "
                "make sure `git` is on PATH for the sandbox. No model call was made."
            ),
        )
    return {"ok": True}


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
