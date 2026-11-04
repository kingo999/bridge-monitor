"""Operator-authenticated audit metadata API."""
import hashlib

import psycopg
from fastapi import APIRouter, Header, HTTPException, Request

from service.http.auth import COOKIE_NAME, require_browser_operator
from service.queries.audit import network_status, start_search, page_snapshot, SnapshotExpired


def audit_owner(request, authorization):
    value = authorization or request.cookies.get(COOKIE_NAME) or ""
    return hashlib.sha256(value.encode()).hexdigest()


def audit_router(dsn, operator_token, sessions):
    router = APIRouter(prefix="/api/v1/audit")

    def authorize(request, authorization):
        require_browser_operator(request, authorization, operator_token, sessions)
        return audit_owner(request, authorization)

    @router.get("/status")
    def status(request: Request, authorization: str | None = Header(default=None)):
        authorize(request, authorization)
        try:
            return network_status(dsn)
        except psycopg.Error:
            raise HTTPException(503, "Audit indexing schema unavailable") from None

    @router.post("/search")
    def search(body: dict, request: Request, authorization: str | None = Header(default=None)):
        owner = authorize(request, authorization)
        try:
            return start_search(dsn, owner, body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except psycopg.Error:
            raise HTTPException(503, "Audit query unavailable or exceeded its time limit") from None

    @router.get("/snapshots/{token}")
    def page(token: str, request: Request, authorization: str | None = Header(default=None),
             after: int = 0, limit: int = 50, q: str = "", usage: str = ""):
        owner = authorize(request, authorization)
        try:
            return page_snapshot(dsn, owner, token, after=after, limit=limit, q=q, usage=usage)
        except SnapshotExpired as exc:
            raise HTTPException(410, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except psycopg.Error:
            raise HTTPException(503, "Audit snapshot unavailable") from None

    return router
