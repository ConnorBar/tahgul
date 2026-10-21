"""Building, numbering and rendering invoices (SPEC: features, invoicing).

A draft is built from a client and a date range. It pulls billable, un-invoiced time
and groups it into lines:

* hourly work: by project, activity, scope, day or entry, at each entry's frozen rate,
  with the workspace's rounding applied per entry (raw entries stay exact)
* fixed-fee scopes: one line for the fee once the scope is done; its time is covered
* retainers: one line per month, plus any hours past the monthly allowance

Entries on a draft are locked. Issuing assigns the next gap-free number, freezes the
from/to addresses and renders the PDF; voiding keeps the number and frees the time.
"""

from __future__ import annotations

import calendar
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from tahgul import numbering
from tahgul import timekeeping as tk
from tahgul.config import get_settings
from tahgul.models import (
    Client,
    Invoice,
    InvoiceLine,
    InvoiceSequence,
    Project,
    Scope,
    TimeEntry,
    Workspace,
)
from tahgul.timeutil import utcnow

CENT = Decimal("0.01")
ZERO = Decimal("0")
TEMPLATES = Path(__file__).parent / "templates"


def q2(x: Decimal) -> Decimal:
    return Decimal(x).quantize(CENT, ROUND_HALF_UP)


# --- Building lines ----------------------------------------------------------------------


@dataclass
class _Group:
    description: str
    rate: Decimal
    seconds: int = 0
    project_id: uuid.UUID | None = None
    scope_id: uuid.UUID | None = None
    entries: list[TimeEntry] = field(default_factory=list)
    first: datetime | None = None


def _money(amount: Decimal, currency: str) -> str:
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$"}.get(currency)
    return f"{sym}{amount:,.2f}" if sym else f"{amount:,.2f} {currency}"


def _qty(x: Decimal) -> str:
    return f"{Decimal(x).normalize():f}"


def _month_label(period: str) -> str:
    y, m = period.split("-")
    return f"{calendar.month_name[int(m)]} {y}"


def _months(first: date, last: date) -> list[str]:
    out, d = [], first.replace(day=1)
    while d <= last:
        out.append(f"{d.year:04d}-{d.month:02d}")
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def _entries_query(client: Client):
    return (
        select(TimeEntry)
        .options(
            selectinload(TimeEntry.project),
            selectinload(TimeEntry.activity),
            selectinload(TimeEntry.scope),
        )
        .join(Project, TimeEntry.project_id == Project.id)
        .where(
            Project.client_id == client.id,
            TimeEntry.deleted_at.is_(None),
            TimeEntry.end_at.is_not(None),
            TimeEntry.billable.is_(True),
            TimeEntry.invoice_id.is_(None),
        )
    )


def billable_entries(db: Session, client: Client, first: date, last: date) -> list[TimeEntry]:
    zone = ZoneInfo(client.workspace.timezone)
    start, end = tk.day_range(first, last, zone)
    return list(
        db.scalars(
            _entries_query(client)
            .where(TimeEntry.start_at >= start, TimeEntry.start_at < end)
            .order_by(TimeEntry.start_at)
        ).all()
    )


