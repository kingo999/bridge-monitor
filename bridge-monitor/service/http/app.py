from pathlib import Path
from contextlib import asynccontextmanager
import re

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from service.http.auth import (COOKIE_NAME, LOCAL_TEST_CREDENTIAL, authenticated_producer,
                               bearer, create_session, matches_local_test_login,
                               require_browser_operator, require_operator)
from service.http.ingest import accept_batch
from service.http.query import query_router, validate_design_mode_owner_id
from service.http.stream import stream_router
from service.ingest.store import read_content


def create_app(dsn: str, *, producer_tokens: dict[str, str], operator_token: str,
               payload_limit_bytes: int, dashboard_dir: str | Path | None = None,
               session_ttl_seconds: int = 3600, secure_cookies: bool = False,
               preview_max_chunks: int = 80, preview_max_bytes: int = 16384,
               timeline_head_events: int = 100, timeline_tail_events: int = 400,
               work_item_max_limit: int = 100,
               subscription_producer_ids: frozenset[str] = frozenset(),
               design_mode_owner_id: str | None = None,
               classifier_config_path: str | None = None,
               local_test_login_origin: str | None = None) -> FastAPI:
    design_mode_owner_id = validate_design_mode_owner_id(design_mode_owner_id)
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
    if local_test_login_origin is not None:
        origin = re.fullmatch(r"https?://127\.0\.0\.1(?::([1-9][0-9]{0,4}))?",
                              local_test_login_origin)
        if origin is None or int(origin[1] or 1) > 65535:
            raise ValueError("local test login requires a literal loopback origin")
        if LOCAL_TEST_CREDENTIAL in tokens:
            raise ValueError("local test login must use a distinct credential")
    if session_ttl_seconds <= 0:
        raise ValueError("session TTL must be positive")
    if not (1 <= preview_max_chunks <= 500 and 1 <= preview_max_bytes <= 65536):
        raise ValueError("output preview limits out of bounds")
    if timeline_head_events < 1 or timeline_tail_events < 1 or timeline_head_events + timeline_tail_events > 2000:
        raise ValueError("timeline event window out of bounds")
    if not 100 <= work_item_max_limit <= 2000:
        raise ValueError("work-item query limit out of bounds")
    from bridge.runtime.queue_maintenance import QueueExpiryMonitor
    queue_monitor = QueueExpiryMonitor(dsn)
    from bridge.audit_ui_worker import AuditIndexer
    audit_indexer = AuditIndexer(dsn)

    @asynccontextmanager
    async def lifespan(app):
        queue_monitor.start()
        audit_indexer.start()
        try:
            yield
        finally:
            import asyncio
            await asyncio.to_thread(queue_monitor.close)
            await asyncio.to_thread(audit_indexer.close)

    app = FastAPI(title="Bridge Monitor Capture", version="0.1.0", lifespan=lifespan)
    app.state.queue_monitor = queue_monitor
    sessions: dict[str, float] = {}
    if not subscription_producer_ids.issubset(producer_tokens):
        raise ValueError("subscription producers must have existing authenticated producer credentials")

    @app.get("/api/v1/queue-maintenance")
    def queue_maintenance(request: Request, authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        return queue_monitor.snapshot()

    @app.post("/api/v1/subscription-usage")
    def subscription_usage(body: dict, authorization: str | None = Header(default=None)):
        from contracts.codex_claude import SubscriptionObservation
        from service.queries.usage_notices import record_subscription_observation
        from pydantic import ValidationError
        producer = authenticated_producer(bearer(authorization), producer_tokens)
        if producer not in subscription_producer_ids:
            raise HTTPException(403, "Trusted provider telemetry producer required")
        try:
            observation = SubscriptionObservation.model_validate(body)
            return record_subscription_observation(dsn, observation, producer_id=producer,
                                                   trusted_producers=subscription_producer_ids)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(422, "Invalid or conflicting subscription observation") from exc

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
    def login(body: dict, request: Request, response: Response):
        token = str(body.get("operator_token", ""))
        if not matches_local_test_login(request, token, local_test_login_origin):
            require_operator(token, operator_token)
        identifier = create_session(sessions, session_ttl_seconds)
        response.set_cookie(COOKIE_NAME, identifier, httponly=True, secure=secure_cookies,
                            samesite="strict", max_age=session_ttl_seconds, path="/")
        return {"authenticated": True, "expires_in_seconds": session_ttl_seconds,
                "work_item_max_limit": work_item_max_limit}

    @app.get("/api/v1/session")
    def session_status(request: Request):
        try:
            require_browser_operator(request, None, operator_token, sessions)
        except HTTPException:
            return {"authenticated": False}
        return {"authenticated": True, "work_item_max_limit": work_item_max_limit}

    @app.get("/api/v1/services")
    def services(request: Request, authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        from service.queries.service_availability import service_report
        return service_report(dsn, classifier_config_path)

    @app.post("/api/v1/session/logout", status_code=204)
    def logout(request: Request, response: Response):
        identifier = request.cookies.get(COOKIE_NAME)
        if identifier:
            sessions.pop(identifier, None)
        response.delete_cookie(COOKIE_NAME, path="/")

    app.include_router(query_router(dsn, operator_token, sessions,
                                    preview_max_chunks=preview_max_chunks,
                                    preview_max_bytes=preview_max_bytes,
                                    timeline_head_events=timeline_head_events,
                                    timeline_tail_events=timeline_tail_events,
                                    work_item_max_limit=work_item_max_limit,
                                    design_mode_owner_id=design_mode_owner_id))
    app.include_router(stream_router(dsn, operator_token, sessions))
    from service.http.audit import audit_router
    app.include_router(audit_router(dsn, operator_token, sessions))

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
