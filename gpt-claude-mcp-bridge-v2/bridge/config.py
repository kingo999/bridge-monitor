"""Configuration and model/effort routing.

Precedence (highest first), evaluated independently for model and effort and
independently for each of the two tools:

    1. GPT_ARCHITECT_MODEL_CONSULT / GPT_ARCHITECT_MODEL_REVIEW   (per tool)
       GPT_ARCHITECT_EFFORT_CONSULT / GPT_ARCHITECT_EFFORT_REVIEW
    2. GPT_ARCHITECT_MODEL / GPT_ARCHITECT_EFFORT                 (v1 compatible)
    3. built-in defaults: gpt-6.1-sol / high

Empty strings count as "unset".  Nothing here ever changes the selected route
at runtime: no escalation, no fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Mapping

from . import errors

DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_EFFORT = "high"
DEFAULT_TIMEOUT_S = 900
MAX_PROMPT_CHARS = 60_000

CONSULT = "consult_architect"
REVIEW = "review_implementation"
TOOL_NAMES = (CONSULT, REVIEW)
_TOOL_SUFFIX = {CONSULT: "CONSULT", REVIEW: "REVIEW"}

DEFAULT_SESSION = "default"
V1_STATE_DIRNAME = ".gpt-architect-bridge"
V2_STATE_DIRNAME = ".gpt-architect-bridge-v2"

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_EFFORT_RE = re.compile(r"^[a-z][a-z_-]{1,15}$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,47}$")
_SERVER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Route:
    tool: str
    model: str
    effort: str
    model_source: str  # env var name or "default"
    effort_source: str

    def public(self) -> dict[str, str]:
        return {
            "model": self.model,
            "effort": self.effort,
            "model_source": self.model_source,
            "effort_source": self.effort_source,
        }


@dataclass(frozen=True)
class Settings:
    timeout_s: int
    queue_timeout_s: int
    session: str
    state_dir: Path
    mcp_allow: frozenset[str]
    allow_untested_cli: bool
    codex_path_override: str | None
    windows_sandbox: str | None  # None = respect the user's Codex config
    sandbox_preflight: bool = True
    windows_sandbox_source: str = "environment"   # "environment" | "settings_file" (when windows_sandbox is set)


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _bad(name: str, value: str, why: str) -> errors.BridgeError:
    return errors.BridgeError(
        errors.INVALID_CONFIGURATION,
        f"{name}={value!r} is invalid: {why}",
        code="bad_env",
        recovery=f"Fix or unset {name} in the MCP server's environment and restart Claude Code.",
    )


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = int(raw, 10)
    except ValueError:
        raise _bad(name, raw, "not an integer") from None
    if not lo <= value <= hi:
        raise _bad(name, raw, f"must be between {lo} and {hi}")
    return value


def resolve_route(tool: str, env: Mapping[str, str]) -> Route:
    if tool not in _TOOL_SUFFIX:
        raise errors.BridgeError(errors.INVALID_CONFIGURATION, f"unknown tool {tool!r}", code="bad_tool")
    suffix = _TOOL_SUFFIX[tool]

    def pick(base: str, default: str, pattern: re.Pattern[str], what: str) -> tuple[str, str]:
        for name in (f"{base}_{suffix}", base):
            raw = _get(env, name)
            if raw is not None:
                if not pattern.fullmatch(raw):
                    raise _bad(name, raw, f"not a valid {what}")
                return raw, name
        return default, "default"

    model, model_src = pick("GPT_ARCHITECT_MODEL", DEFAULT_MODEL, _MODEL_RE, "model name")
    effort, effort_src = pick("GPT_ARCHITECT_EFFORT", DEFAULT_EFFORT, _EFFORT_RE, "effort level")
    return Route(tool, model, effort, model_src, effort_src)


#: A client that starts the server with its own minimal environment (observed: the ChatGPT/Codex desktop app
#: spawns it with ~20 variables and no way for the bridge's owner to add one from outside) cannot be given
#: `GPT_ARCHITECT_WINDOWS_SANDBOX`.  The same ONE override may therefore also live in the v2 state directory.
#: Strict on purpose: a regular file, at most MAX_SETTINGS_BYTES, one JSON object, known keys, known values;
#: anything else REFUSES the call (never silently ignored).  The environment variable wins when both are set.
SETTINGS_FILENAME = "settings.json"
MAX_SETTINGS_BYTES = 4096
_SETTINGS_KEYS = frozenset({"windows_sandbox"})


def _settings_file_bad(path: Path, why: str) -> errors.BridgeError:
    return errors.BridgeError(
        errors.INVALID_CONFIGURATION,
        f"The bridge settings file {path} is invalid: {why}.",
        code="settings_file_invalid",
        recovery=(f"Fix or delete {path.name} in the v2 state directory. Accepted content: "
                  '{"windows_sandbox": "elevated" | "unelevated"} and nothing else. No model call was made.'),
    )


def read_settings_file(state_dir: Path) -> dict[str, str]:
    """The validated contents of ``<state_dir>/settings.json`` ({} when it does not exist)."""
    path = state_dir / SETTINGS_FILENAME
    try:
        if not path.exists() and not path.is_symlink():
            return {}
        if path.is_symlink() or not path.is_file():
            raise _settings_file_bad(path, "it is not a regular file")
        if path.stat().st_size > MAX_SETTINGS_BYTES:
            raise _settings_file_bad(path, f"it is larger than {MAX_SETTINGS_BYTES} bytes")
        data = json.loads(path.read_text(encoding="utf-8"))
    except errors.BridgeError:
        raise
    except (OSError, ValueError) as exc:
        raise _settings_file_bad(path, f"it cannot be read as JSON ({exc.__class__.__name__})") from None
    if not isinstance(data, dict):
        raise _settings_file_bad(path, "the top level is not an object")
    unknown = sorted(set(data) - _SETTINGS_KEYS)
    if unknown:
        raise _settings_file_bad(path, f"unknown key(s) {', '.join(map(repr, unknown[:3]))}")
    value = data.get("windows_sandbox")
    if "windows_sandbox" in data and value not in ("elevated", "unelevated"):
        raise _settings_file_bad(path, "windows_sandbox must be 'elevated' or 'unelevated'")
    return {k: v for k, v in data.items()}


def default_state_dir(env: Mapping[str, str]) -> Path:
    override = _get(env, "GPT_ARCHITECT_V2_STATE_DIR")
    if override:
        return Path(override)
    home = _get(env, "USERPROFILE") or _get(env, "HOME") or str(Path.home())
    return Path(home) / V2_STATE_DIRNAME


def load_settings(env: Mapping[str, str]) -> Settings:
    timeout = _int(env, "GPT_ARCHITECT_TIMEOUT", DEFAULT_TIMEOUT_S, 1, 86_400)
    queue_timeout = _int(env, "GPT_ARCHITECT_QUEUE_TIMEOUT", timeout + 60, 1, 172_800)

    session = _get(env, "GPT_ARCHITECT_SESSION") or DEFAULT_SESSION
    if not _SESSION_RE.fullmatch(session):
        raise _bad("GPT_ARCHITECT_SESSION", session, "use 1-48 letters, digits, '_', '.', '-'")

    allow: set[str] = set()
    raw_allow = _get(env, "GPT_ARCHITECT_MCP_ALLOW")
    if raw_allow:
        for name in raw_allow.split(","):
            name = name.strip()
            if not name:
                continue
            if not _SERVER_RE.fullmatch(name):
                raise _bad("GPT_ARCHITECT_MCP_ALLOW", name, "not a plain MCP server name")
            allow.add(name)

    untested = _get(env, "GPT_ARCHITECT_ALLOW_UNTESTED_CLI")
    if untested not in (None, "0", "1"):
        raise _bad("GPT_ARCHITECT_ALLOW_UNTESTED_CLI", untested, "must be 0 or 1")

    win_sandbox = _get(env, "GPT_ARCHITECT_WINDOWS_SANDBOX")
    if win_sandbox is not None and win_sandbox not in ("elevated", "unelevated"):
        raise _bad("GPT_ARCHITECT_WINDOWS_SANDBOX", win_sandbox, "must be 'elevated' or 'unelevated'")
    win_sandbox_source = "environment"
    if win_sandbox is None:
        from_file = read_settings_file(default_state_dir(env)).get("windows_sandbox")
        if from_file:
            win_sandbox, win_sandbox_source = from_file, "settings_file"

    preflight = _get(env, "GPT_ARCHITECT_SANDBOX_PREFLIGHT")
    if preflight not in (None, "0", "1"):
        raise _bad("GPT_ARCHITECT_SANDBOX_PREFLIGHT", preflight, "must be 0 or 1")

    on_infra = _get(env, "GPT_ARCHITECT_ON_TURN_INFRA_FAILURE")
    if on_infra not in (None, "error"):
        raise _bad("GPT_ARCHITECT_ON_TURN_INFRA_FAILURE", on_infra,
                   "the 'degraded' mode was removed from the production path (review P3): an answer whose "
                   "actual turn hit infrastructure failures is always withheld; only 'error' is accepted")

    return Settings(
        sandbox_preflight=preflight != "0",
        windows_sandbox=win_sandbox,
        windows_sandbox_source=win_sandbox_source,
        timeout_s=timeout,
        queue_timeout_s=queue_timeout,
        session=session,
        state_dir=default_state_dir(env),
        mcp_allow=frozenset(allow),
        allow_untested_cli=untested == "1",
        codex_path_override=_get(env, "GPT_ARCHITECT_CODEX_PATH"),
    )


def validate_route_against_catalog(route: Route, catalog: Mapping[str, list[str]]) -> None:
    """Reject unsupported model/effort combinations before any inference."""
    if route.model not in catalog:
        raise errors.BridgeError(
            errors.INVALID_CONFIGURATION,
            f"model {route.model!r} (from {route.model_source}) is not in this Codex CLI's model catalog.",
            code="unknown_model",
            recovery=(
                "Run `codex debug models` to list valid slugs, then set "
                f"{route.model_source if route.model_source != 'default' else 'GPT_ARCHITECT_MODEL'}. "
                "The bridge never substitutes another model."
            ),
        )
    levels = catalog[route.model]
    if route.effort not in levels:
        raise errors.BridgeError(
            errors.INVALID_CONFIGURATION,
            f"reasoning effort {route.effort!r} (from {route.effort_source}) is not supported by "
            f"model {route.model!r}; supported: {', '.join(levels) or 'none listed'}.",
            code="unsupported_effort",
            recovery=(
                f"Set {route.effort_source if route.effort_source != 'default' else 'GPT_ARCHITECT_EFFORT'} "
                "to a supported level. The bridge never escalates or lowers effort automatically."
            ),
        )
