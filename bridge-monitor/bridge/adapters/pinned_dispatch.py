"""Opt-in BM-07 paid ingress adapter; no worker, lease claim or runtime registration."""
import json

import psycopg

from bridge.adapters.codex_ingress import CodexIngress
from contracts.pinned_context import PinnedContext
from contracts.work import AdmissionDecision, CallRequest


def prepare_pinned_prompt(
    *, request: CallRequest, context: PinnedContext, expected_context_sha256: str,
    prompt: str, max_context_bytes: int, max_prompt_bytes: int,
) -> str:
    """Verify external identity and current bounds, then encode instruction/data."""
    for limit in (max_context_bytes, max_prompt_bytes):
        if type(limit) is not int or limit <= 0:
            raise ValueError('byte budget must be a positive integer')
    if max_context_bytes > 262144:
        raise ValueError('context byte budget exceeds hard cap')
    request = CallRequest.model_validate(request.model_dump())
    context = PinnedContext.model_validate(context)
    if context.context_sha256 != expected_context_sha256:
        raise ValueError('expected context digest mismatch')
    if (context.task_id, context.work_item_id) != (request.task.task_id, request.task.work_item_id):
        raise ValueError('context/call identity mismatch')
    if sum(item.pin.byte_length for item in context.items) > max_context_bytes:
        raise ValueError('context byte budget exceeded')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('nonblank instruction required')
    if len(prompt.encode('utf-8')) > max_prompt_bytes:
        raise ValueError('prompt byte budget exceeded')
    rendered = json.dumps({'instruction': prompt, 'pinned_context': context.model_dump(mode='json')},
                          sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    if len(rendered.encode('utf-8')) > max_prompt_bytes:
        raise ValueError('prompt byte budget exceeded')
    return rendered


def submit_pinned_context(
    ingress: CodexIngress, request: CallRequest, *, context: PinnedContext,
    expected_context_sha256: str, prompt: str, max_context_bytes: int, reserve_tokens: int,
) -> AdmissionDecision:
    """Capture and queue through existing ingress. Never claim or launch work.

    Caller explicitly selects the existing paid profile; no provider selection
    or fallback is performed. An unregistered API, not a live dispatch switch.
    """
    request = CallRequest.model_validate(request.model_dump())
    if (type(reserve_tokens) is not int or reserve_tokens <= 0
            or reserve_tokens > request.task.token_budget):
        raise ValueError('invalid token reservation')
    policy = ingress.refresh_policy()
    if request.requested_model != policy.fallback.paid_model_profile:
        raise ValueError('requested model must match reviewed paid profile')
    rendered = prepare_pinned_prompt(
        request=request, context=context, expected_context_sha256=expected_context_sha256,
        prompt=prompt, max_context_bytes=max_context_bytes,
        max_prompt_bytes=policy.deployment.payload_limit_bytes,
    )
    # Reuse a previously captured exact prompt on retries. Existing admission
    # remains the authority for call conflicts, durable budgets and queue state.
    with psycopg.connect(ingress.dsn) as conn:
        prior = conn.execute('SELECT j.prompt_content_id,c.raw_bytes FROM runtime_jobs j '
                             'JOIN content_objects c ON c.content_id=j.prompt_content_id '
                             'WHERE j.call_id=%s', (request.call_id,)).fetchone()
    if prior and bytes(prior[1]) != rendered.encode('utf-8'):
        raise ValueError('existing call prompt mismatch')
    return ingress.submit(
        request, prompt=rendered, pool='claude', reserve_tokens=reserve_tokens,
        captured_content_id=prior[0] if prior else None,
        expected_policy_version=policy.version,
    )
