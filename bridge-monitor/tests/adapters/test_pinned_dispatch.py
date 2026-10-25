from types import SimpleNamespace

import pytest

from bridge.adapters.pinned_dispatch import prepare_pinned_prompt, submit_pinned_context
from tests.contracts.test_pinned_context import build
from tests.integration.test_admission import _call, _policy


def request():
    call = _call('task', 'call')
    call.task.work_item_id = 'work'
    return call


def prepare(**overrides):
    bundle = build()
    args = dict(request=request(), context=bundle, expected_context_sha256=bundle.context_sha256,
                prompt='Analyze the supplied evidence', max_context_bytes=1024, max_prompt_bytes=10000)
    return prepare_pinned_prompt(**(args | overrides))


def test_prompt_contains_exact_validated_bundle_and_instruction():
    import json
    body = json.loads(prepare())
    assert body['instruction'] == 'Analyze the supplied evidence'
    assert body['pinned_context']['context_sha256'] == build().context_sha256


def test_self_consistent_replacement_is_rejected_against_external_pin():
    other = build(task_id='other')
    with pytest.raises(ValueError, match='expected context'):
        prepare(context=other)


@pytest.mark.parametrize('field', ['task_id', 'work_item_id'])
def test_context_must_match_call_identity(field):
    call = request()
    setattr(call.task, field, 'other')
    with pytest.raises(ValueError, match='identity'):
        prepare(request=call)


def test_current_context_budget_and_whole_prompt_cap_are_checked():
    with pytest.raises(ValueError, match='context.*budget'):
        prepare(max_context_bytes=1)
    exact = prepare()
    assert prepare(max_prompt_bytes=len(exact.encode())) == exact
    with pytest.raises(ValueError, match='prompt.*budget'):
        prepare(max_prompt_bytes=len(exact.encode()) - 1)


@pytest.mark.parametrize('field', ['max_context_bytes', 'max_prompt_bytes'])
@pytest.mark.parametrize('value', [True, 0, -1, '1024'])
def test_byte_budgets_are_strict(field, value):
    with pytest.raises(ValueError):
        prepare(**{field: value})


def test_invalid_context_is_revalidated():
    bundle = build().model_copy(update={'context_sha256': '0' * 64})
    with pytest.raises(ValueError):
        prepare(context=bundle)


@pytest.mark.parametrize('reserve', [True, 0, -1, '1', 1001])
def test_invalid_reservation_rejected_before_ingress(reserve):
    ingress = SimpleNamespace(refresh_policy=lambda: pytest.fail('ingress accessed'))
    with pytest.raises(ValueError):
        submit_pinned_context(ingress, request(), context=build(),
            expected_context_sha256=build().context_sha256, prompt='Analyze',
            max_context_bytes=1024, reserve_tokens=reserve)


def test_unapproved_model_never_submits():
    ingress = SimpleNamespace(refresh_policy=lambda: _policy(),
                              submit=lambda *a, **kw: pytest.fail('submitted'))
    call = request()
    call.requested_model = 'different-profile'
    with pytest.raises(ValueError, match='model'):
        submit_pinned_context(ingress, call, context=build(),
            expected_context_sha256=build().context_sha256, prompt='Analyze',
            max_context_bytes=1024, reserve_tokens=1)
