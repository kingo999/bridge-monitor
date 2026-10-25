import builtins
import hashlib
import socket
import subprocess

import pytest
from pydantic import ValidationError

from contracts.catalogue import ModuleManifest
from contracts.content import ContentObject
from contracts.pinned_context import ContextPin, PinnedContext, PinnedContextItem, manifest_sha256
from service.queries.pinned_context import build_pinned_context


def manifest(summary='Worker'):
    return ModuleManifest(module_id='worker', manifest_version=1, summary=summary)


def content(name='source', body=b'focused evidence'):
    return ContentObject.from_bytes(name, body, role='source', direction='in',
                                    content_type='text/plain', origin='synthetic', capture_layer='test')


def pin(item):
    return ContextPin(content_id=item.content_id, sha256=item.sha256, byte_length=item.byte_length)


def build(items=None, pins=None, **overrides):
    items = [content()] if items is None else items
    args = dict(task_id='task', work_item_id='work', manifest=manifest(),
                expected_manifest_sha256=manifest_sha256(manifest()),
                pins=[pin(item) for item in items] if pins is None else pins,
                content=items, max_bytes=1024)
    return build_pinned_context(**(args | overrides))


def test_exact_content_manifest_and_task_are_pinned():
    result = build()
    assert result.task_id == 'task' and result.work_item_id == 'work'
    assert result.manifest_sha256 == manifest_sha256(manifest())
    assert result.items[0].raw_bytes == b'focused evidence'
    assert result.items[0].pin.sha256 == hashlib.sha256(b'focused evidence').hexdigest()
    assert result == PinnedContext.model_validate_json(result.model_dump_json())


def test_manifest_pin_has_stable_canonical_encoding():
    # SHA-256 of literal ASCII JSON with sorted keys and no insignificant spaces:
    # {"manifest_version":1,"module_id":"worker","summary":"Worker"}
    assert manifest_sha256(manifest()) == 'ef4052c854aa155b27c969bd7b31fbe75d2eea70ccdb7f7ff4feace9a87b33b4'


def test_context_pin_has_stable_version_one_golden_vector():
    assert build().context_sha256 == '20aa9c351d218736ba1f01ccc343ee176597fa421e7e160d757b2a4f587c55b9'


def test_only_explicit_selection_is_included_in_requested_order():
    a, b, unused = content('a', b'a'), content('b', b'b'), content('unused', b'ignore')
    result = build([unused, a, b], [pin(b), pin(a)])
    assert [item.pin.content_id for item in result.items] == ['b', 'a']


def test_input_order_does_not_change_pin_but_selection_order_does():
    a, b = content('a', b'a'), content('b', b'b')
    first = build([a, b], [pin(a), pin(b)])
    assert first == build([b, a], [pin(a), pin(b)])
    assert first.context_sha256 != build([a, b], [pin(b), pin(a)]).context_sha256


@pytest.mark.parametrize('changed', [{'task_id': 'other'}, {'work_item_id': 'other'}])
def test_task_identity_is_bound(changed):
    assert build().context_sha256 != build(**changed).context_sha256


def test_same_manifest_version_with_changed_content_rejected():
    assert manifest_sha256(manifest()) != manifest_sha256(manifest('Changed'))
    with pytest.raises(ValueError, match='manifest.*mismatch'):
        build(manifest=manifest('Changed'))


def test_source_with_same_id_but_changed_bytes_rejected():
    with pytest.raises(ValueError, match='pin.*mismatch'):
        build([content(body=b'changed')], [pin(content())])


def test_missing_source_rejected():
    with pytest.raises(ValueError, match='missing'):
        build([], [pin(content())])


def test_duplicate_sources_and_duplicate_pins_rejected():
    item = content()
    with pytest.raises(ValueError, match='duplicate content'):
        build([item, item], [pin(item)])
    with pytest.raises(ValueError, match='duplicate pin'):
        build([item], [pin(item), pin(item)])


def test_budget_uses_exact_byte_length_and_never_truncates():
    item = content(body='é'.encode())
    assert build([item], max_bytes=2).items[0].raw_bytes == item.raw_bytes
    with pytest.raises(ValueError, match='budget'):
        build([item], max_bytes=1)


@pytest.mark.parametrize('budget', [True, 0, -1, '1024', 262145])
def test_budget_is_strict_and_bounded(budget):
    with pytest.raises(ValueError):
        build(max_bytes=budget)


@pytest.mark.parametrize('state', ['partial', 'unavailable', 'removed', 'unknown'])
def test_incomplete_content_is_rejected(state):
    item = content().model_copy(update={'completeness': state})
    with pytest.raises(ValueError, match='complete'):
        build([item])


def test_invalid_constructed_source_is_revalidated():
    item = content().model_copy(update={'raw_bytes': b'tampered'})
    with pytest.raises(ValueError):
        build([item], [pin(content())])


def test_non_utf8_is_rejected():
    with pytest.raises(ValueError, match='UTF-8'):
        build([content(body=b'\xff')])


def test_empty_and_excessive_selection_are_rejected():
    with pytest.raises(ValueError):
        build([], [])
    items = [content(str(i), b'x') for i in range(33)]
    with pytest.raises(ValueError):
        build(items)


@pytest.mark.parametrize('field,value', [('context_sha256', '0' * 64),
                                          ('manifest_sha256', '0' * 64),
                                          ('task_id', 'different')])
def test_public_bundle_rejects_tampering(field, value):
    raw = build().model_dump()
    raw[field] = value
    with pytest.raises(ValidationError):
        PinnedContext.model_validate(raw)


