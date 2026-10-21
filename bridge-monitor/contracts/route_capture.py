"""Primitive-only arrival snapshot shared with an optional observer."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


def valid_metadata_text(value, *, allow_empty=False) -> bool:
    # Bounded short identifiers only; never encode or scan request/source bodies here.
    return (type(value) is str and (allow_empty or len(value)>0) and len(value)<=256
            and '\x00' not in value
            and all(not 0xD800<=ord(char)<=0xDFFF for char in value))


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

    def valid_binding(self) -> bool:
        # Cheap bounds only: no serialization, hashing or I/O on the caller thread.
        ids = (self.task_id, self.work_item_id, self.category, self.case_task_id,
               self.origin_ref, self.output_contract, self.case_output_contract,
               self.decision_id, self.paid_call_id)
        return (all(valid_metadata_text(v) for v in ids)
            and type(self.prompt) is str and type(self.source) is str
            and 0 < len(self.prompt) + len(self.source)
            and self.case_prompt == self.prompt and self.case_task_id == self.task_id
            and self.output_contract == self.case_output_contract == 'complete_python_module_v1'
            and self.category == 'single_file_python_fix'
            and type(self.scope) is tuple and 0 < len(self.scope) <= 8
            and all(valid_metadata_text(v) for v in self.scope)
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

    def acceptable(self) -> bool:
        return self.valid_binding() and len(self.prompt) + len(self.source) <= 24576


class RouteObserver(Protocol):
    def offer(self, snapshot: RouteCapture) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class BridgeCapture:
    """Prompt-only parent ingress. No repository, answer, or caller context is copied."""
    task_id: str
    work_item_id: str
    prompt: str
    method: str
    paid_call_id: str
    decision_id: str
    requested_model: str
    captured_at: datetime
    origin_kind: str = 'natural'
    verifier_ref: str | None = None
    output_contract: str = ''

    def valid_binding(self) -> bool:
        ids = (self.task_id, self.work_item_id, self.paid_call_id,
               self.decision_id, self.requested_model)
        return (all(valid_metadata_text(v) for v in ids)
            and type(self.prompt) is str and 0 < len(self.prompt)
            and self.method in ('route_work', 'delegate_claude')
            and self.origin_kind in ('natural', 'controlled')
            and (self.verifier_ref is None or
                 valid_metadata_text(self.verifier_ref,allow_empty=True))
            and valid_metadata_text(self.output_contract,allow_empty=True)
            and self.captured_at.utcoffset() is not None)

    def acceptable(self) -> bool:
        return self.valid_binding() and len(self.prompt) <= 24576
