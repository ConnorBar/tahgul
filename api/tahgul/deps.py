"""Request authentication: session cookie (owner) or bearer token (API clients)."""

from collections.abc import Callable
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from tahgul.config import get_settings
from tahgul.db import get_db
from tahgul.models import ApiToken, UserSession
from tahgul.principal import Principal
from tahgul.security import parse_api_token, sha256
from tahgul.timeutil import as_utc, utcnow

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-requested-with"

# Only bump last-used times this often, to avoid a write on every request.
TOUCH_INTERVAL = timedelta(minutes=1)


def _unauthorized(detail: str = "not_authenticated") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def _from_token(db: Session, raw: str) -> Principal:
    prefix = parse_api_token(raw)
    if prefix is None:
        raise _unauthorized("invalid_token")
    token = db.scalar(select(ApiToken).where(ApiToken.prefix == prefix))
    if (
        token is None
        or token.revoked_at is not None
        or token.deleted_at is not None
        or token.token_hash != sha256(raw)
    ):
        raise _unauthorized("invalid_token")
    now = utcnow()
    if token.last_used_at is None or now - as_utc(token.last_used_at) > TOUCH_INTERVAL:
        token.last_used_at = now
        db.commit()
    return Principal(
        kind="token", name=token.name, scopes=frozenset(token.scopes), token_id=token.id
    )


def _from_session(db: Session, request: Request, raw: str) -> Principal:
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == sha256(raw)))
    now = utcnow()
    if sess is None or sess.deleted_at is not None or as_utc(sess.expires_at) <= now:
        raise _unauthorized()
    # Cookie-authenticated writes must carry a custom header. Browsers can't add one
    # cross-site without a CORS preflight, which Tahgul never allows (CSRF guard).
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "tahgul":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="csrf_header_missing")
    if sess.last_seen_at is None or now - as_utc(sess.last_seen_at) > TOUCH_INTERVAL:
        sess.last_seen_at = now
        db.commit()
    return Principal(kind="owner", name="owner", user_id=sess.user_id, session_id=sess.id)


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return _from_token(db, auth[7:].strip())
    cookie = request.cookies.get(get_settings().session_cookie_name)
    if cookie:
        return _from_session(db, request, cookie)
    raise _unauthorized()


def require_scope(scope: str) -> Callable[[Principal], Principal]:
    def check(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"scope_required:{scope}")
        return principal

    return check


def require_owner(principal: Principal = Depends(get_principal)) -> Principal:
    """Owner-only actions (token management, security settings) never accept API tokens."""
    if not principal.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner_session_required")
    return principal