def build_lines(db: Session, inv: Invoice) -> list[str]:
    """(Re)build the invoice's generated lines; manual lines are kept. Returns warnings."""
    ws: Workspace = inv.workspace
    client: Client = inv.client
    zone = ZoneInfo(ws.timezone)
    warnings: list[str] = []

    # Release what this draft held and drop its generated lines.
    for e in db.scalars(select(TimeEntry).where(TimeEntry.invoice_id == inv.id)).all():
        e.invoice_id = e.invoice_line_id = None
    for line in list(inv.lines):
        if line.kind != "manual":
            inv.lines.remove(line)
            db.delete(line)
    db.flush()

    entries = billable_entries(db, client, inv.period_start, inv.period_end)
    hourly: list[TimeEntry] = []
    fixed: dict[uuid.UUID, list[TimeEntry]] = defaultdict(list)
    retainer: dict[tuple[uuid.UUID, str], list[TimeEntry]] = defaultdict(list)
    for e in entries:
        s = e.scope
        if s is not None and s.deleted_at is None and s.type == "fixed":
            fixed[s.id].append(e)
        elif (
            s is not None and s.deleted_at is None and s.type == "retainer" and s.status != "draft"
        ):
            local = tk.local_date(e.start_at, zone)
            retainer[(s.id, f"{local.year:04d}-{local.month:02d}")].append(e)
        else:
            hourly.append(e)

    new_lines: list[tuple[InvoiceLine, list[TimeEntry]]] = []

    # Hourly work, grouped; one line per (group, rate) so quantity × price = amount.
    groups: dict[tuple, _Group] = {}
    for e in hourly:
        if e.rate is None:
            warnings.append(
                f"No rate for {e.project.name if e.project else 'an entry'} "
                f"on {tk.local_date(e.start_at, zone)}; billed at 0."
            )
        rate = e.rate or ZERO
        label, pid, sid = _label(inv.group_by, e, zone)
        key = (label, rate) if inv.group_by != "entry" else (e.id,)
        g = groups.get(key)
        if g is None:
            g = groups[key] = _Group(
                description=label, rate=rate, project_id=pid, scope_id=sid, first=e.start_at
            )
        g.seconds += tk.round_seconds(
            e.duration_seconds or 0, ws.rounding_minutes, ws.rounding_mode
        )
        g.entries.append(e)
    by_label = defaultdict(list)
    for g in groups.values():
        by_label[g.description].append(g)
    for g in sorted(groups.values(), key=lambda x: (x.first, x.description)):
        desc = g.description
        if len(by_label[desc]) > 1:  # same label at different rates: say which
            desc = f"{desc} (at {_money(g.rate, inv.currency)}/h)"
        qty = tk.hours(g.seconds)
        new_lines.append(
            (
                InvoiceLine(
                    kind="time",
                    description=desc[:500],
                    quantity=qty,
                    unit="hours",
                    unit_price=q2(g.rate),
                    amount=q2(qty * g.rate),
                    project_id=g.project_id,
                    scope_id=g.scope_id,
                ),
                g.entries,
            )
        )

    # Fixed-fee scopes: billed once, when done.
    fixed_scopes = db.scalars(
        select(Scope)
        .join(Project)
        .where(
            Project.client_id == client.id,
            Scope.type == "fixed",
            Scope.status == "done",
            Scope.invoiced_at.is_(None),
            Scope.deleted_at.is_(None),
        )
    ).all()
    for s in fixed_scopes:
        covered = list(db.scalars(_entries_query(client).where(TimeEntry.scope_id == s.id)))
        new_lines.append(
            (
                InvoiceLine(
                    kind="fixed",
                    description=f"{s.title} (fixed fee)",
                    quantity=Decimal(1),
                    unit="item",
                    unit_price=q2(s.fee or ZERO),
                    amount=q2(s.fee or ZERO),
                    project_id=s.project_id,
                    scope_id=s.id,
                ),
                covered,
            )
        )
    for sid in fixed:
        if sid not in {s.id for s in fixed_scopes}:
            scope = db.get(Scope, sid)
            warnings.append(
                f"Time on fixed-fee scope “{scope.title}” isn't billed until "
                "the scope is marked done."
            )

    # Retainers: one line per month in the period, plus overage.
    retainers = db.scalars(
        select(Scope)
        .join(Project)
        .where(
            Project.client_id == client.id,
            Scope.type == "retainer",
            Scope.deleted_at.is_(None),
            Scope.status != "draft",
        )
    ).all()
    billed = {
        (ln.scope_id, ln.period)
        for ln in db.scalars(
            select(InvoiceLine)
            .join(Invoice)
            .where(
                InvoiceLine.kind == "retainer",
                Invoice.status != "void",
                Invoice.id != inv.id,
                Invoice.deleted_at.is_(None),
            )
        )
    }
    for s in retainers:
        for period in _months(inv.period_start, inv.period_end):
            month_start = date.fromisoformat(period + "-01")
            if (
                s.start_date
                and s.start_date
                > month_start.replace(
                    day=calendar.monthrange(month_start.year, month_start.month)[1]
                )
            ) or (s.end_date and s.end_date < month_start):
                continue
            if (s.id, period) in billed:
                continue
            month_entries = retainer.get((s.id, period), [])
            new_lines.append(
                (
                    InvoiceLine(
                        kind="retainer",
                        description=f"{s.title} retainer, {_month_label(period)}",
                        quantity=Decimal(1),
                        unit="month",
                        unit_price=q2(s.fee or ZERO),
                        amount=q2(s.fee or ZERO),
                        project_id=s.project_id,
                        scope_id=s.id,
                        period=period,
                    ),
                    month_entries,
                )
            )
            if s.included_hours is not None and month_entries:
                secs = sum(
                    tk.round_seconds(e.duration_seconds or 0, ws.rounding_minutes, ws.rounding_mode)
                    for e in month_entries
                )
                extra = tk.hours(secs) - s.included_hours
                if extra > 0:
                    rate = s.rate or month_entries[0].rate or ZERO
                    new_lines.append(
                        (
                            InvoiceLine(
                                kind="overage",
                                description=f"{s.title}: hours over {_qty(s.included_hours)} "
                                f"included, {_month_label(period)}",
                                quantity=extra,
                                unit="hours",
                                unit_price=q2(rate),
                                amount=q2(extra * rate),
                                project_id=s.project_id,
                                scope_id=s.id,
                                period=period,
                            ),
                            [],
                        )
                    )
    for sid, period in retainer:
        if not any(ln.scope_id == sid and ln.period == period for ln, _ in new_lines):
            warnings.append(
                f"Retainer time in {_month_label(period)} isn't billed: that month "
                "is already invoiced or outside the retainer's dates."
            )

    # Place: generated lines first (in order), manual lines after.
    manual = [ln for ln in inv.lines if ln.kind == "manual"]
    pos = 0
    for line, covered in new_lines:
        line.position = pos
        pos += 1
        inv.lines.append(line)
        db.flush()
        for e in covered:
            e.invoice_id, e.invoice_line_id = inv.id, line.id
    for line in manual:
        line.position = pos
        pos += 1
    recalc(inv)
    return warnings


