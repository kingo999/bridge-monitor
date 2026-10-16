"""One absolute local cutoff shared by context, queue and execution stages."""

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class DeadlineBudget:
    local_cutoff_at: datetime
    bridge_signal_deadline_at: datetime
    fallback_start_latest_at: datetime


def reserve_local_deadlines(*, now: datetime, parent_deadline_at: datetime,
                            client_deadline_at: datetime, fallback_seconds: int,
                            delivery_margin_seconds: int) -> DeadlineBudget:
    if any(value.tzinfo is None for value in (now, parent_deadline_at, client_deadline_at)):
        raise ValueError("timezone-aware deadlines required")
    if fallback_seconds <= 0 or delivery_margin_seconds <= 0:
        raise ValueError("positive continuation and delivery allowance required")
    fallback_latest = parent_deadline_at - timedelta(seconds=fallback_seconds)
    signal_deadline = client_deadline_at - timedelta(seconds=delivery_margin_seconds)
    cutoff = min(fallback_latest, signal_deadline)
    if cutoff <= now:
        raise ValueError("no time remains for local work and paid continuation")
    return DeadlineBudget(local_cutoff_at=cutoff,
                          bridge_signal_deadline_at=signal_deadline,
                          fallback_start_latest_at=fallback_latest)


def stage_deadline(*, started_at: datetime, stage_seconds: int,
                   local_cutoff_at: datetime) -> datetime:
    if stage_seconds <= 0:
        raise ValueError("positive stage deadline required")
    return min(started_at + timedelta(seconds=stage_seconds), local_cutoff_at)
