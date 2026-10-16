"""Stable error categories for the v2 bridge.

Every failure that reaches an MCP caller is a ``BridgeError`` whose text starts
with ``[bridge-error:<category>/<code>]`` so callers (and humans) can act on it
without parsing prose.  ``inference_may_have_started`` is the replay guard: once
it is true, nothing in the bridge re-runs the call.
"""

from __future__ import annotations

# --- stable categories -------------------------------------------------------
INVALID_INPUT = "invalid_input"
INVALID_CONFIGURATION = "invalid_configuration"
CLI_MISSING = "cli_missing"
CLI_INCOMPATIBLE = "cli_incompatible"
AUTHENTICATION = "authentication"
CAPABILITY_AUDIT = "capability_audit_failed"
STATE_ERROR = "state_error"
SESSION_CORRUPT = "session_corrupt"
SESSION_MISMATCH = "session_mismatch"
QUEUE_TIMEOUT = "queue_timeout"
RATE_LIMITED = "rate_limited"
SUBPROCESS_FAILED = "subprocess_failed"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
OUTPUT_INCOMPLETE = "output_incomplete"
INTERNAL = "internal_error"
SANDBOX_UNAVAILABLE = "sandbox_unavailable"
SESSION_IN_DOUBT = "session_in_doubt"

ALL_CATEGORIES = (
    INVALID_INPUT,
    INVALID_CONFIGURATION,
    CLI_MISSING,
    CLI_INCOMPATIBLE,
    AUTHENTICATION,
    CAPABILITY_AUDIT,
    STATE_ERROR,
    SESSION_CORRUPT,
    SESSION_MISMATCH,
    QUEUE_TIMEOUT,
    RATE_LIMITED,
    SUBPROCESS_FAILED,
    TIMEOUT,
    CANCELLED,
    OUTPUT_INCOMPLETE,
    INTERNAL,
    SANDBOX_UNAVAILABLE,
    SESSION_IN_DOUBT,
)


class BridgeError(RuntimeError):
    """A categorised, actionable failure."""

    #: U8: the validated thread id the runner's event parser had ALREADY observed when this failure was
    #: raised (a frozen snapshot, set by `runner.run_turn`), so recovery never depends on an observer
    #: callback that may not have run yet.
    observed_thread_id: str | None = None

    def __init__(
        self,
        category: str,
        message: str,
        *,
        code: str | None = None,
        recovery: str | None = None,
        inference_may_have_started: bool = False,
    ) -> None:
        assert category in ALL_CATEGORIES, category
        self.category = category
        self.code = code or category
        self.message = message
        self.recovery = recovery
        self.inference_may_have_started = inference_may_have_started
        super().__init__(self.render())

    def render(self) -> str:
        head = f"[bridge-error:{self.category}/{self.code}] {self.message}"
        parts = [head]
        if self.inference_may_have_started:
            parts.append(
                "The model turn may already have run and consumed allowance; "
                "the bridge did NOT retry it."
            )
        if self.recovery:
            parts.append(f"Recovery: {self.recovery}")
        return "\n".join(parts)


class InputError(BridgeError, ValueError):
    """Bad caller input (kept ``ValueError``-compatible with v1)."""

    def __init__(self, message: str, *, code: str = "invalid_input") -> None:
        super().__init__(INVALID_INPUT, message, code=code)