def _label(
    group_by: str, e: TimeEntry, zone: ZoneInfo
) -> tuple[str, uuid.UUID | None, uuid.UUID | None]:
    p = e.project
    if group_by == "activity":
        return (e.activity.name if e.activity else "Consulting"), None, None
    if group_by == "scope":
        if e.scope is not None:
            return f"{p.name}: {e.scope.title}", p.id, e.scope.id
        return f"{p.name} (outside agreed scope)" if p else "Other work", p and p.id, None
    if group_by == "day":
        d = tk.local_date(e.start_at, zone)
        return f"{d:%a %b} {d.day}: {p.name if p else 'Work'}", p and p.id, None
    if group_by == "entry":
        d = tk.local_date(e.start_at, zone)
        what = e.notes or (e.activity.name if e.activity else "")
        return (
            f"{d:%b} {d.day}, {p.name if p else 'Work'}" + (f": {what}" if what else ""),
            p and p.id,
            e.scope_id,
        )
    return (p.name if p else "Other work"), p and p.id, None


def recalc(inv: Invoice) -> None:
    sub = sum((ln.amount for ln in inv.lines), ZERO)
    inv.subtotal = q2(sub)
    inv.tax_amount = q2(sub * inv.tax_rate / 100) if inv.tax_rate else ZERO
    inv.total = q2(inv.subtotal + inv.tax_amount)


