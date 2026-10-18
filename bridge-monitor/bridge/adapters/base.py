from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class AdapterResult:
    state: Literal["completed", "failed", "uncertain", "denied"]
    answer: str | None = None
    reported_model: str | None = None
    uncertainty: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw_answer: str | None = None
    raw_content_id: str | None = None
    completion_claim: dict | None = None
    local_tool_attempts: int | None = None
    terminated: bool = False
    cancelled: bool = False


def ensure_lease(lease, expected_call_id: str | None = None) -> None:
    if not lease.lease_id or lease.fencing_token < 1:
        raise ValueError("durable dispatch lease required")
    if expected_call_id is not None and lease.call_id != expected_call_id:
        raise ValueError("dispatch lease does not bind requested call")
