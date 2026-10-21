"""What a client sees on a shared report: progress, hours and budget, never internals.

Shown: projects, hours by week, scope budgets used, tasks finished and coming up.
Optional per link: money (amounts, invoices) and time-entry notes. Never shown: rates,
other clients, internal comments, task descriptions, voice notes, ideas.
"""

from __future__ import annotations

import hashlib
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from tahgul import timekeeping as tk
from tahgul.invoicing import TEMPLATES, _money
from tahgul.models import (
    BoardColumn,
    Invoice,
    Project,
    ReportLink,
    Scope,
    Task,
    TimeEntry,
)
from tahgul.timeutil import as_utc, utcnow

PERIODS = {
    "this-month": "This month",
    "last-month": "Last month",
    "90d": "Last 90 days",
    "all": "All time",
}
ZERO = Decimal(0)


def new_token() -> str:
    return secrets.token_urlsafe(24)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def find(db: Session, token: str) -> ReportLink | None:
    link = db.scalar(select(ReportLink).where(ReportLink.token_hash == token_hash(token)))
    if link is None or link.deleted_at is not None or link.revoked_at is not None:
        return None
    if link.expires_at is not None and as_utc(link.expires_at) <= utcnow():
        return None
    if link.client is None or link.client.deleted_at is not None:
        return None
    return link


def find_by_link(link: ReportLink) -> bool:
    """Whether a link would open right now."""
    if link.deleted_at is not None or link.revoked_at is not None:
        return False
    return link.expires_at is None or as_utc(link.expires_at) > utcnow()


def period_range(period: str, today: date, first_work: date | None) -> tuple[date, date]:
    if period == "last-month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if period == "90d":
        return today - timedelta(days=89), today
    if period == "all":
        return (first_work or today), today
    return today.replace(day=1), today


@dataclass
class ProjectBlock:
    name: str
    colour: str
    status: str
    hours: Decimal = ZERO
    billable_hours: Decimal = ZERO
    amount: Decimal = ZERO
    budget_hours: Decimal | None = None
    used_hours: Decimal = ZERO
    scopes: list[dict] = field(default_factory=list)
    done: list[dict] = field(default_factory=list)
    upcoming: list[dict] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)