def tax_rate_for(client: Client) -> Decimal | None:
    return client.tax_rate if client.tax_rate is not None else client.workspace.tax_rate


def terms_for(client: Client) -> int:
    return (
        client.payment_terms_days
        if client.payment_terms_days is not None
        else client.workspace.payment_terms_days
    )


# --- Issuing -----------------------------------------------------------------------------


def next_number(db: Session, inv: Invoice) -> str:
    """Take the next number in the invoice's counter bucket, gap-free (row-locked)."""
    ws, client = inv.workspace, inv.client
    pattern = ws.invoice_number_pattern
    if numbering.uses_client_code(pattern) and not client.code:
        raise HTTPException(409, detail="client_needs_code")
    key = numbering.sequence_key(pattern, on=inv.issue_date, client_id=str(client.id))
    seq = db.scalar(
        select(InvoiceSequence)
        .where(InvoiceSequence.workspace_id == ws.id, InvoiceSequence.key == key)
        .with_for_update()
    )
    if seq is None:
        seq = InvoiceSequence(workspace_id=ws.id, key=key, last_value=0)
        db.add(seq)
    seq.last_value += 1
    return numbering.format_number(
        pattern, on=inv.issue_date, seq=seq.last_value, client_code=client.code
    )


def bill_from(ws: Workspace) -> str:
    return "\n".join(
        x for x in (ws.business_name or ws.name, ws.business_address, ws.business_email) if x
    )


def bill_to(client: Client) -> str:
    return "\n".join(x for x in (client.name, client.billing_address, client.contact_email) if x)


# --- Rendering ---------------------------------------------------------------------------

_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
_env.filters["money"] = lambda v, cur: _money(Decimal(v), cur)
_env.filters["qty"] = _qty
_env.filters["span"] = lambda a, b: (
    f"{a:%b} {a.day} – {b:%b} {b.day}, {b.year}"
    if a.year == b.year
    else f"{a:%b} {a.day}, {a.year} – {b:%b} {b.day}, {b.year}"
)
_env.filters["date"] = lambda d: f"{d:%B} {d.day}, {d.year}"


def render_html(inv: Invoice) -> str:
    ws = inv.workspace
    return _env.get_template("invoice.html").render(
        inv=inv,
        ws=ws,
        client=inv.client,
        lines=sorted(inv.lines, key=lambda ln: ln.position),
        bill_from=inv.bill_from or bill_from(ws),
        bill_to=inv.bill_to or bill_to(inv.client),
        draft=inv.status == "draft",
        void=inv.status == "void",
        terms=(inv.due_date - inv.issue_date).days,
    )


def render_pdf(inv: Invoice) -> bytes:
    from weasyprint import HTML  # imported lazily: needs Pango at runtime

    return HTML(string=render_html(inv), base_url=str(TEMPLATES)).write_pdf()


def pdf_path_for(inv: Invoice) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in (inv.number or str(inv.id)))
    return get_settings().data_dir / "invoices" / f"{inv.issue_date.year}" / f"{safe}.pdf"


def store_pdf(inv: Invoice) -> Path:
    path = pdf_path_for(inv)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(render_pdf(inv))
    tmp.replace(path)
    return path


def release(db: Session, inv: Invoice) -> None:
    """Free the invoice's time (and fixed-fee scopes) so it can be billed again."""
    for e in db.scalars(select(TimeEntry).where(TimeEntry.invoice_id == inv.id)).all():
        e.invoice_id = e.invoice_line_id = None
    for line in inv.lines:
        if line.kind == "fixed" and line.scope_id:
            s = db.get(Scope, line.scope_id)
            if s is not None:
                s.invoiced_at = None


def mark_fixed_invoiced(db: Session, inv: Invoice) -> None:
    for line in inv.lines:
        if line.kind == "fixed" and line.scope_id:
            db.get(Scope, line.scope_id).invoiced_at = utcnow()
