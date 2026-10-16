from fastapi import FastAPI, Header, HTTPException, Response

from service.http.auth import bearer, authenticated_producer, require_operator
from service.http.ingest import accept_batch
from service.ingest.store import read_content


def create_app(dsn: str, *, producer_tokens: dict[str, str], operator_token: str,
               payload_limit_bytes: int) -> FastAPI:
    if payload_limit_bytes <= 0:
        raise ValueError("a positive configured payload limit is required")
    app = FastAPI(title="Bridge Monitor Capture", version="0.1.0")

    @app.post("/api/v1/events/batch")
    def events_batch(body: dict, authorization: str | None = Header(default=None)):
        producer = authenticated_producer(bearer(authorization), producer_tokens)
        return accept_batch(dsn, producer, body, payload_limit_bytes=payload_limit_bytes)

    @app.get("/api/v1/content/{content_id}")
    def content_body(content_id: str, authorization: str | None = Header(default=None)):
        require_operator(bearer(authorization), operator_token)
        raw = read_content(dsn, content_id, role="operator")
        if raw is None:
            raise HTTPException(status_code=404, detail="content unavailable")
        return Response(raw, media_type="application/octet-stream")

    return app
