"""Invoices: drafts built from time, issuing with gap-free numbers, PDFs and exports.

Every invoice endpoint needs the ``invoice`` token scope (the owner always has it).
"""

import csv
import io
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from tahgul import audit, lookup
from tahgul import invoicing as inv_svc
from tahgul import timekeeping as tk
from tahgul.config import get_settings
from tahgul.crud import (
    PercentIn,
    PercentOut,
    TwoPlaces,
    apply_changes,
    get_live,
    patch_data,
    require_admin,
    resolve_workspace,
)
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import Client, Invoice, InvoiceLine, Project, TimeEntry, Workspace
from tahgul.principal import Principal
from tahgul.timeutil import as_utc, utcnow

router = APIRouter(prefix="/api/v1", tags=["invoicing"])

GroupBy = Literal["project", "activity", "scope", "day", "entry"]
Status = Literal["draft", "sent", "paid", "void"]


# --- Schemas -----------------------------------------------------------------------------


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    position: int
    kind: str
    description: str
    quantity: TwoPlaces
    unit: str
    unit_price: TwoPlaces
    amount: TwoPlaces
    project_id: uuid.UUID | None
    scope_id: uuid.UUID | None
    period: str | None
    entry_count: int = 0


class InvoiceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    workspace_id: uuid.UUID
    client_id: uuid.UUID
    client_name: str = ""
    number: str | None
    status: str
    issue_date: date
    due_date: date
    period_start: date
    period_end: date
    currency: str
    total: TwoPlaces
    paid_on: date | None
    overdue: bool = False
    created_at: datetime


class InvoiceOut(InvoiceSummary):
    group_by: str
    tax_rate: PercentOut | None
    subtotal: TwoPlaces
    tax_amount: TwoPlaces
    notes: str | None
    bill_from: str
    bill_to: str
    sent_at: datetime | None
    voided_at: datetime | None
    lines: list[LineOut] = []
    hours: TwoPlaces = Decimal(0)
    warnings: list[str] = Field(default=[], description="Things to check before issuing")


class InvoiceIn(BaseModel):
    client_id: uuid.UUID | None = None
    client: str | None = Field(None, description="Client name or code, instead of client_id")
    workspace_id: uuid.UUID | None = None
    period_start: date | None = Field(
        None, description="Default: the client's earliest unbilled work"
    )
    period_end: date | None = Field(None, description="Default: today")
    group_by: GroupBy = "project"
    issue_date: date | None = Field(None, description="Default: today")
    notes: str | None = Field(None, max_length=4000)


class InvoicePatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    period_start: date | None = None
    period_end: date | None = None
    group_by: GroupBy | None = None
    issue_date: date | None = None
    due_date: date | None = None
    tax_rate: PercentIn | None = None
    notes: str | None = Field(None, max_length=4000)


class LineIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(Decimal(1), max_digits=10, decimal_places=2)
    unit: Literal["hours", "item", "month"] = "item"
    unit_price: Decimal = Field(
        max_digits=12, decimal_places=2, description="Negative for a discount"
    )


class LinePatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    description: str | None = Field(None, min_length=1, max_length=500)
    quantity: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    unit: Literal["hours", "item", "month"] | None = None
    unit_price: Decimal | None = Field(None, max_digits=12, decimal_places=2)


class PaidIn(BaseModel):
    paid_on: date | None = Field(None, description="Default: today")


class UnbilledClient(BaseModel):
    client_id: uuid.UUID
    client_name: str
    workspace_id: uuid.UUID
    currency: str
    hours: TwoPlaces
    amount: TwoPlaces
    entries: int
    first_date: date
    last_date: date


class Totals(BaseModel):
    currency: str
    outstanding: TwoPlaces = Field(description="Sent, not yet paid")
    overdue: TwoPlaces
    paid_this_year: TwoPlaces
    drafts: int


# --- Helpers -----------------------------------------------------------------------------


def _today(ws: Workspace) -> date:
    return utcnow().astimezone(ZoneInfo(ws.timezone)).date()


