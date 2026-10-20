"""API tokens: one per client, scoped, stored hashed, shown once, revocable."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from tahgul import audit
from tahgul.db import get_db
from tahgul.deps import require_owner
from tahgul.models import ApiToken
from tahgul.principal import TOKEN_SCOPES, Principal
from tahgul.security import new_api_token, sha256
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: ["read"])

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("scopes")
    @classmethod
    def valid_scopes(cls, v: list[str]) -> list[str]:
        bad = sorted(set(v) - set(TOKEN_SCOPES))
        if bad:
            raise ValueError(
                f"unknown scopes: {', '.join(bad)}; allowed: {', '.join(TOKEN_SCOPES)}"
            )
        if not v:
            raise ValueError("at least one scope is required")
        return sorted(set(v))


class TokenOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class TokenCreated(TokenOut):
    token: str = Field(description="The full token. Shown only once; store it now.")


@router.get("", response_model=list[TokenOut])
def list_tokens(_: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    return db.scalars(
        select(ApiToken).where(ApiToken.deleted_at.is_(None)).order_by(ApiToken.created_at.desc())
    ).all()


@router.post("", response_model=TokenCreated, status_code=status.HTTP_201_CREATED)
def create_token(
    body: TokenIn, principal: Principal = Depends(require_owner), db: Session = Depends(get_db)
):
    raw, prefix = new_api_token()
    token = ApiToken(name=body.name, prefix=prefix, token_hash=sha256(raw), scopes=body.scopes)
    db.add(token)
    db.flush()
    audit.record(
        db, principal, "create", "api_token", token.id, {"name": body.name, "scopes": body.scopes}
    )
    db.commit()
    db.refresh(token)
    return TokenCreated(**TokenOut.model_validate(token).model_dump(), token=raw)


@router.delete("/{token_id}", response_model=TokenOut)
def revoke_token(
    token_id: uuid.UUID,
    principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    token = db.get(ApiToken, token_id)
    if token is None or token.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not_found")
    if token.revoked_at is None:
        token.revoked_at = utcnow()
        audit.record(db, principal, "revoke", "api_token", token.id, {"name": token.name})
        db.commit()
        db.refresh(token)
    return token
