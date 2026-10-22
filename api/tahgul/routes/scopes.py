"""Scopes: the agreed pieces of a project, with budgets and burn (SPEC: scopes and budgets)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

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
from tahgul.invoicing import FEE_KINDS, fee_billed
from tahgul.models import Invoice, InvoiceLine, Project, Scope, Task, TimeEntry, Workspace
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


class FeeInvoice(BaseModel):
    id: uuid.UUID
    number: str | None
    status: str
    external: bool
    issue_date: date
    due_date: date
    paid_on: date | None
    overdue: bool
    amount: TwoPlaces = Field(description="Of this fee")
    total: TwoPlaces = Field(description="The whole invoice")


class FeeBilling(BaseModel):
    """Where a fixed fee stands: invoiced, paid, waiting, still to bill."""

    fee: TwoPlaces
    invoiced: TwoPlaces = Field(description="On sent or paid invoices")
    paid: TwoPlaces
    outstanding: TwoPlaces = Field(description="Invoiced, not paid yet")
    overdue: TwoPlaces
    in_draft: TwoPlaces = Field(description="On draft invoices")
    to_invoice: TwoPlaces = Field(description="Not on any invoice yet")
    invoices: list[FeeInvoice]


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
    billing: FeeBilling | None = Field(None, description="Fixed-fee scopes only")
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


def fee_billing(db: Session, scopes: list[Scope]) -> dict[uuid.UUID, FeeBilling]:
    fixed = [s for s in scopes if s.type == "fixed"]
    if not fixed:
        return {}
    rows = db.execute(
        select(InvoiceLine.scope_id, InvoiceLine.amount, Invoice)
        .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
        .where(
            InvoiceLine.scope_id.in_([s.id for s in fixed]),
            InvoiceLine.kind.in_(FEE_KINDS),
            Invoice.deleted_at.is_(None),
            Invoice.status != "void",
        )
        .order_by(Invoice.issue_date, Invoice.created_at)
    ).all()
    zones: dict[uuid.UUID, date] = {}
    by_scope: dict[uuid.UUID, dict[uuid.UUID, FeeInvoice]] = {s.id: {} for s in fixed}
    for sid, amount, inv in rows:
        if inv.workspace_id not in zones:
            ws = db.get(Workspace, inv.workspace_id)
            zones[inv.workspace_id] = utcnow().astimezone(ZoneInfo(ws.timezone)).date()
        today = zones[inv.workspace_id]
        fi = by_scope[sid].get(inv.id)
        if fi is None:
            fi = by_scope[sid][inv.id] = FeeInvoice(
                id=inv.id,
                number=inv.external_number if inv.external else inv.number,
                status=inv.status,
                external=inv.external,
                issue_date=inv.issue_date,
                due_date=inv.due_date,
                paid_on=inv.paid_on,
                overdue=inv.status == "sent" and inv.due_date < today,
                amount=ZERO,
                total=inv.total,
            )
        fi.amount += amount
    out = {}
    for s in fixed:
        invs = list(by_scope[s.id].values())

        def total(pred, invs=invs) -> Decimal:
            return sum((i.amount for i in invs if pred(i)), ZERO)

        invoiced = total(lambda i: i.status in ("sent", "paid"))
        drafted = total(lambda i: i.status == "draft")
        paid = total(lambda i: i.status == "paid")
        fee = s.fee or ZERO
        out[s.id] = FeeBilling(
            fee=fee,
            invoiced=invoiced,
            paid=paid,
            outstanding=invoiced - paid,
            overdue=total(lambda i: i.overdue),
            in_draft=drafted,
            to_invoice=max(fee - invoiced - drafted, ZERO),
            invoices=invs,
        )
    return out


def scope_outs(db: Session, scopes: list[Scope]) -> list[ScopeOut]:
    usage = _usage(db, [s.id for s in scopes])
    billing = fee_billing(db, scopes)
    out = []
    for s in scopes:
        o = ScopeOut.model_validate(s)
        o.burn = _burn(s, *usage.get(s.id, (0, ZERO)))
        o.billing = billing.get(s.id)
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
    if s.type == "fixed" and ({"fee", "type"} & data.keys()):
        billed = fee_billed(db, s.id)
        if billed and (new_type != "fixed" or data.get("fee", s.fee) < billed):
            # Already (partly) on invoices: the fee can grow, not shrink below what's billed.
            raise HTTPException(409, detail="scope_invoiced")
    changes = apply_changes(s, data)
    if s.type == "fixed" and "fee" in changes:
        full = fee_billed(db, s.id, statuses=("sent", "paid")) >= (s.fee or ZERO)
        s.invoiced_at = (s.invoiced_at or utcnow()) if full else None
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
    if s.invoiced_at or (s.type == "fixed" and fee_billed(db, s.id)):
        raise HTTPException(409, detail="scope_invoiced")
    for e in db.scalars(select(TimeEntry).where(TimeEntry.scope_id == s.id)).all():
        if e.invoice_id is None:
            e.scope_id = None
    for t in db.scalars(select(Task).where(Task.scope_id == s.id)).all():
        t.scope_id = None
    s.deleted_at = utcnow()
    audit.record(db, principal, "delete", "scope", s.id, {"title": s.title})
    db.commit()
