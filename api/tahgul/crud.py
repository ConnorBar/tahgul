"""Small helpers shared by the CRUD routers."""

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Any

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, PlainSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tahgul.models import Workspace
from tahgul.models.base import Base
from tahgul.principal import Principal


def _two_places(d: Decimal) -> str:
    return str(d.quantize(Decimal("0.01")))


def _percent(d: Decimal) -> str:
    return format(d.normalize(), "f")


# Response types: money, rates and hours are always strings with two decimals
# ("150.00") so clients never meet float rounding; percents drop trailing zeros.
TwoPlaces = Annotated[Decimal, PlainSerializer(_two_places, return_type=str)]
PercentOut = Annotated[Decimal, PlainSerializer(_percent, return_type=str)]

# Shared field types for request bodies.
RateIn = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2, examples=["150.00"])]
MoneyIn = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
HoursIn = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
PercentIn = Annotated[Decimal, Field(ge=0, le=100, max_digits=6, decimal_places=3)]
Name = Annotated[str, Field(min_length=1, max_length=200)]


def not_found(entity: str = "not_found") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{entity}_not_found")


def get_live[M: Base](db: Session, model: type[M], id_: uuid.UUID, entity: str) -> M:
    obj = db.get(model, id_)
    if obj is None or obj.deleted_at is not None:
        raise not_found(entity)
    return obj


def resolve_workspace(db: Session, workspace_id: uuid.UUID | None) -> Workspace:
    """Use the given workspace, or the only one when there's just one."""
    if workspace_id is not None:
        return get_live(db, Workspace, workspace_id, "workspace")
    live = db.scalars(select(Workspace).where(Workspace.deleted_at.is_(None)).limit(2)).all()
    if len(live) == 1:
        return live[0]
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="workspace_required" if live else "no_workspace",
    )


def ensure_unique_name[M: Base](
    db: Session,
    model: type[M],
    name: str,
    *,
    exclude_id: uuid.UUID | None = None,
    **scope: Any,
) -> None:
    """Names are unique (case-insensitively) within their parent, so lookups by name work."""
    q = select(model.id).where(
        func.lower(model.name) == name.strip().lower(), model.deleted_at.is_(None)
    )
    for col, value in scope.items():
        q = q.where(getattr(model, col) == value)
    if exclude_id is not None:
        q = q.where(model.id != exclude_id)
    if db.scalar(q.limit(1)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="name_taken")


def require_admin(principal: Principal, what: str) -> None:
    if not principal.has("admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"scope_required:admin ({what})")


def guard_rate_fields(principal: Principal, data: dict[str, Any], fields: set[str]) -> None:
    """Changing a rate needs the owner or an admin token (SPEC: agent limits)."""
    touched = [f for f in fields if f in data]
    if touched:
        require_admin(principal, "changing rates")


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal | uuid.UUID):
        return str(v)
    if isinstance(v, date | datetime | time):
        return v.isoformat()
    return v


def apply_changes(obj: Base, data: dict[str, Any]) -> dict[str, list[Any]]:
    """Set attributes and return {field: [old, new]} for the ones that changed."""
    changes: dict[str, list[Any]] = {}
    for field, new in data.items():
        old = getattr(obj, field)
        if isinstance(new, str):
            new = new.strip()
        if old != new:
            setattr(obj, field, new)
            changes[field] = [_plain(old), _plain(new)]
    return changes


def snapshot(data: dict[str, Any]) -> dict[str, Any]:
    return {k: _plain(v) for k, v in data.items()}


def patch_data(body: BaseModel, nullable: set[str]) -> dict:
    """Fields the client sent. An explicit null clears only nullable fields."""
    data = body.model_dump(exclude_unset=True)
    for k, v in list(data.items()):
        if v is None and k not in nullable:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{k}_cannot_be_null")
    return data