def _load(db: Session, invoice_id: uuid.UUID) -> Invoice:
    inv = db.scalar(
        select(Invoice)
        .options(
            selectinload(Invoice.lines),
            selectinload(Invoice.client),
            selectinload(Invoice.workspace),
        )
        .where(Invoice.id == invoice_id, Invoice.deleted_at.is_(None))
    )
    if inv is None:
        raise HTTPException(404, detail="invoice_not_found")
    return inv


def _draft(db: Session, invoice_id: uuid.UUID) -> Invoice:
    inv = _load(db, invoice_id)
    if inv.status != "draft":
        raise HTTPException(409, detail="invoice_issued")
    return inv


def _overdue(inv: Invoice) -> bool:
    return inv.status == "sent" and inv.due_date < _today(inv.workspace)


def _summary(inv: Invoice) -> InvoiceSummary:
    o = InvoiceSummary.model_validate(inv)
    o.client_name = inv.client.name
    o.overdue = _overdue(inv)
    return o


def _out(db: Session, inv: Invoice, warnings: list[str] | None = None) -> InvoiceOut:
    counts = dict(
        db.execute(
            select(TimeEntry.invoice_line_id, func.count())
            .where(TimeEntry.invoice_id == inv.id)
            .group_by(TimeEntry.invoice_line_id)
        ).all()
    )
    o = InvoiceOut.model_validate(
        {
            **{
                c: getattr(inv, c)
                for c in InvoiceOut.model_fields
                if hasattr(inv, c) and c not in ("lines", "warnings", "bill_from", "bill_to")
            },
            "bill_from": inv.bill_from or inv_svc.bill_from(inv.workspace),
            "bill_to": inv.bill_to or inv_svc.bill_to(inv.client),
            "client_name": inv.client.name,
            "overdue": _overdue(inv),
        }
    )
    o.lines = []
    for ln in sorted(inv.lines, key=lambda x: x.position):
        lo = LineOut.model_validate(ln)
        lo.entry_count = counts.get(ln.id, 0)
        o.lines.append(lo)
    o.hours = sum((ln.quantity for ln in inv.lines if ln.unit == "hours"), Decimal(0))
    if warnings is None:
        warnings = _standing_warnings(inv)
    o.warnings = warnings
    return o


def _standing_warnings(inv: Invoice) -> list[str]:
    if inv.status != "draft":
        return []
    out = []
    if not inv.lines:
        out.append("This invoice has no lines.")
    if inv.total < 0:
        out.append("The total is negative.")
    if not inv.client.billing_address:
        out.append(f"{inv.client.name} has no billing address.")
    ws = inv.workspace
    if not (ws.business_name or ws.business_address):
        out.append("Add your business name and address in workspace settings.")
    return out


def _flush_number(db: Session, inv: Invoice) -> None:
    """Assign the next number; retries once if another request took the same bucket."""
    for attempt in range(2):
        try:
            with db.begin_nested():
                inv.number = inv_svc.next_number(db, inv)
                db.flush()
            return
        except IntegrityError:
            if attempt:
                raise HTTPException(409, detail="invoice_number_taken") from None


def _write_pdf(inv: Invoice) -> None:
    path = inv_svc.store_pdf(inv)
    inv.pdf_path = str(path.relative_to(get_settings().data_dir))


# --- Reading -----------------------------------------------------------------------------


@router.get("/invoices", response_model=list[InvoiceSummary])
def list_invoices(
    workspace_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    status: Status | None = None,
    overdue: bool | None = None,
    year: int | None = Query(None, description="Issue year"),
    _: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    q = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.workspace))
        .where(Invoice.deleted_at.is_(None))
    )
    if workspace_id:
        q = q.where(Invoice.workspace_id == workspace_id)
    if client_id:
        q = q.where(Invoice.client_id == client_id)
    if status:
        q = q.where(Invoice.status == status)
    if year:
        q = q.where(
            Invoice.issue_date >= date(year, 1, 1), Invoice.issue_date < date(year + 1, 1, 1)
        )
    rows = db.scalars(q.order_by(Invoice.issue_date.desc(), Invoice.created_at.desc())).all()
    out = [_summary(i) for i in rows]
    if overdue is not None:
        out = [o for o in out if o.overdue == overdue]
    return out


