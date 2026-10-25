"""Opt-in coordination within the existing route_work entry point."""
import hashlib
import json
import uuid
import psycopg
from pydantic import ValidationError
from bridge.router.codex_claude import prepare_codex_claude_route
from bridge.runtime.codex_claude import (record_codex_claude_decision,
    reserve_codex_claude_call, release_codex_claude_call, claim_codex_claude_dispatch)

def route_codex_claude(coordinator, task, prompt, *, requested_model, reserve_tokens,
                      output_contract, verifier_ref=None, source_id=None):
    policy=coordinator.policy.codex_claude
    if source_id:
        return {"state":"keep_owner","reason":"owner_tools_required"}
    try:
        decision=prepare_codex_claude_route(policy,task,prompt,coordinator.claude_adapter)
    except (ValidationError,TypeError,ValueError):
        return {"state":"rejected","reason":"invalid_routing_facts"}
    binding=hashlib.sha256(json.dumps({"prompt":prompt,"output_contract":output_contract,
        "verifier_ref":verifier_ref,"requested_model":requested_model,"reserve_tokens":reserve_tokens},
        sort_keys=True).encode()).hexdigest()
    try:
        decision_id=record_codex_claude_decision(coordinator.dsn,task_id=task.task_id,
            work_item_id=task.work_item_id,prompt_sha256=binding,decision=decision)
    except ValueError:
        return {"state":"rejected","reason":"decision_identity_conflict"}
    answer={"decision_id":decision_id,"decision":decision.model_dump(mode="json")}
    if decision.action!="claude":
        return {**answer,"state":decision.action,"reason":decision.reason}
    profile=policy.profiles[decision.selected_profile]
    adapter=coordinator.claude_adapter
    if not callable(getattr(adapter,"run_profile",None)):
        return {**answer,"state":"rejected","reason":"profile_adapter_unavailable"}
    call_id=str(uuid.uuid5(uuid.NAMESPACE_URL,"cc-call:"+task.work_item_id))
    held=reserve_codex_claude_call(coordinator.dsn,call_id=call_id,work_item_id=task.work_item_id,
        workflow_id=task.task_id,profile=decision.selected_profile,amount=profile.reference_usd,policy=policy)
    if held["state"]=="rejected":return {**answer,**held}
    if held["state"] in ("settled","released"):
        return {**answer,**coordinator.get_execution_status(call_id)}
    if not claim_codex_claude_dispatch(coordinator.dsn,call_id):
        with psycopg.connect(coordinator.dsn) as conn:
            existing=conn.execute("SELECT 1 FROM runtime_jobs WHERE call_id=%s",(call_id,)).fetchone()
        if existing:return {**answer,**coordinator.get_execution_status(call_id)}
        return {**answer,"state":"uncertain","call_id":call_id,"reason":"dispatch_pending_or_unreconciled"}
    result=coordinator.delegate_claude(task,prompt,requested_model=profile.model_id,
        reserve_tokens=reserve_tokens,_call_id=call_id,_expected_policy_version=coordinator.policy_version,
        _codex_claude_profile=decision.selected_profile)
    if result["state"] in ("rejected","queue_expired"):
        with psycopg.connect(coordinator.dsn) as conn:
            job=conn.execute("SELECT state,launch_claimed_at FROM runtime_jobs WHERE call_id=%s",(call_id,)).fetchone()
        if job is None or (job[0]=="cancelled" and job[1] is None):
            release_codex_claude_call(coordinator.dsn,call_id,termination_confirmed=True,no_charge_confirmed=True)
    return {**answer,**result}
