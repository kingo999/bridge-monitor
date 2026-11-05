import hmac
import secrets
import time

from fastapi import HTTPException, Request


def bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="authentication required")
    token = authorization[7:]
    if not token.strip():
        raise HTTPException(status_code=401, detail="authentication required")
    return token


def authenticated_producer(token: str, producer_tokens: dict[str, str]) -> str:
    for producer_id, expected in producer_tokens.items():
        if hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
            return producer_id
    raise HTTPException(status_code=401, detail="invalid producer credential")


def require_operator(token: str, operator_token: str) -> None:
    if not hmac.compare_digest(token.encode("utf-8"), operator_token.encode("utf-8")):
        raise HTTPException(status_code=401, detail="operator access required")


COOKIE_NAME = "bridge_monitor_session"
LOCAL_TEST_CREDENTIAL = "password"


def matches_local_test_login(request: Request, token: str, origin: str | None) -> bool:
    if origin is None or token != LOCAL_TEST_CREDENTIAL:
        return False
    scheme, authority = origin.split("://", 1)
    server = request.scope.get("server")
    return bool(
        server and server[0] == "127.0.0.1"
        and request.client and request.client.host == "127.0.0.1"
        and request.url.scheme == scheme
        and request.headers.getlist("host") == [authority]
        and request.headers.getlist("origin") == [origin]
        and not any(name in {"forwarded", "via"} or name.startswith("x-forwarded-")
                    for name in request.headers)
    )


def create_session(sessions: dict[str, float], ttl_seconds: int) -> str:
    now = time.monotonic()
    for key, expiry in list(sessions.items()):
        if expiry <= now:
            sessions.pop(key, None)
    identifier = secrets.token_urlsafe(32)
    sessions[identifier] = now + ttl_seconds
    return identifier


def require_browser_operator(request: Request, authorization: str | None,
                             operator_token: str, sessions: dict[str, float]) -> None:
    if authorization:
        require_operator(bearer(authorization), operator_token)
        return
    identifier = request.cookies.get(COOKIE_NAME)
    if identifier and sessions.get(identifier, 0) > time.monotonic():
        return
    raise HTTPException(status_code=401, detail="operator access required")
