"""Versioned local capture semantics; no provider/billing identity is inferred."""
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from typing import Annotated,Literal
from contracts.operations import Identifier,Sha256,UtcTimestamp

COUNTERS=('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens')
MAPPING_ID='claude_cli_result.execution.v1'
EXPORT_VERSION='bm04-evidence-v1'
Counter=Annotated[int,Field(strict=True,ge=0,le=9223372036854775807)]

class UsageSnapshot(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    event_id:Identifier
    payload_hash:Sha256
    observation_id:Identifier
    model_execution_id:Identifier
    occurred_at:UtcTimestamp
    producer_id:Identifier
    producer_incarnation:Identifier
    origin:Literal['observed','imported','synthetic']
    capture_completeness:Literal['complete','partial','unavailable','removed','unknown']
    unresolved:StrictBool=False
    scope:Identifier
    is_cumulative:StrictBool
    provenance:Identifier
    input_tokens:Counter|None=None
    output_tokens:Counter|None=None
    cache_read_input_tokens:Counter|None=None
    cache_creation_input_tokens:Counter|None=None
