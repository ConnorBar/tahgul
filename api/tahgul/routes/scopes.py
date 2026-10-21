"""Scopes: the agreed pieces of a project, with budgets and burn (SPEC: scopes and budgets)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tahgul import audit
from tahgul import timekeeping as tk
from tahgul.crud import (
    HoursIn,
    MoneyIn,
    RateIn,
    TwoPlaces,
    apply_changes,
    get_live,
    guard_rate_fields,
    patch_data,
    require_admin,
    snapshot,
)
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import Project, Scope, Task, TimeEntry
from tahgul.principal import Principal
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1", tags=["scopes"])

ScopeType = Literal["fixed", "hourly", "retainer"]
ScopeStatus = Literal["draft", "agreed", "done"]
RATE_FIELDS = {"rate", "fee"}
NULLABLE = {
    "description",
    "rate",
    "fee",
    "budget_hours",
    "budget_amount",
    "included_hours",
    "start_date",
    "end_date",
}
WARN_AT = Decimal("0.8")
ZERO = Decimal("0")


class ScopeFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    description: str | None = Field(None, max_length=20_000)
    rate: RateIn | None = Field(None, description="Hourly rate for work in this scope")
    fee: MoneyIn | None = Field(None, description="Fixed fee, or the monthly retainer")
    budget_hours: HoursIn | None = None
    budget_amount: MoneyIn | None = None
    included_hours: HoursIn | None = Field(None, description="Retainer hours per month")
    start_date: date | None = None
    end_date: date | None = None


class ScopeIn(ScopeFields):
    title: str = Field(min_length=1, max_length=200)
    type: ScopeType = "hourly"
    status: ScopeStatus = "agreed"

    @model_validator(mode="after")
    def fee_needed(self):
        if self.type in ("fixed", "retainer") and self.fee is None:
            raise ValueError(f"a {self.type} scope needs a fee")
        return self


class ScopePatch(ScopeFields):
    title: str | None = Field(None, min_length=1, max_length=200)
    type: ScopeType | None = None
    status: ScopeStatus | None = None


class Burn(BaseModel):
    hours: TwoPlaces = Field(description="All time logged in the scope")
    amount: TwoPlaces = Field(description="Billable value at the frozen rates")
    hours_pct: TwoPlaces | None = Field(description="Of budget_hours, 0–100+")
    amount_pct: TwoPlaces | None
    warning: Literal["ok", "near", "over"] | None = Field(
        description="near at 80% of a budget, over at 100%"
    )


class ScopeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str | None
    type: str
    status: str
    rate: TwoPlaces | None
    fee: TwoPlaces | None
    budget_hours: TwoPlaces | None
    budget_amount: TwoPlaces | None
    included_hours: TwoPlaces | None
    start_date: date | None
    end_date: date | None
    invoiced_at: datetime | None
    burn: Burn | None = None
    created_at: datetime


class ScopeReport(BaseModel):
    project_id: uuid.UUID
    scopes: list[ScopeOut]
    outside_scope_hours: TwoPlaces = Field(
        description="Time on the project in no scope: possible scope creep"
    )
    outside_scope_amount: TwoPlaces
    creep: bool = Field(description="The project has scopes and some work is outside them")


def _usage(db: Session, scope_ids: list[uuid.UUID]) -> dict:
    if not scope_ids:
        return {}
    rows = db.execute(
        select(
            TimeEntry.scope_id,
            func.coalesce(func.sum(TimeEntry.duration_seconds), 0),
            func.coalesce(func.sum(TimeEntry.amount), 0),
        )
        .where(
            TimeEntry.scope_id.in_(scope_ids),
            TimeEntry.deleted_at.is_(None),
            TimeEntry.end_at.is_not(None),
        )
        .group_by(TimeEntry.scope_id)
    ).all()
    return {sid: (int(secs), Decimal(amt)) for sid, secs, amt in rows}


def _burn(s: Scope, secs: int, amount: Decimal) -> Burn:
    hours = tk.hours(secs)
    hp = (hours / s.budget_hours * 100) if s.budget_hours else None
    ap = (amount / s.budget_amount * 100) if s.budget_amount else None
    pcts = [p for p in (hp, ap) if p is not None]
    warning = None
    if pcts:
        top = max(pcts)
        warning = "over" if top >= 100 else "near" if top >= WARN_AT * 100 else "ok"
    return Burn(hours=hours, amount=amount, hours_pct=hp, amount_pct=ap, warning=warning)


def scope_outs(db: Session, scopes: list[Scope]) -> list[ScopeOut]:
    usage = _usage(db, [s.id for s in scopes])
    out = []
    for s in scopes:
        o = ScopeOut.model_validate(s)
        o.burn = _burn(s, *usage.get(s.id, (0, ZERO)))
        out.append(o)
    return out


def check_scope(
    db: Session, scope_id: uuid.UUID | None, project_id: uuid.UUID | None
) -> Scope | None:
    """A scope must belong to the same project as the work."""
    if scope_id is None:
        return None
    s = get_live(db, Scope, scope_id, "scope")
    if project_id is None or s.project_id != project_id:
        raise HTTPException(422, detail="scope_in_other_project")
    return s


@router.get("/projects/{project_id}/scopes", response_model=ScopeReport)
def list_scopes(
    project_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Scopes with hours and money used against budget, plus work outside every scope."""
    get_live(db, Project, project_id, "project")
    scopes = list(
        db.scalars(
            select(Scope)
            .where(Scope.project_id == project_id, Scope.deleted_at.is_(None))
            .order_by(Scope.start_date.is_(None), Scope.start_date, Scope.created_at)
        ).all()
    )
    secs, amount = db.execute(
        select(
            func.coalesce(func.sum(TimeEntry.duration_seconds), 0),
            func.coalesce(func.sum(TimeEntry.amount), 0),
        ).where(
            TimeEntry.project_id == project_id,
            TimeEntry.scope_id.is_(None),
            TimeEntry.deleted_at.is_(None),
            TimeEntry.end_at.is_not(None),
        )
    ).one()
    return ScopeReport(
        project_id=project_id,
        scopes=scope_outs(db, scopes),
        outside_scope_hours=tk.hours(int(secs)),
        outside_scope_amount=Decimal(amount),
        creep=bool(scopes) and int(secs) > 0,
    )


