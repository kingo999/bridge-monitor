"""Opt-in pure materialization of explicitly pinned, supplied ContentObjects."""
from typing import Annotated

from pydantic import Field, TypeAdapter

from contracts.catalogue import ModuleManifest
from contracts.content import ContentObject
from contracts.pinned_context import (
    ContextPin, PinnedContext, PinnedContextItem, context_sha256, manifest_sha256,
)


_PINS = TypeAdapter(Annotated[tuple[ContextPin, ...], Field(min_length=1, max_length=32)])


def build_pinned_context(
    *, task_id: str, work_item_id: str, manifest: ModuleManifest,
    expected_manifest_sha256: str, pins: list[ContextPin] | tuple[ContextPin, ...],
    content: list[ContentObject] | tuple[ContentObject, ...], max_bytes: int,
) -> PinnedContext:
    """Verify exact selection without searching, substituting, or truncating.

    Caller must obtain expected pins independently of this materialization.
    Digests establish byte identity, not source authenticity or permission.
    Limits: 32 selected items, 64 KiB/item, 256 KiB total, 256 supplied objects.
    """
    if type(max_bytes) is not int or not 0 < max_bytes <= 262144:
        raise ValueError('byte budget must be an integer from 1 to 262144')
    if type(pins) not in (list, tuple) or type(content) not in (list, tuple):
        raise ValueError('pins and content must be a plain list or tuple')
    if len(content) > 256:
        raise ValueError('too many supplied content objects')
    supplied_bytes = 0
    for item in content:
        if type(item) is not ContentObject:
            raise ValueError('supplied content must be ContentObject instances')
        if item.raw_bytes is not None:
            if type(item.raw_bytes) is not bytes:
                raise ValueError('supplied content must contain exact bytes')
            supplied_bytes += len(item.raw_bytes)
            if len(item.raw_bytes) > 65536 or supplied_bytes > 262144:
                raise ValueError('supplied content exceeds byte budget')
        if item.encoding not in ('utf-8', 'UTF-8', 'utf8'):
            raise ValueError('supplied encoding must be UTF-8')
    manifest = ModuleManifest.model_validate(manifest)
    digest = manifest_sha256(manifest)
    if digest != expected_manifest_sha256:
        raise ValueError('manifest pin mismatch')
    pins = _PINS.validate_python(pins)
    if len({pin.content_id for pin in pins}) != len(pins):
        raise ValueError('duplicate pin identity')
    if sum(pin.byte_length for pin in pins) > max_bytes:
        raise ValueError('selected context exceeds byte budget')

    supplied = {}
    for item in content:
        # ContentObject is mutable and does not revalidate instances. Round-trip
        # its fields through the existing validator rather than trusting it.
        item = ContentObject.model_validate(item.model_dump())
        if item.content_id in supplied:
            raise ValueError('duplicate content identity')
        supplied[item.content_id] = item

    selected = []
    for pin in pins:
        item = supplied.get(pin.content_id)
        if item is None:
            raise ValueError('missing pinned content')
        if item.completeness != 'complete' or item.raw_bytes is None:
            raise ValueError('complete content required')
        selected.append(PinnedContextItem(pin=pin, raw_bytes=item.raw_bytes))
    return PinnedContext(
        task_id=task_id, work_item_id=work_item_id, manifest=manifest,
        manifest_sha256=digest, items=tuple(selected),
        context_sha256=context_sha256(task_id, work_item_id, digest, pins),
    )
