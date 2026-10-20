"""Owner login: password + optional TOTP, sessions in secure HTTP-only cookies."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from tahgul import audit
from tahgul.config import get_settings
from tahgul.db import get_db
from tahgul.deps import get_principal, require_owner
from tahgul.models import LoginAttempt, User, UserSession
from tahgul.principal import Principal
from tahgul.security import (
    DUMMY_HASH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    new_session_token,
    new_totp_secret,
    sha256,
    totp_uri,
    verify_password,
    verify_totp,
)
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None


class MeOut(BaseModel):
    kind: str
    name: str
    email: str | None = None
    scopes: list[str]
    totp_enabled: bool | None = None


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class TotpCodeIn(BaseModel):
    code: str


class TotpDisableIn(BaseModel):
    code: str
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_throttle(db: Session, ip: str) -> None:
    s = get_settings()
    since = utcnow() - timedelta(minutes=s.login_window_minutes)
    failures = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(LoginAttempt.ip == ip, LoginAttempt.created_at >= since)
    )
    if failures and failures >= s.login_max_failures:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="too_many_attempts")


def _fail(db: Session, ip: str, email: str, detail: str = "invalid_credentials") -> HTTPException:
    db.add(LoginAttempt(ip=ip, email=email))
    db.commit()
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail)


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.session_cookie_name,
        token,
        max_age=s.session_days * 86400,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        path="/",
    )


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    _check_throttle(db, ip)

    user = db.scalar(
        select(User).where(func.lower(User.email) == body.email.lower(), User.deleted_at.is_(None))
    )
    if user is None:
        verify_password(DUMMY_HASH, body.password)  # equalize timing
        raise _fail(db, ip, body.email)
    if not verify_password(user.password_hash, body.password):
        raise _fail(db, ip, body.email)

    if user.totp_enabled:
        if not body.totp_code:
            # Password was right; ask the client for the second factor.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="totp_required")
        step = verify_totp(user.totp_secret or "", body.totp_code, user.totp_last_step)
        if step is None:
            raise _fail(db, ip, body.email, "invalid_totp")
        user.totp_last_step = step

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    token = new_session_token()
    sess = UserSession(
        user_id=user.id,
        token_hash=sha256(token),
        expires_at=utcnow() + timedelta(days=get_settings().session_days),
        last_seen_at=utcnow(),
        user_agent=(request.headers.get("user-agent") or "")[:500],
    )
    db.add(sess)
    db.execute(delete(LoginAttempt).where(LoginAttempt.ip == ip))
    principal = Principal(kind="owner", name="owner", user_id=user.id)
    audit.record(db, principal, "login", "session", changes={"ip": ip})
    db.commit()

    _set_cookie(response, token)
    return MeOut(
        kind="owner", name="owner", email=user.email, scopes=["*"], totp_enabled=user.totp_enabled
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response, principal: Principal = Depends(require_owner), db: Session = Depends(get_db)
):
    sess = db.get(UserSession, principal.session_id)
    if sess is not None:
        sess.deleted_at = utcnow()
        db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")


@router.get("/me", response_model=MeOut)
def me(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if principal.is_owner:
        user = db.get(User, principal.user_id)
        return MeOut(
            kind="owner",
            name="owner",
            email=user.email if user else None,
            scopes=["*"],
            totp_enabled=user.totp_enabled if user else None,
        )
    return MeOut(kind="token", name=principal.name, scopes=sorted(principal.scopes))


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChangeIn,
    principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    user = db.get(User, principal.user_id)
    if user is None or not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_password")
    user.password_hash = hash_password(body.new_password)
    # Sign out every other session.
    for sess in user.sessions:
        if sess.id != principal.session_id and sess.deleted_at is None:
            sess.deleted_at = utcnow()
    audit.record(db, principal, "update", "user", user.id, {"password": "changed"})
    db.commit()


@router.post("/totp/setup", response_model=TotpSetupOut)
def totp_setup(principal: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    """Generate a new secret. It only takes effect after /totp/enable confirms a code."""
    user = db.get(User, principal.user_id)
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="totp_already_enabled")
    user.totp_secret = new_totp_secret()
    db.commit()
    return TotpSetupOut(secret=user.totp_secret, otpauth_uri=totp_uri(user.totp_secret, user.email))


@router.post("/totp/enable", status_code=status.HTTP_204_NO_CONTENT)
def totp_enable(
    body: TotpCodeIn, principal: Principal = Depends(require_owner), db: Session = Depends(get_db)
):
    user = db.get(User, principal.user_id)
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="totp_already_enabled")
    if not user.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="totp_not_set_up")
    step = verify_totp(user.totp_secret, body.code, None)
    if step is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_totp")
    user.totp_enabled = True
    user.totp_last_step = step
    audit.record(db, principal, "update", "user", user.id, {"totp": "enabled"})
    db.commit()


@router.post("/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
def totp_disable(
    body: TotpDisableIn,
    principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    user = db.get(User, principal.user_id)
    if not user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="totp_not_enabled")
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_password")
    if verify_totp(user.totp_secret or "", body.code, user.totp_last_step) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_totp")
    user.totp_enabled = False
    user.totp_secret = None
    user.totp_last_step = None
    audit.record(db, principal, "update", "user", user.id, {"totp": "disabled"})
    db.commit()