def test_public_bundle_rejects_body_tampering():
    raw = build().model_dump()
    raw['items'][0]['raw_bytes'] = b'tampered'
    with pytest.raises(ValidationError):
        PinnedContext.model_validate(raw)


def test_output_is_frozen_and_runtime_fields_are_rejected():
    result = build()
    with pytest.raises(ValidationError):
        result.items[0].raw_bytes = b'changed'
    with pytest.raises(ValidationError):
        PinnedContext.model_validate(result.model_dump() | {'dispatch': 'run'})


def test_builder_is_pure_and_preserves_inputs(monkeypatch):
    item = content()
    before = item.model_dump_json()

    def forbidden(*args, **kwargs):
        pytest.fail('unexpected I/O')

    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    assert build([item]).items[0].raw_bytes == item.raw_bytes
    assert item.model_dump_json() == before


@pytest.mark.parametrize('changes', [
    {'sha256': 'A' * 64}, {'sha256': 'not-a-digest'},
    {'byte_length': True}, {'byte_length': 0}, {'byte_length': 65537},
    {'content_id': ''}, {'command': 'run'},
])
def test_pin_validation_is_strict(changes):
    with pytest.raises(ValidationError):
        ContextPin.model_validate(pin(content()).model_dump() | changes)


def test_pin_byte_length_is_independently_verified():
    wrong = pin(content()).model_copy(update={'byte_length': 1})
    with pytest.raises(ValueError, match='pin mismatch'):
        build(pins=[wrong])


def test_content_identity_changes_bundle_digest():
    assert build().context_sha256 != build([content('other-id')]).context_sha256


def test_manifest_content_changes_bundle_digest_when_explicitly_reapproved():
    other = manifest('Changed')
    changed = build(manifest=other, expected_manifest_sha256=manifest_sha256(other))
    assert build().context_sha256 != changed.context_sha256


def test_exact_whitespace_and_newlines_are_preserved():
    a, b = content(body=b'line\r\n'), content(body=b'line\n')
    assert build([a]).items[0].raw_bytes == b'line\r\n'
    assert build([a]).context_sha256 != build([b]).context_sha256


def test_missing_body_is_rejected():
    item = content().model_copy(update={'completeness': 'unavailable', 'raw_bytes': None})
    with pytest.raises(ValueError, match='complete'):
        build([item])


def test_per_item_and_total_byte_caps():
    exact = content(body=b'x' * 65536)
    assert len(build([exact], max_bytes=65536).items[0].raw_bytes) == 65536
    with pytest.raises(ValueError):
        build([content(body=b'x' * 65537)], max_bytes=262144)
    items = [content(str(i), b'x' * 65536) for i in range(4)]
    result = build(items, max_bytes=262144)
    assert sum(len(item.raw_bytes) for item in result.items) == 262144
    items.append(content('overflow', b'x'))
    with pytest.raises(ValueError, match='budget'):
        build(items, max_bytes=262144)


def test_public_bundle_enforces_hard_total_cap():
    item = PinnedContextItem(pin=pin(content(body=b'x' * 65536)), raw_bytes=b'x' * 65536)
    raw = build().model_dump()
    raw['items'] = [item.model_copy(update={'pin': item.pin.model_copy(update={'content_id': str(i)})})
                    for i in range(5)]
    with pytest.raises(ValidationError, match='budget'):
        PinnedContext.model_validate(raw)


def test_source_collection_bound():
    items = [content(str(i), b'x') for i in range(257)]
    with pytest.raises(ValueError, match='too many'):
        build(items, pins=[pin(items[0])])


def test_public_bundle_revalidates_constructed_nested_instances():
    result = build()
    invalid = result.items[0].model_copy(update={'raw_bytes': b'changed'})
    invalid_bundle = result.model_copy(update={'items': (invalid,)})
    with pytest.raises(ValidationError):
        PinnedContext.model_validate(invalid_bundle)


def test_bundle_schema_version_is_strict():
    for version in (True, 2, '1'):
        with pytest.raises(ValidationError):
            PinnedContext.model_validate(build().model_dump() | {'schema_version': version})


@pytest.mark.parametrize('body', [b'line\r\n', b'\xef\xbb\xbfBOM', b'a\x00b', 'é 🦊'.encode()])
def test_exact_utf8_json_transport(body):
    result = build([content(body=body)])
    restored = PinnedContext.model_validate_json(result.model_dump_json())
    assert restored == result and restored.items[0].raw_bytes == body


@pytest.mark.parametrize('argument', ['content', 'pins'])
def test_lazy_iterables_are_rejected_without_consumption(argument):
    def lazy():
        pytest.fail('lazy input was consumed')
        yield content()
    with pytest.raises(ValueError, match='list or tuple'):
        build(**{argument: lazy()})


def test_unselected_source_bytes_are_bounded_before_hashing(monkeypatch):
    oversized = content('unused', b'x' * 65537)
    with pytest.raises(ValueError, match='supplied.*budget'):
        build([content(), oversized], pins=[pin(content())])
    items = [content(str(i), b'x' * 65536) for i in range(5)]
    with pytest.raises(ValueError, match='supplied.*budget'):
        build(items, pins=[pin(items[0])])


def test_non_utf8_declared_encoding_is_rejected():
    item = content().model_copy(update={'encoding': 'latin-1'})
    with pytest.raises(ValueError, match='encoding'):
        build([item])
