import base64
from typing import Any

from fastapi import HTTPException

from contracts.content import ContentObject
from contracts.events import EventEnvelope
from service.ingest.store import ingest_batch


def accept_batch(dsn: str, producer_id: str, body: dict[str, Any], *, payload_limit_bytes: int) -> dict:
    try:
        events = [EventEnvelope.model_validate(item) for item in body.get("events", [])]
        content = []
        for item in body.get("content", []):
            fields = dict(item)
            raw = base64.b64decode(fields.pop("raw_base64"), validate=True)
            if len(raw) > payload_limit_bytes:
                raise HTTPException(status_code=413, detail="content exceeds configured payload limit")
            content.append(ContentObject.from_bytes(fields.pop("content_id"), raw, **fields))
        return {"receipts": ingest_batch(dsn, producer_id, events, content)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
