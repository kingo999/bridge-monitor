"""Strict wire contracts and durable records, independent of Codex sessions."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator


class WorkerError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, validate_assignment=True)


ID = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')]
Text = Annotated[str, Field(min_length=1, max_length=8192)]
Context = Annotated[str, Field(max_length=49152)]
Hash = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class Versioned(StrictModel):
    schema_version: Literal[1] = 1

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('schema version must be integer')
        return value


class Endpoint(StrictModel):
    host: str
    base_url: str


class SourceApproval(StrictModel):
    classification: Literal['synthetic', 'approved_repository']
    approval_ref: Text
    audit_content_approved: bool

    @field_validator('audit_content_approved')
    @classmethod
    def approved(cls, value):
        if not value:
            raise ValueError('explicit content audit approval required')
        return value


class AcceptanceCheck(StrictModel):
    check_id: ID
    kind: Literal['compile', 'behaviour', 'source']
    contract: Text
    method: Text


class TaskPacket(Versioned):
    task_id: ID
    category: Literal['isolated_repair', 'mechanical_transformation', 'supplied_code_explanation']
    objective: Text
    constraints: Text
    source: Annotated[str, Field(min_length=1, max_length=49152)]
    context: Context
    diagnostic: Context = ''
    versions: dict[ID, Text]
    references: Annotated[list[Text], Field(max_length=32)] = Field(default_factory=list)
    output_form: Text
    context_id: ID
    approval: SourceApproval
    checks: Annotated[list[AcceptanceCheck], Field(min_length=1, max_length=32)]

    @model_validator(mode='after')
    def sufficient(self):
        if not self.context.strip() or not self.source.strip():
            raise ValueError('focused source and caller context required')
        if len(self.versions) > 32 or len({c.check_id for c in self.checks}) != len(self.checks):
            raise ValueError('bounded versions and unique checks required')
        kinds = {c.kind for c in self.checks}
        if self.category == 'supplied_code_explanation':
            if 'source' not in kinds:
                raise ValueError('independent source checks required')
        elif not {'compile', 'behaviour'} <= kinds:
            raise ValueError('independent compiler and behaviour checks required')
        if len(self.model_dump_json().encode()) > 131072:
            raise ValueError('packet too large')
        return self


class MeasuredEffort(StrictModel):
    preparation_s: float | None = None
    validation_s: float | None = None
    review_s: float | None = None
    human_active_s: float | None = None
    human_measurement_ref: Text | None = None
    premium_tokens: Annotated[int, Field(ge=0)] | None = None
    receipt_ref: Text | None = None

    @model_validator(mode='after')
    def measured(self):
        for name in ('preparation_s', 'validation_s', 'review_s', 'human_active_s'):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError('invalid interval')
        if self.human_active_s is not None and not self.human_measurement_ref:
            raise ValueError('human measurement reference required')
        if self.premium_tokens is not None and not self.receipt_ref:
            raise ValueError('receipt required')
        return self


class CheckObservation(StrictModel):
    check_id: ID
    passed: bool
    evidence_ref: Text
    observed: Text


class ValidationReport(Versioned):
    task_id: ID
    attempt: Annotated[int, Field(ge=1, le=2)]
    packet_hash: Hash
    result_digest: Hash
    verdict: Literal['accepted', 'failed', 'unverifiable']
    observations: Annotated[list[CheckObservation], Field(max_length=32)]
    diagnostics: Context = ''
    toolchain_ref: Text | None = None
    supervisor_edits: Context = ''
    effort: MeasuredEffort = Field(default_factory=MeasuredEffort)


class StatusRequest(Versioned):
    action: Literal['status']


class ReconnectRequest(Versioned):
    action: Literal['reconnect']
    address: Annotated[str, Field(max_length=253)] | None = None


class ContinueWithoutRequest(Versioned):
    action: Literal['continue_without']


class GenerateRequest(Versioned):
    action: Literal['generate']
    packet: TaskPacket
    attempt: Annotated[int, Field(ge=1, le=2)]
    feedback_ref: Hash | None = None
    effort: MeasuredEffort = Field(default_factory=MeasuredEffort)


class RecordValidationRequest(Versioned):
    action: Literal['record_validation']
    report: ValidationReport


LocalWorkerRequest = Annotated[StatusRequest | ReconnectRequest | ContinueWithoutRequest | GenerateRequest | RecordValidationRequest, Field(discriminator='action')]
REQUEST_ADAPTER = TypeAdapter(LocalWorkerRequest)


class LocalWorkerResult(Versioned):
    action: str
    worker_id: Literal['LOCAL-00'] = 'LOCAL-00'
    state: Literal['status_returned', 'draft_returned', 'unavailable', 'busy', 'incomplete_error']
    code: str
    explanation: str = ''
    availability: Literal['discovering', 'available', 'unavailable', 'disabled'] = 'unavailable'
    coordination: Literal['idle', 'busy', 'uncertain'] = 'idle'
    validation: Literal['pending', 'accepted', 'failed', 'unverifiable'] = 'pending'
    next_actions: list[str] = Field(default_factory=list)
    task_id: str | None = None
    packet_hash: str | None = None
    request_id: str | None = None
    attempt: int | None = None
    endpoint: str | None = None
    peer_ip: str | None = None
    model: str | None = None
    digest: str | None = None
    result_digest: str | None = None
    response: str | None = None
    done: bool | None = None
    done_reason: str | None = None
    metrics: dict[str, int | float | None] = Field(default_factory=dict)
    timings: dict[str, float | None] = Field(default_factory=dict)
    audit_refs: list[str] = Field(default_factory=list)
    replayed_from_ledger: bool = False
    needs_context: bool = False
    evidence_origin: Literal['supervisor_reported'] | None = None


class WorkerSnapshot(Versioned):
    availability: Literal['discovering', 'available', 'unavailable'] = 'unavailable'
    coordination: Literal['idle', 'busy', 'uncertain'] = 'idle'
    endpoint: Endpoint | None = None
    peer_ip: str | None = None
    budget_block: str | None = None
    connection_revision: int = 0
    candidate_results: list[dict] = Field(default_factory=list)


class AttemptRecord(Versioned):
    request_id: str
    attempt: int
    result: LocalWorkerResult | None = None
    retired: bool = False


class TaskRecord(Versioned):
    packet: TaskPacket
    packet_hash: Hash
    attempts: dict[str, AttemptRecord] = Field(default_factory=dict)
    validations: list[ValidationReport] = Field(default_factory=list)


class ActiveRequest(Versioned):
    request_id: str
    task_id: ID
    attempt: int
    code: str = 'active'


class BudgetProof(Versioned):
    model_digest: str
    template_hash: Hash
    metadata_hash: Hash
    server_version: str
    rendered_prefix: str
    rendered_suffix: str
    token_allowance: int
    method: Literal['verified_utf8_bpe'] = 'verified_utf8_bpe'
    provenance: str


class PreparedPrompt(StrictModel):
    text: str
    prompt_hash: Hash
    proof_hash: Hash
    upper_bound: int
    template_version: str = 'local00-v1'


class TransportRequest(StrictModel):
    endpoint: Endpoint
    method: Literal['GET', 'POST']
    path: str
    body: dict | None = None
    timeout_s: float = 5.0
    max_bytes: int = 1048576


class TransportOutcome(StrictModel):
    phase: Literal['not_sent', 'may_have_started', 'terminal_received'] = 'not_sent'
    code: str = 'ok'
    status: int | None = None
    peer_ip: str | None = None
    body_b64: str = ''
    data: dict | None = None
    elapsed_s: float = 0.0
    truncated: bool = False


def canonical_json(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def content_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def canonical_packet_hash(packet: TaskPacket) -> str:
    return content_hash(packet)