def build(db: Session, link: ReportLink, period: str) -> dict:
    client = link.client
    ws = client.workspace
    zone = ZoneInfo(ws.timezone)
    today = datetime.now(zone).date()
    q = select(Project).where(
        Project.client_id == client.id, Project.deleted_at.is_(None), Project.status != "idea"
    )
    if link.project_id:
        q = q.where(Project.id == link.project_id)
    projects = list(db.scalars(q.order_by(Project.name)).all())
    pids = [p.id for p in projects]

    all_entries = (
        db.scalars(
            select(TimeEntry).where(
                TimeEntry.project_id.in_(pids),
                TimeEntry.deleted_at.is_(None),
                TimeEntry.end_at.is_not(None),
            )
        ).all()
        if pids
        else []
    )
    first_work = min((tk.local_date(e.start_at, zone) for e in all_entries), default=None)
    first, last = period_range(period, today, first_work)
    start, end = tk.day_range(first, last, zone)

    blocks: dict = {
        p.id: ProjectBlock(p.name, p.colour, p.status, budget_hours=p.budget_hours)
        for p in projects
    }
    weeks: dict = defaultdict(lambda: ZERO)
    total = {"hours": ZERO, "billable": ZERO, "amount": ZERO}
    for e in all_entries:
        b = blocks[e.project_id]
        hrs = tk.hours(e.duration_seconds or 0)
        b.used_hours += hrs
        if not (start <= as_utc(e.start_at) < end):
            continue
        b.hours += hrs
        total["hours"] += hrs
        weeks[tk.week_start(tk.local_date(e.start_at, zone))] += hrs
        if e.billable:
            b.billable_hours += hrs
            b.amount += e.amount or ZERO
            total["billable"] += hrs
            total["amount"] += e.amount or ZERO
        if link.show_notes:
            b.entries.append(
                {"date": tk.local_date(e.start_at, zone), "hours": hrs, "notes": e.notes or ""}
            )

    if pids:
        from tahgul.routes.scopes import scope_outs

        scopes = db.scalars(
            select(Scope)
            .where(Scope.project_id.in_(pids), Scope.deleted_at.is_(None), Scope.status != "draft")
            .order_by(Scope.start_date.is_(None), Scope.start_date, Scope.created_at)
        ).all()
        project_of = {s.id: s.project_id for s in scopes}
        for o in scope_outs(db, list(scopes)):
            blocks[project_of[o.id]].scopes.append(
                {
                    "title": o.title,
                    "type": o.type,
                    "status": o.status,
                    "budget_hours": o.budget_hours,
                    "used_hours": o.burn.hours if o.burn else ZERO,
                    "pct": min(float(o.burn.hours_pct), 100.0)
                    if o.burn and o.burn.hours_pct
                    else None,
                    "warning": o.burn.warning if o.burn else None,
                    "fee": o.fee,
                }
            )

    if link.show_tasks and pids:
        tasks = db.execute(
            select(Task, BoardColumn)
            .join(BoardColumn, Task.column_id == BoardColumn.id)
            .where(Task.project_id.in_(pids), Task.deleted_at.is_(None), Task.parent_id.is_(None))
        ).all()
        for t, col in tasks:
            b = blocks[t.project_id]
            if col.is_done:
                if t.completed_at and start <= as_utc(t.completed_at) < end:
                    b.done.append({"title": t.title, "when": tk.local_date(t.completed_at, zone)})
            else:
                b.upcoming.append({"title": t.title, "status": col.name, "due": t.due_date})
        for b in blocks.values():
            b.done.sort(key=lambda x: x["when"], reverse=True)
            b.upcoming.sort(key=lambda x: (x["due"] is None, x["due"] or date.max, x["title"]))
            b.upcoming = b.upcoming[:12]
            b.entries.sort(key=lambda x: x["date"])

    invoices = []
    if link.show_amounts:
        invoices = db.scalars(
            select(Invoice)
            .where(
                Invoice.client_id == client.id,
                Invoice.deleted_at.is_(None),
                Invoice.status.in_(("sent", "paid")),
            )
            .order_by(Invoice.issue_date.desc())
            .limit(12)
        ).all()

    week_rows = []
    if weeks:
        w = tk.week_start(first)
        while w <= last:
            week_rows.append({"start": w, "hours": weeks.get(w, ZERO)})
            w += timedelta(days=7)
    while week_rows and not week_rows[0]["hours"]:  # start at the first week with work
        week_rows.pop(0)
    peak = max((r["hours"] for r in week_rows), default=ZERO) or Decimal(1)

    return {
        "link": link,
        "client": client,
        "ws": ws,
        "business": ws.business_name or ws.name,
        "period": period,
        "period_label": PERIODS[period],
        "periods": PERIODS,
        "first": first,
        "last": last,
        "projects": [
            b
            for b in blocks.values()
            if b.hours or b.scopes or b.upcoming or b.done or b.status == "active"
        ],
        "total": total,
        "weeks": week_rows,
        "peak": peak,
        "invoices": invoices,
        "currency": ws.currency,
        "generated": datetime.now(zone),
    }


_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
_env.filters["money"] = lambda v, cur: _money(Decimal(v), cur)
_env.filters["hrs"] = lambda v: f"{Decimal(v).quantize(Decimal('0.1')).normalize():f}"
_env.filters["d"] = lambda d: f"{d:%b} {d.day}, {d.year}"
_env.filters["dshort"] = lambda d: f"{d:%b} {d.day}"


def render(ctx: dict, *, pdf: bool = False) -> str:
    return _env.get_template("client_report.html").render(**ctx, pdf=pdf)


def render_pdf(ctx: dict) -> bytes:
    from weasyprint import HTML

    return HTML(string=render(ctx, pdf=True), base_url=str(TEMPLATES)).write_pdf()