@router.get("/invoices/unbilled", response_model=list[UnbilledClient])
def unbilled(
    workspace_id: uuid.UUID | None = None,
    _: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Clients with billable time not yet on an invoice: who to bill next."""
    q = (
        select(TimeEntry)
        .options(
            selectinload(TimeEntry.project).selectinload(Project.client),
            selectinload(TimeEntry.project).selectinload(Project.workspace),
        )
        .join(Project, TimeEntry.project_id == Project.id)
        .where(
            Project.client_id.is_not(None),
            TimeEntry.deleted_at.is_(None),
            TimeEntry.end_at.is_not(None),
            TimeEntry.billable.is_(True),
            TimeEntry.invoice_id.is_(None),
        )
    )
    if workspace_id:
        q = q.where(TimeEntry.workspace_id == workspace_id)
    acc: dict[uuid.UUID, dict] = {}
    for e in db.scalars(q):
        c = e.project.client
        if c is None or c.deleted_at is not None:
            continue
        zone = ZoneInfo(e.project.workspace.timezone)
        d = tk.local_date(e.start_at, zone)
        a = acc.setdefault(
            c.id,
            {
                "client": c,
                "secs": 0,
                "amount": Decimal(0),
                "n": 0,
                "first": d,
                "last": d,
                "ws": e.project.workspace,
            },
        )
        a["secs"] += e.duration_seconds or 0
        a["amount"] += e.amount or 0
        a["n"] += 1
        a["first"], a["last"] = min(a["first"], d), max(a["last"], d)
    return sorted(
        (
            UnbilledClient(
                client_id=c_id,
                client_name=a["client"].name,
                workspace_id=a["ws"].id,
                currency=a["ws"].currency,
                hours=tk.hours(a["secs"]),
                amount=a["amount"],
                entries=a["n"],
                first_date=a["first"],
                last_date=a["last"],
            )
            for c_id, a in acc.items()
        ),
        key=lambda u: -u.amount,
    )


@router.get("/invoices/totals", response_model=Totals)
def totals(
    workspace_id: uuid.UUID | None = None,
    _: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    ws = resolve_workspace(db, workspace_id)
    today = _today(ws)
    rows = db.scalars(
        select(Invoice).where(Invoice.workspace_id == ws.id, Invoice.deleted_at.is_(None))
    ).all()
    z = Decimal(0)
    return Totals(
        currency=ws.currency,
        outstanding=sum((i.total for i in rows if i.status == "sent"), z),
        overdue=sum((i.total for i in rows if i.status == "sent" and i.due_date < today), z),
        paid_this_year=sum(
            (
                i.total
                for i in rows
                if i.status == "paid" and i.paid_on and i.paid_on.year == today.year
            ),
            z,
        ),
        drafts=sum(1 for i in rows if i.status == "draft"),
    )


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(
    invoice_id: uuid.UUID,
    _: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    return _out(db, _load(db, invoice_id))


@router.get("/invoices/{invoice_id}/html", response_class=HTMLResponse)
def invoice_html(
    invoice_id: uuid.UUID,
    _: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """The invoice as HTML, for a quick preview."""
    return HTMLResponse(
        inv_svc.render_html(_load(db, invoice_id)),
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"
            )
        },
    )


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(
    invoice_id: uuid.UUID,
    download: bool = False,
    _: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """The PDF: the stored copy for issued invoices, a fresh preview for drafts."""
    inv = _load(db, invoice_id)
    name = f"invoice-{inv.number or 'draft'}.pdf".replace("/", "-")
    disp = "attachment" if download else "inline"
    headers = {"Content-Disposition": f'{disp}; filename="{name}"'}
    if inv.status == "draft":
        return Response(inv_svc.render_pdf(inv), media_type="application/pdf", headers=headers)
    path = get_settings().data_dir / inv.pdf_path if inv.pdf_path else None
    if path is None or not path.exists():  # e.g. data volume lost: render it again
        _write_pdf(inv)
        db.commit()
        path = get_settings().data_dir / inv.pdf_path
    return FileResponse(Path(path), media_type="application/pdf", headers=headers)


# --- Drafts ------------------------------------------------------------------------------


@router.post("/invoices", response_model=InvoiceOut, status_code=201)
@router.post("/invoices/draft", response_model=InvoiceOut, status_code=201, include_in_schema=False)
def create_invoice(
    body: InvoiceIn,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Draft an invoice from the client's unbilled time (and done fixed fees, retainers)."""
    if body.client_id:
        client = get_live(db, Client, body.client_id, "client")
    elif body.client:
        client = lookup.find_client(db, resolve_workspace(db, body.workspace_id).id, body.client)
    else:
        raise HTTPException(422, detail="client_required")
    ws = client.workspace
    today = _today(ws)
    end = body.period_end or today
    start = body.period_start
    if start is None:
        firsts = [
            tk.local_date(e.start_at, ZoneInfo(ws.timezone))
            for e in db.scalars(
                inv_svc._entries_query(client).order_by(TimeEntry.start_at).limit(1)
            )
        ]
        start = min(firsts[0], end) if firsts else end.replace(day=1)
    if start > end:
        raise HTTPException(422, detail="period_start_after_end")
    issue = body.issue_date or today
    inv = Invoice(
        workspace_id=ws.id,
        client_id=client.id,
        status="draft",
        issue_date=issue,
        due_date=issue + timedelta(days=inv_svc.terms_for(client)),
        period_start=start,
        period_end=end,
        group_by=body.group_by,
        currency=ws.currency,
        tax_rate=inv_svc.tax_rate_for(client),
        notes=body.notes,
    )
    db.add(inv)
    db.flush()
    db.refresh(inv)
    warnings = inv_svc.build_lines(db, inv)
    audit.record(
        db,
        principal,
        "create",
        "invoice",
        inv.id,
        {"client": client.name, "period": [str(start), str(end)]},
    )
    db.commit()
    return _out(db, _load(db, inv.id), warnings + _standing_warnings(inv))


@router.patch("/invoices/{invoice_id}", response_model=InvoiceOut)
def update_invoice(
    invoice_id: uuid.UUID,
    body: InvoicePatch,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    inv = _draft(db, invoice_id)
    data = patch_data(body, {"tax_rate", "notes"})
    if "issue_date" in data and "due_date" not in data:
        data["due_date"] = data["issue_date"] + timedelta(days=inv_svc.terms_for(inv.client))
    changes = apply_changes(inv, data)
    if inv.period_start > inv.period_end:
        raise HTTPException(422, detail="period_start_after_end")
    if inv.due_date < inv.issue_date:
        raise HTTPException(422, detail="due_before_issue")
    warnings = None
    if {"period_start", "period_end", "group_by"} & changes.keys():
        warnings = inv_svc.build_lines(db, inv) + _standing_warnings(inv)
    else:
        inv_svc.recalc(inv)
    if changes:
        audit.record(db, principal, "update", "invoice", inv.id, changes)
    db.commit()
    return _out(db, _load(db, inv.id), warnings)


@router.post("/invoices/{invoice_id}/rebuild", response_model=InvoiceOut)
def rebuild_invoice(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Pull in time logged since the draft was made. Manual lines stay."""
    inv = _draft(db, invoice_id)
    warnings = inv_svc.build_lines(db, inv)
    audit.record(db, principal, "rebuild", "invoice", inv.id, {})
    db.commit()
    return _out(db, _load(db, inv.id), warnings + _standing_warnings(inv))


@router.post("/invoices/{invoice_id}/lines", response_model=InvoiceOut, status_code=201)
def add_line(
    invoice_id: uuid.UUID,
    body: LineIn,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Add a manual line: expenses, a discount (negative price), anything not from time."""
    inv = _draft(db, invoice_id)
    pos = max((ln.position for ln in inv.lines), default=-1) + 1
    inv.lines.append(
        InvoiceLine(
            kind="manual",
            position=pos,
            description=body.description,
            quantity=body.quantity,
            unit=body.unit,
            unit_price=body.unit_price,
            amount=inv_svc.q2(body.quantity * body.unit_price),
        )
    )
    inv_svc.recalc(inv)
    audit.record(db, principal, "add_line", "invoice", inv.id, {"description": body.description})
    db.commit()
    return _out(db, _load(db, inv.id))


@router.patch("/invoices/{invoice_id}/lines/{line_id}", response_model=InvoiceOut)
def update_line(
    invoice_id: uuid.UUID,
    line_id: uuid.UUID,
    body: LinePatch,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Reword any line; change the numbers only on manual lines (time comes from entries)."""
    inv = _draft(db, invoice_id)
    ln = next((x for x in inv.lines if x.id == line_id), None)
    if ln is None:
        raise HTTPException(404, detail="line_not_found")
    data = body.model_dump(exclude_unset=True, exclude_none=True)
    if ln.kind != "manual" and data.keys() - {"description"}:
        raise HTTPException(409, detail="line_from_time")
    changes = apply_changes(ln, data)
    ln.amount = inv_svc.q2(ln.quantity * ln.unit_price)
    inv_svc.recalc(inv)
    if changes:
        audit.record(db, principal, "update_line", "invoice", inv.id, changes)
    db.commit()
    return _out(db, _load(db, inv.id))


@router.delete("/invoices/{invoice_id}/lines/{line_id}", response_model=InvoiceOut)
def delete_line(
    invoice_id: uuid.UUID,
    line_id: uuid.UUID,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Remove a line. Its time goes back to unbilled (a rebuild would add it again)."""
    inv = _draft(db, invoice_id)
    ln = next((x for x in inv.lines if x.id == line_id), None)
    if ln is None:
        raise HTTPException(404, detail="line_not_found")
    for e in db.scalars(select(TimeEntry).where(TimeEntry.invoice_line_id == ln.id)).all():
        e.invoice_id = e.invoice_line_id = None
    inv.lines.remove(ln)
    db.delete(ln)
    inv_svc.recalc(inv)
    audit.record(db, principal, "delete_line", "invoice", inv.id, {"description": ln.description})
    db.commit()
    return _out(db, _load(db, inv.id))


@router.delete("/invoices/{invoice_id}", status_code=204)
def delete_invoice(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Delete a draft and free its time. Issued invoices are voided instead, never deleted."""
    inv = _draft(db, invoice_id)
    inv_svc.release(db, inv)
    inv.deleted_at = utcnow()
    audit.record(db, principal, "delete", "invoice", inv.id, {"client": inv.client.name})
    db.commit()


# --- Lifecycle ---------------------------------------------------------------------------


@router.post("/invoices/{invoice_id}/issue", response_model=InvoiceOut)
def issue_invoice(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Give the draft its number, freeze it, store the PDF, and mark it sent."""
    inv = _draft(db, invoice_id)
    if not inv.lines:
        raise HTTPException(422, detail="invoice_empty")
    _flush_number(db, inv)
    inv.bill_from = inv_svc.bill_from(inv.workspace)
    inv.bill_to = inv_svc.bill_to(inv.client)
    inv.status = "sent"
    inv.sent_at = utcnow()
    inv_svc.mark_fixed_invoiced(db, inv)
    _write_pdf(inv)
    audit.record(
        db, principal, "issue", "invoice", inv.id, {"number": inv.number, "total": str(inv.total)}
    )
    db.commit()
    return _out(db, _load(db, inv.id))


@router.post("/invoices/{invoice_id}/paid", response_model=InvoiceOut)
def mark_paid(
    invoice_id: uuid.UUID,
    body: PaidIn,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    inv = _load(db, invoice_id)
    if inv.status not in ("sent", "paid"):
        raise HTTPException(409, detail=f"invoice_{inv.status}")
    inv.status = "paid"
    inv.paid_on = body.paid_on or _today(inv.workspace)
    audit.record(db, principal, "paid", "invoice", inv.id, {"paid_on": str(inv.paid_on)})
    db.commit()
    return _out(db, _load(db, inv.id))


@router.post("/invoices/{invoice_id}/unpaid", response_model=InvoiceOut)
def mark_unpaid(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Undo "paid" (a bounced payment, a slip of the finger)."""
    inv = _load(db, invoice_id)
    if inv.status != "paid":
        raise HTTPException(409, detail=f"invoice_{inv.status}")
    inv.status, inv.paid_on = "sent", None
    audit.record(db, principal, "unpaid", "invoice", inv.id, {})
    db.commit()
    return _out(db, _load(db, inv.id))


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceOut)
def void_invoice(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Cancel an issued invoice. The number stays used (no gaps); the time is unbilled again."""
    require_admin(principal, "voiding an invoice")
    inv = _load(db, invoice_id)
    if inv.status == "draft":
        raise HTTPException(409, detail="invoice_draft")
    if inv.status == "void":
        return _out(db, inv)
    inv_svc.release(db, inv)
    inv.status, inv.voided_at = "void", utcnow()
    _write_pdf(inv)
    audit.record(db, principal, "void", "invoice", inv.id, {"number": inv.number})
    db.commit()
    return _out(db, _load(db, inv.id))


# --- Exports -----------------------------------------------------------------------------


def _csv(rows: list[list], header: list[str], name: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response(
        "﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/exports/time-entries.csv", tags=["exports"])
def export_entries(
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    workspace_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Every finished entry in the range, for a spreadsheet or your accountant."""
    ws = resolve_workspace(db, workspace_id)
    zone = ZoneInfo(ws.timezone)
    start, end = tk.day_range(date_from, date_to, zone)
    q = (
        select(TimeEntry)
        .options(
            selectinload(TimeEntry.project).selectinload(Project.client),
            selectinload(TimeEntry.activity),
            selectinload(TimeEntry.scope),
        )
        .where(
            TimeEntry.workspace_id == ws.id,
            TimeEntry.deleted_at.is_(None),
            TimeEntry.end_at.is_not(None),
            TimeEntry.start_at >= start,
            TimeEntry.start_at < end,
        )
    )
    if project_id:
        q = q.where(TimeEntry.project_id == project_id)
    if client_id:
        q = q.join(Project, TimeEntry.project_id == Project.id).where(
            Project.client_id == client_id
        )
    entries = db.scalars(q.order_by(TimeEntry.start_at)).all()
    numbers = dict(
        db.execute(
            select(Invoice.id, Invoice.number).where(
                Invoice.id.in_({e.invoice_id for e in entries if e.invoice_id})
            )
        ).all()
    )
    rows = []
    for e in entries:
        s, f = as_local(e.start_at, zone), as_local(e.end_at, zone)
        p = e.project
        rows.append(
            [
                s.date().isoformat(),
                s.strftime("%H:%M"),
                f.strftime("%H:%M"),
                tk.hours(e.duration_seconds or 0),
                p.client.name if p and p.client else "",
                p.name if p else "",
                e.activity.name if e.activity else "",
                e.scope.title if e.scope else "",
                e.notes or "",
                "yes" if e.billable else "no",
                e.rate if e.rate is not None else "",
                e.amount if e.amount is not None else "",
                numbers.get(e.invoice_id) or ("draft" if e.invoice_id else ""),
            ]
        )
    return _csv(
        rows,
        [
            "date",
            "start",
            "end",
            "hours",
            "client",
            "project",
            "activity",
            "scope",
            "notes",
            "billable",
            "rate",
            "amount",
            "invoice",
        ],
        f"time-{date_from}-to-{date_to}.csv",
    )


@router.get("/exports/invoices.csv", tags=["exports"])
def export_invoices(
    year: int | None = None,
    workspace_id: uuid.UUID | None = None,
    _: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Issued invoices (drafts left out), e.g. for taxes."""
    q = (
        select(Invoice)
        .options(selectinload(Invoice.client))
        .where(Invoice.deleted_at.is_(None), Invoice.status != "draft")
    )
    if workspace_id:
        q = q.where(Invoice.workspace_id == workspace_id)
    if year:
        q = q.where(
            Invoice.issue_date >= date(year, 1, 1), Invoice.issue_date < date(year + 1, 1, 1)
        )
    rows = [
        [
            i.number,
            i.issue_date,
            i.due_date,
            i.client.name,
            i.period_start,
            i.period_end,
            i.currency,
            i.subtotal,
            i.tax_amount,
            i.total,
            i.status,
            i.paid_on or "",
        ]
        for i in db.scalars(q.order_by(Invoice.issue_date, Invoice.number))
    ]
    return _csv(
        rows,
        [
            "number",
            "issued",
            "due",
            "client",
            "period_start",
            "period_end",
            "currency",
            "subtotal",
            "tax",
            "total",
            "status",
            "paid_on",
        ],
        f"invoices-{year or 'all'}.csv",
    )


def as_local(dt: datetime, zone: ZoneInfo) -> datetime:
    return as_utc(dt).astimezone(zone)
