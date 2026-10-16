import hmac

from fastapi import HTTPException


def bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="authentication required")
    return authorization[7:]


def authenticated_producer(token: str, producer_tokens: dict[str, str]) -> str:
    for producer_id, expected in producer_tokens.items():
        if hmac.compare_digest(token, expected):
            return producer_id
    raise HTTPException(status_code=401, detail="invalid producer credential")


def require_operator(token: str, operator_token: str) -> None:
    if not hmac.compare_digest(token, operator_token):
        raise HTTPException(status_code=401, detail="operator access required")
