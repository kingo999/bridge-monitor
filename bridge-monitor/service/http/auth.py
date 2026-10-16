import hmac

from fastapi import HTTPException


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
