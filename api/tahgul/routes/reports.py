"""Reports: hours and money by client, project, scope and week (SPEC: ideas inbox and reports)."""

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from tahgul import lookup
from tahgul import timekeeping as tk
from tahgul.crud import TwoPlaces, get_live, resolve_workspace
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import Client, Project, TimeEntry
from tahgul.principal import Principal
from tahgul.routes.time import _q

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

ZERO = Decimal("0")


class Totals(BaseModel):
    hours: TwoPlaces
    billable_hours: TwoPlaces
    amount: TwoPlaces = Field(description="Billable work at the frozen rates.")
    unbilled_amount: TwoPlaces = Field(description="Billable and not yet on an invoice.")
    effective_rate: TwoPlaces | None = Field(description="Amount ÷ billable hours.")


class ClientLine(Totals):
    client_id: uuid.UUID | None
    name: str


class ProjectLine(Totals):
    project_id: uuid.UUID | None
    name: str
    client_name: str | None
    colour: str | None
    budget_hours: TwoPlaces | None
    budget_amount: TwoPlaces | None
    used_hours_all_time: TwoPlaces
    used_amount_all_time: TwoPlaces
    budget_hours_left: TwoPlaces | None
    budget_amount_left: TwoPlaces | None


class ScopeLine(Totals):
    scope_id: uuid.UUID | None = Field(description="None = work outside every scope")
    title: str
    project_id: uuid.UUID
    project_name: str
    type: str | None
    budget_hours: TwoPlaces | None
    budget_amount: TwoPlaces | None
    used_hours_all_time: TwoPlaces | None
    burn_warning: str | None = Field(description="ok, near (80%) or over (100%), all time")


class WeekLine(Totals):
    week_start: date


class Summary(BaseModel):
    workspace_id: uuid.UUID
    currency: str
    from_: date = Field(alias="from")
    to: date
    totals: Totals
    by_client: list[ClientLine]
    by_project: list[ProjectLine]
    by_scope: list[ScopeLine] = Field(
        description="Scoped work, plus work outside the scopes of projects that have any"
    )
    by_week: list[WeekLine]
    entry_count: int

    model_config = {"populate_by_name": True}


class _Acc:
    __slots__ = ("secs", "bill_secs", "amount", "unbilled")

    def __init__(self):
        self.secs = 0
        self.bill_secs = 0
        self.amount = ZERO
        self.unbilled = ZERO

    def add(self, e: TimeEntry) -> None:
        s = e.duration_seconds or 0
        self.secs += s
        if e.billable:
            self.bill_secs += s
            amt = e.amount or ZERO
            self.amount += amt
            if e.invoice_id is None:
                self.unbilled += amt

    def totals(self) -> dict:
        bill_h = tk.hours(self.bill_secs)
        return {
            "hours": tk.hours(self.secs),
            "billable_hours": bill_h,
            "amount": self.amount,
            "unbilled_amount": self.unbilled,
            "effective_rate": (self.amount / bill_h) if bill_h else None,
        }


