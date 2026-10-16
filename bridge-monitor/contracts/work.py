from pydantic import BaseModel, ConfigDict, Field


class TaskPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    work_item_id: str
    category: str
    scope: list[str]
    token_budget: int = Field(ge=0)
    call_budget: int = Field(ge=0)


class CallRequest(BaseModel):
    call_id: str
    task: TaskPacket
    requested_model: str | None = None
    parent_call_id: str | None = None
    parent_attempt_id: str | None = None


class ExecutionBinding(BaseModel):
    worker_instance_id: str
    producer_id: str
    producer_incarnation: str
    call_id: str
    attempt_id: str


class AdmissionDecision(BaseModel):
    work_item_id: str
    route: str
    state: str
    reason: str | None = None
    policy_version: str


class DispatchLease(BaseModel):
    lease_id: str
    call_id: str
    owner_incarnation: str
    fencing_token: int = Field(ge=1)
    policy_version: str


class FallbackTransition(BaseModel):
    child_call_id: str
    state: str
    recipient_call_id: str
    owner_execution_id: str | None = None
    reason: str
