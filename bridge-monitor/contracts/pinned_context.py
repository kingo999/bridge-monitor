"""Exact, caller-selected context snapshots; no retrieval or execution authority."""
import hashlib
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBytes, model_validator

from contracts.catalogue import ModuleManifest
from contracts.operations import Identifier, Sha256


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def manifest_sha256(manifest: ModuleManifest) -> str:
    """Pin manifest content, independently of the caller's version label."""
    manifest = ModuleManifest.model_validate(manifest)
    return _sha256_json(manifest.model_dump(mode='json'))


class _Frozen(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')


class ContextPin(_Frozen):
    content_id: Identifier
    sha256: Sha256
    byte_length: Annotated[int, Field(strict=True, gt=0, le=65536)]


class PinnedContextItem(_Frozen):
    pin: ContextPin
    raw_bytes: Annotated[StrictBytes, Field(min_length=1, max_length=65536)]

    @model_validator(mode='after')
    def _exact_bytes(self):
        if len(self.raw_bytes) != self.pin.byte_length or hashlib.sha256(self.raw_bytes).hexdigest() != self.pin.sha256:
            raise ValueError('content pin mismatch')
        try:
            self.raw_bytes.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('context must be UTF-8') from exc
        return self


def context_sha256(task_id: str, work_item_id: str, manifest_digest: str,
                   pins: tuple[ContextPin, ...]) -> str:
    """Versioned canonical envelope binds identity, manifest and ordered item pins."""
    return _sha256_json({
        'schema_version': 1, 'task_id': task_id, 'work_item_id': work_item_id,
        'manifest_sha256': manifest_digest,
        'pins': [pin.model_dump(mode='json') for pin in pins],
    })


class PinnedContext(_Frozen):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    task_id: Identifier
    work_item_id: Identifier
    manifest: ModuleManifest
    manifest_sha256: Sha256
    items: Annotated[tuple[PinnedContextItem, ...], Field(min_length=1, max_length=32)]
    context_sha256: Sha256

    @model_validator(mode='after')
    def _integrity(self):
        if manifest_sha256(self.manifest) != self.manifest_sha256:
            raise ValueError('manifest pin mismatch')
        pins = tuple(item.pin for item in self.items)
        if len({pin.content_id for pin in pins}) != len(pins):
            raise ValueError('duplicate pin identity')
        if sum(pin.byte_length for pin in pins) > 262144:
            raise ValueError('context exceeds hard byte budget')
        if context_sha256(self.task_id, self.work_item_id, self.manifest_sha256, pins) != self.context_sha256:
            raise ValueError('context pin mismatch')
        return self
