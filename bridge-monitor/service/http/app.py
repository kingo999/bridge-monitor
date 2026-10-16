from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from service.http.auth import (COOKIE_NAME, authenticated_producer, bearer, create_session,
                               require_browser_operator, require_operator)
from service.http.ingest import accept_batch
from service.http.query import query_router
from service.http.stream import stream_router
from service.ingest.store import read_content


def create_app(dsn: str, *, producer_tokens: dict[str, str], operator_token: str,
               payload_limit_bytes: int, dashboard_dir: str | Path | None = None,
               session_ttl_seconds: int = 3600, secure_cookies: bool = False) -> FastAPI:
    if payload_limit_bytes <= 0:
        raise ValueError("a positive configured payload limit is required")
    if (not isinstance(operator_token, str) or not operator_token.strip()
            or not producer_tokens or any(not isinstance(producer, str) or not producer.strip()
                                          or not isinstance(token, str) or not token.strip()
                                          for producer, token in producer_tokens.items())):
        raise ValueError("nonempty producer and operator credentials are required")
    tokens = [operator_token, *producer_tokens.values()]
    if len(tokens) != len(set(tokens)):
        raise ValueError("producer and operator credentials must be distinct")
    if session_ttl_seconds <= 0:
        raise ValueError("session TTL must be positive")
    app = FastAPI(title="Bridge Monitor Capture", version="0.1.0")
    sessions: dict[str, float] = {}

    @app.post("/api/v1/events/batch")
    def events_batch(body: dict, authorization: str | None = Header(default=None)):
        producer = authenticated_producer(bearer(authorization), producer_tokens)
        return accept_batch(dsn, producer, body, payload_limit_bytes=payload_limit_bytes)

    @app.get("/api/v1/content/{content_id}")
    def content_body(content_id: str, request: Request,
                     authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        raw = read_content(dsn, content_id, role="operator")
        if raw is None:
            raise HTTPException(status_code=404, detail="content unavailable")
        return Response(raw, media_type="application/octet-stream")

    @app.post("/api/v1/session")
    def login(body: dict, response: Response):
        require_operator(str(body.get("operator_token", "")), operator_token)
        identifier = create_session(sessions, session_ttl_seconds)
        response.set_cookie(COOKIE_NAME, identifier, httponly=True, secure=secure_cookies,
                            samesite="strict", max_age=session_ttl_seconds, path="/")
        return {"authenticated": True, "expires_in_seconds": session_ttl_seconds}

    @app.get("/api/v1/session")
    def session_status(request: Request):
        try:
            require_browser_operator(request, None, operator_token, sessions)
        except HTTPException:
            return {"authenticated": False}
        return {"authenticated": True}

    @app.post("/api/v1/session/logout", status_code=204)
    def logout(request: Request, response: Response):
        identifier = request.cookies.get(COOKIE_NAME)
        if identifier:
            sessions.pop(identifier, None)
        response.delete_cookie(COOKIE_NAME, path="/")

    app.include_router(query_router(dsn, operator_token, sessions))
    app.include_router(stream_router(dsn, operator_token, sessions))

    if dashboard_dir is not None:
        export = Path(dashboard_dir).resolve()
        if not (export / "index.html").is_file():
            raise ValueError("dashboard export must contain index.html")
        if (export / "_next").is_dir():
            app.mount("/_next", StaticFiles(directory=export / "_next"), name="next-assets")

        @app.get("/")
        def dashboard_index():
            return FileResponse(export / "index.html", headers={"Cache-Control": "no-store"})

    return app
