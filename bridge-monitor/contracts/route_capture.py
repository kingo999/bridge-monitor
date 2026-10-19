"""Primitive-only arrival snapshot shared with an optional observer."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RouteCapture:
    task_id: str
    work_item_id: str
    category: str
    scope: tuple[str, ...]
    scope_approved: bool
    prompt: str
    case_task_id: str
    case_prompt: str
    source: str
    case_digest: str
    origin_kind: str
    origin_ref: str
    output_contract: str
    case_output_contract: str
    decision_id: str
    paid_call_id: str
    captured_at: datetime
    registered_at: datetime
    solution_available_at: datetime | None
    tests_present: bool
    case_validated: bool

    def acceptable(self) -> bool:
        # Cheap bounds only: no serialization, hashing or I/O on the caller thread.
        ids = (self.task_id, self.work_item_id, self.category, self.case_task_id,
               self.origin_ref, self.output_contract, self.case_output_contract,
               self.decision_id, self.paid_call_id)
        return (all(type(v) is str and 0 < len(v) <= 256 for v in ids)
            and type(self.prompt) is str and type(self.source) is str
            and 0 < len(self.prompt) + len(self.source) <= 24576
            and self.case_prompt == self.prompt and self.case_task_id == self.task_id
            and self.output_contract == self.case_output_contract == 'complete_python_module_v1'
            and self.category == 'single_file_python_fix'
            and type(self.scope) is tuple and 0 < len(self.scope) <= 8
            and all(type(v) is str and 0 < len(v) <= 256 for v in self.scope)
            and self.scope_approved is True and self.case_validated is True
            and self.origin_kind in ('natural', 'controlled')
            and type(self.case_digest) is str and len(self.case_digest) == 64
            and all(c in '0123456789abcdef' for c in self.case_digest)
            and self.captured_at.utcoffset() is not None
            and self.registered_at.utcoffset() is not None
            and self.registered_at <= self.captured_at
            and (self.solution_available_at is None
                 or (self.solution_available_at.utcoffset() is not None
                     and self.solution_available_at > self.captured_at)))


class RouteObserver(Protocol):
    def offer(self, snapshot: RouteCapture) -> None: ...
    def close(self) -> None: ...
