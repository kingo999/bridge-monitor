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