@router.post("/projects/{project_id}/scopes", response_model=ScopeOut, status_code=201)
def create_scope(
    project_id: uuid.UUID,
    body: ScopeIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    get_live(db, Project, project_id, "project")
    data = body.model_dump()
    guard_rate_fields(principal, {k: v for k, v in data.items() if v is not None}, RATE_FIELDS)
    s = Scope(project_id=project_id, **data)
    db.add(s)
    db.flush()
    audit.record(db, principal, "create", "scope", s.id, snapshot(data))
    db.commit()
    return scope_outs(db, [s])[0]


@router.get("/scopes/{scope_id}", response_model=ScopeOut)
def get_scope(
    scope_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return scope_outs(db, [get_live(db, Scope, scope_id, "scope")])[0]


@router.patch("/scopes/{scope_id}", response_model=ScopeOut)
def update_scope(
    scope_id: uuid.UUID,
    body: ScopePatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    s = get_live(db, Scope, scope_id, "scope")
    data = patch_data(body, NULLABLE)
    guard_rate_fields(principal, data, RATE_FIELDS)
    new_type = data.get("type", s.type)
    if new_type in ("fixed", "retainer") and data.get("fee", s.fee) is None:
        raise HTTPException(422, detail="scope_needs_fee")
    if s.invoiced_at and ({"fee", "type"} & data.keys()):
        raise HTTPException(409, detail="scope_invoiced")
    changes = apply_changes(s, data)
    if changes:
        audit.record(db, principal, "update", "scope", s.id, changes)
        db.commit()
    return scope_outs(db, [s])[0]


@router.delete("/scopes/{scope_id}", status_code=204)
def delete_scope(
    scope_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Delete a scope; its time and tasks stay on the project, outside any scope."""
    require_admin(principal, "deleting")
    s = get_live(db, Scope, scope_id, "scope")
    if s.invoiced_at:
        raise HTTPException(409, detail="scope_invoiced")
    for e in db.scalars(select(TimeEntry).where(TimeEntry.scope_id == s.id)).all():
        if e.invoice_id is None:
            e.scope_id = None
    for t in db.scalars(select(Task).where(Task.scope_id == s.id)).all():
        t.scope_id = None
    s.deleted_at = utcnow()
    audit.record(db, principal, "delete", "scope", s.id, {"title": s.title})
    db.commit()
