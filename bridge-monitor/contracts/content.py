import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContentObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: str = Field(min_length=1)
    raw_bytes: bytes | None = None
    byte_length: int = Field(ge=0)
    sha256: str | None = None
    role: str
    direction: str
    content_type: str
    encoding: str = "utf-8"
    origin: Literal["observed", "imported", "synthetic"]
    capture_layer: str
    completeness: Literal["complete", "partial", "unavailable", "removed", "unknown"] = "complete"
    message_id: str | None = None
    block_order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_bytes(self):
        if self.raw_bytes is not None:
            if self.byte_length != len(self.raw_bytes):
                raise ValueError("byte length mismatch")
            if self.sha256 != hashlib.sha256(self.raw_bytes).hexdigest():
                raise ValueError("content hash mismatch")
        if self.completeness == "complete" and self.raw_bytes is None:
            raise ValueError("complete content needs exact bytes")
        return self

    @classmethod
    def from_bytes(cls, content_id: str, raw: bytes, **fields):
        return cls(content_id=content_id, raw_bytes=raw, byte_length=len(raw),
                   sha256=hashlib.sha256(raw).hexdigest(), **fields)


class ContentBatch(BaseModel):
    objects: list[ContentObject] = Field(default_factory=list)