@router.get("/summary", response_model=Summary, response_model_by_alias=True)
def summary(
    workspace_id: uuid.UUID | None = None,
    client_ref: str | None = Query(None, alias="client", description="Client id or name"),
    project_ref: str | None = Query(None, alias="project", description="Project id or name"),
    from_: date | None = Query(None, alias="from", description="Local date; default: Monday"),
    to: date | None = Query(None, description="Local date, inclusive; default: Sunday"),
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Hours, money and budget left for a date range."""
    as_id = lookup._as_uuid(client_ref) if client_ref else None
    if as_id is not None:
        workspace_id = workspace_id or get_live(db, Client, as_id, "client").workspace_id
    ws = resolve_workspace(db, workspace_id)
    client = lookup.find_client(db, ws.id, client_ref).id if client_ref else None
    project = lookup.find_project(db, ws.id, project_ref).id if project_ref else None
    zone = tk.tz(ws)
    today = datetime.now(zone).date()
    first = from_ or tk.week_start(today)
    last = to or (first + timedelta(days=6) if from_ is None else today)
    start, end = tk.day_range(first, last, zone)

    q = _q().where(
        TimeEntry.workspace_id == ws.id,
        TimeEntry.deleted_at.is_(None),
        TimeEntry.end_at.is_not(None),
        TimeEntry.start_at >= start,
        TimeEntry.start_at < end,
    )
    if client is not None:
        q = q.where(TimeEntry.project_id.in_(select(Project.id).where(Project.client_id == client)))
    if project is not None:
        q = q.where(TimeEntry.project_id == project)
    entries = db.scalars(q).all()

    total = _Acc()
    by_client: dict = defaultdict(_Acc)
    by_project: dict = defaultdict(_Acc)
    by_week: dict = defaultdict(_Acc)
    by_scope: dict = defaultdict(_Acc)
    projects: dict = {}
    for e in entries:
        total.add(e)
        p = e.project
        by_client[(p.client_id, p.client.name) if p and p.client else (None, "No client")].add(e)
        by_project[p.id if p else None].add(e)
        projects[p.id if p else None] = p
        by_week[tk.week_start(tk.local_date(e.start_at, zone))].add(e)
        if p is not None:
            by_scope[(p.id, e.scope_id)].add(e)

    # Budgets are measured against all work ever logged on the project.
    used = _all_time_usage(db, [pid for pid in projects if pid])

    project_lines = []
    for pid, acc in by_project.items():
        p = projects[pid]
        u_secs, u_amt = used.get(pid, (0, ZERO))
        u_h = tk.hours(u_secs)
        project_lines.append(
            ProjectLine(
                project_id=pid,
                name=p.name if p else "No project",
                client_name=p.client.name if p and p.client else None,
                colour=p.colour if p else None,
                budget_hours=p.budget_hours if p else None,
                budget_amount=p.budget_amount if p else None,
                used_hours_all_time=u_h,
                used_amount_all_time=u_amt,
                budget_hours_left=(p.budget_hours - u_h) if p and p.budget_hours else None,
                budget_amount_left=(p.budget_amount - u_amt) if p and p.budget_amount else None,
                **acc.totals(),
            )
        )
    project_lines.sort(key=lambda x: -x.hours)

    client_lines = sorted(
        (
            ClientLine(client_id=cid, name=name, **acc.totals())
            for (cid, name), acc in by_client.items()
        ),
        key=lambda x: -x.hours,
    )
    scope_lines = _scope_lines(db, by_scope, projects)
    week_lines = [WeekLine(week_start=w, **acc.totals()) for w, acc in sorted(by_week.items())]

    return Summary(
        workspace_id=ws.id,
        currency=ws.currency,
        from_=first,
        to=last,
        totals=Totals(**total.totals()),
        by_client=client_lines,
        by_project=project_lines,
        by_scope=scope_lines,
        by_week=week_lines,
        entry_count=len(entries),
    )


def _scope_lines(db: Session, by_scope: dict, projects: dict) -> list[ScopeLine]:
    from tahgul.models import Scope
    from tahgul.routes.scopes import scope_outs

    scoped_projects = set(
        db.scalars(
            select(Scope.project_id).where(
                Scope.project_id.in_([pid for pid in projects if pid]),
                Scope.deleted_at.is_(None),
            )
        ).all()
    )
    scope_ids = [sid for (_, sid) in by_scope if sid]
    scopes = {
        o.id: o
        for o in scope_outs(
            db, list(db.scalars(select(Scope).where(Scope.id.in_(scope_ids))).all())
        )
    }
    lines = []
    for (pid, sid), acc in by_scope.items():
        p = projects[pid]
        if sid is None and pid not in scoped_projects:
            continue
        o = scopes.get(sid)
        lines.append(
            ScopeLine(
                scope_id=sid,
                title=o.title if o else "Outside agreed scopes",
                project_id=pid,
                project_name=p.name,
                type=o.type if o else None,
                budget_hours=o.budget_hours if o else None,
                budget_amount=o.budget_amount if o else None,
                used_hours_all_time=o.burn.hours if o and o.burn else None,
                burn_warning=o.burn.warning if o and o.burn else None,
                **acc.totals(),
            )
        )
    lines.sort(key=lambda x: (x.project_name, x.scope_id is None, -x.hours))
    return lines


def _all_time_usage(db: Session, project_ids: list[uuid.UUID]) -> dict:
    if not project_ids:
        return {}
    rows = db.execute(
        select(
            TimeEntry.project_id, TimeEntry.duration_seconds, TimeEntry.amount, TimeEntry.billable
        ).where(
            TimeEntry.project_id.in_(project_ids),
            TimeEntry.deleted_at.is_(None),
            TimeEntry.end_at.is_not(None),
        )
    ).all()
    out: dict = {}
    for pid, secs, amount, billable in rows:
        s, a = out.get(pid, (0, ZERO))
        out[pid] = (s + (secs or 0), a + ((amount or ZERO) if billable else ZERO))
    return out
