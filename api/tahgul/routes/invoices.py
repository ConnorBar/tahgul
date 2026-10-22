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

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from tahgul import audit, lookup
from tahgul import invoicing as inv_svc
from tahgul import timekeeping as tk
from tahgul.config import get_settings
from tahgul.crud import (
    MoneyIn,
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
from tahgul.models import Client, Invoice, InvoiceLine, Project, Scope, TimeEntry, Workspace
from tahgul.principal import Principal
from tahgul.routes.scopes import FeeBilling, fee_billing
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
    external: bool = Field(False, description="Recorded here, sent from elsewhere")
    has_pdf: bool = False
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
    fees: TwoPlaces = Field(
        Decimal(0), description="Fixed fees of done scopes not yet invoiced (in amount)"
    )


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
    o.number = _number(inv)
    o.has_pdf = _has_pdf(inv)
    return o


def _number(inv: Invoice) -> str | None:
    return inv.external_number if inv.external else inv.number


def _has_pdf(inv: Invoice) -> bool:
    return not inv.external or bool(inv.pdf_path)


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
            "number": _number(inv),
            "has_pdf": _has_pdf(inv),
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
            selectinload(TimeEntry.scope),
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

    def row(c: Client, ws: Workspace, d: date) -> dict:
        return acc.setdefault(
            c.id,
            {
                "client": c,
                "secs": 0,
                "amount": Decimal(0),
                "fees": Decimal(0),
                "n": 0,
                "first": d,
                "last": d,
                "ws": ws,
            },
        )

    for e in db.scalars(q):
        c = e.project.client
        if c is None or c.deleted_at is not None:
            continue
        if e.scope is not None and e.scope.deleted_at is None and e.scope.type == "fixed":
            continue  # covered by the fee, which is counted below once the scope is done
        zone = ZoneInfo(e.project.workspace.timezone)
        d = tk.local_date(e.start_at, zone)
        a = row(c, e.project.workspace, d)
        a["secs"] += e.duration_seconds or 0
        a["amount"] += e.amount or 0
        a["n"] += 1
        a["first"], a["last"] = min(a["first"], d), max(a["last"], d)
    fq = (
        select(Scope)
        .options(
            selectinload(Scope.project).selectinload(Project.client),
            selectinload(Scope.project).selectinload(Project.workspace),
        )
        .join(Project, Scope.project_id == Project.id)
        .where(
            Scope.type == "fixed",
            Scope.status == "done",
            Scope.deleted_at.is_(None),
            Scope.invoiced_at.is_(None),
            Project.client_id.is_not(None),
        )
    )
    if workspace_id:
        fq = fq.where(Project.workspace_id == workspace_id)
    for sc in db.scalars(fq):
        c = sc.project.client
        rest = (sc.fee or 0) - inv_svc.fee_billed(db, sc.id)
        if c is None or c.deleted_at is not None or rest <= 0:
            continue
        ws = sc.project.workspace
        a = row(c, ws, sc.end_date or _today(ws))
        a["amount"] += rest
        a["fees"] += rest
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
                fees=a["fees"],
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
    name = f"invoice-{_number(inv) or 'draft'}.pdf".replace("/", "-").replace('"', "")
    disp = "attachment" if download else "inline"
    headers = {"Content-Disposition": f'{disp}; filename="{name}"'}
    if inv.external:  # the copy you sent, if you attached it
        path = get_settings().data_dir / inv.pdf_path if inv.pdf_path else None
        if path is None or not path.exists():
            raise HTTPException(404, detail="no_pdf")
        return FileResponse(Path(path), media_type="application/pdf", headers=headers)
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
    """Delete a draft and free its time. Issued invoices are voided instead, never deleted,
    except ones recorded from elsewhere (they never took a Tahgul number)."""
    inv = _load(db, invoice_id)
    if inv.status != "draft" and not inv.external:
        raise HTTPException(409, detail="invoice_issued")
    inv_svc.release(db, inv)
    inv.deleted_at = utcnow()
    inv_svc.sync_fee_scopes(db, inv)
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
    inv_svc.sync_fee_scopes(db, inv)
    if not inv.external:
        _write_pdf(inv)
    audit.record(db, principal, "void", "invoice", inv.id, {"number": _number(inv)})
    db.commit()
    return _out(db, _load(db, inv.id))


# --- Fixed fees, and invoices sent from elsewhere ----------------------------------------

PDF_MAX = 20 * 1024 * 1024


class FeeInvoiceIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    amount: MoneyIn | None = Field(None, description="Default: all of the fee not yet invoiced")
    description: str | None = Field(None, min_length=1, max_length=500)
    issue_date: date | None = Field(None, description="Default: today")
    notes: str | None = Field(None, max_length=4000)


class ExternalInvoiceIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    scope_id: uuid.UUID | None = Field(None, description="The fixed-fee scope it bills")
    client_id: uuid.UUID | None = Field(None, description="Needed when there is no scope_id")
    number: str = Field(min_length=1, max_length=60, description="Its number, as sent")
    issue_date: date
    due_date: date | None = Field(None, description="Default: the client's payment terms")
    amount: MoneyIn = Field(description="What it billed")
    description: str | None = Field(None, min_length=1, max_length=500)
    status: Literal["sent", "paid"] = "sent"
    paid_on: date | None = Field(None, description="With status paid; default: today")
    notes: str | None = Field(None, max_length=4000)

    @model_validator(mode="after")
    def who(self):
        if self.scope_id is None and self.client_id is None:
            raise ValueError("give scope_id or client_id")
        return self


class ExternalPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    number: str | None = Field(None, min_length=1, max_length=60)
    issue_date: date | None = None
    due_date: date | None = None
    amount: MoneyIn | None = None
    description: str | None = Field(None, min_length=1, max_length=500)
    notes: str | None = Field(None, max_length=4000)


def _fee_scope(db: Session, scope_id: uuid.UUID) -> tuple[Scope, Client]:
    s = get_live(db, Scope, scope_id, "scope")
    if s.type != "fixed":
        raise HTTPException(409, detail="scope_not_fixed_fee")
    client = s.project.client
    if client is None or client.deleted_at is not None:
        raise HTTPException(422, detail="project_has_no_client")
    return s, client


def _fee_left(db: Session, s: Scope, exclude: uuid.UUID | None = None) -> Decimal:
    return inv_svc.q2((s.fee or 0) - inv_svc.fee_billed(db, s.id, exclude=exclude))


def _check_part(amount: Decimal, left: Decimal) -> None:
    if amount <= 0:
        raise HTTPException(422, detail="amount_must_be_positive")
    if amount > left:
        raise HTTPException(422, detail="amount_over_fee")


def _fee_line(s: Scope, amount: Decimal, description: str | None, currency: str) -> InvoiceLine:
    fee = inv_svc.q2(s.fee or 0)
    if description is None:
        if amount == fee:
            description = f"{s.title} (fixed fee)"
        else:
            pct = (amount / fee * 100) if fee else Decimal(0)
            description = f"{s.title}: {pct:.0f}% of fixed fee {inv_svc._money(fee, currency)}"
    return InvoiceLine(
        kind="fee",
        position=0,
        description=description[:500],
        quantity=Decimal(1),
        unit="item",
        unit_price=amount,
        amount=amount,
        project_id=s.project_id,
        scope_id=s.id,
    )


class FixedFeeOut(BaseModel):
    scope_id: uuid.UUID
    title: str
    status: str
    project_id: uuid.UUID
    project_name: str
    client_id: uuid.UUID | None
    client_name: str | None
    currency: str
    billing: FeeBilling


@router.get("/fixed-fees", response_model=list[FixedFeeOut])
def fixed_fees(
    workspace_id: uuid.UUID | None = None,
    open: bool = Query(False, description="Only fees not fully invoiced or not fully paid"),
    _: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Every fixed-price piece of work and where its money stands: invoiced, paid, waiting."""
    q = (
        select(Scope)
        .options(
            selectinload(Scope.project).selectinload(Project.client),
            selectinload(Scope.project).selectinload(Project.workspace),
        )
        .join(Project, Scope.project_id == Project.id)
        .where(Scope.type == "fixed", Scope.deleted_at.is_(None), Project.deleted_at.is_(None))
        .order_by(Scope.created_at)
    )
    if workspace_id:
        q = q.where(Project.workspace_id == workspace_id)
    scopes = list(db.scalars(q))
    billing = fee_billing(db, scopes)
    out = []
    for sc in scopes:
        b = billing[sc.id]
        if open and b.to_invoice <= 0 and b.in_draft <= 0 and b.outstanding <= 0:
            continue
        p = sc.project
        out.append(
            FixedFeeOut(
                scope_id=sc.id,
                title=sc.title,
                status=sc.status,
                project_id=p.id,
                project_name=p.name,
                client_id=p.client_id,
                client_name=p.client.name if p.client else None,
                currency=p.workspace.currency,
                billing=b,
            )
        )
    return out


@router.post("/scopes/{scope_id}/invoice", response_model=InvoiceOut, status_code=201)
def invoice_fee(
    scope_id: uuid.UUID,
    body: FeeInvoiceIn,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Draft an invoice for a fixed fee, or part of it (a deposit, a milestone), any time.

    The draft holds only the fee; change its dates to pull in other unbilled work too.
    """
    s, client = _fee_scope(db, scope_id)
    left = _fee_left(db, s)
    amount = inv_svc.q2(body.amount if body.amount is not None else left)
    _check_part(amount, left)
    ws = client.workspace
    issue = body.issue_date or _today(ws)
    inv = Invoice(
        workspace_id=ws.id,
        client_id=client.id,
        status="draft",
        issue_date=issue,
        due_date=issue + timedelta(days=inv_svc.terms_for(client)),
        period_start=issue,
        period_end=issue,
        group_by="project",
        currency=ws.currency,
        tax_rate=inv_svc.tax_rate_for(client),
        notes=body.notes,
    )
    db.add(inv)
    db.flush()
    db.refresh(inv)
    inv.lines.append(_fee_line(s, amount, body.description, ws.currency))
    inv_svc.recalc(inv)
    audit.record(db, principal, "create", "invoice", inv.id, {"scope": s.title, "fee": str(amount)})
    db.commit()
    return _out(db, _load(db, inv.id))


@router.post("/invoices/external", response_model=InvoiceOut, status_code=201)
def record_external(
    body: ExternalInvoiceIn,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Record an invoice you sent from somewhere else, so Tahgul can track its payment.

    It keeps its own number (Tahgul's numbering is untouched) and counts towards
    outstanding and paid totals. Attach the PDF you sent with ``POST …/file``.
    """
    scope = None
    if body.scope_id:
        scope, client = _fee_scope(db, body.scope_id)
        _check_part(body.amount, _fee_left(db, scope))
    else:
        client = get_live(db, Client, body.client_id, "client")
        if body.amount <= 0:
            raise HTTPException(422, detail="amount_must_be_positive")
    ws = client.workspace
    due = body.due_date or body.issue_date + timedelta(days=inv_svc.terms_for(client))
    if due < body.issue_date:
        raise HTTPException(422, detail="due_before_issue")
    paid = body.status == "paid"
    inv = Invoice(
        workspace_id=ws.id,
        client_id=client.id,
        external=True,
        external_number=body.number,
        status=body.status,
        issue_date=body.issue_date,
        due_date=due,
        period_start=body.issue_date,
        period_end=body.issue_date,
        group_by="project",
        currency=ws.currency,
        notes=body.notes,
        bill_from=inv_svc.bill_from(ws),
        bill_to=inv_svc.bill_to(client),
        sent_at=utcnow(),
        paid_on=(body.paid_on or _today(ws)) if paid else None,
    )
    db.add(inv)
    db.flush()
    db.refresh(inv)
    amount = inv_svc.q2(body.amount)
    if scope is not None:
        inv.lines.append(_fee_line(scope, amount, body.description, ws.currency))
    else:
        inv.lines.append(
            InvoiceLine(
                kind="manual",
                position=0,
                description=(body.description or f"Invoice {body.number}")[:500],
                quantity=Decimal(1),
                unit="item",
                unit_price=amount,
                amount=amount,
            )
        )
    inv_svc.recalc(inv)
    inv_svc.sync_fee_scopes(db, inv)
    audit.record(
        db,
        principal,
        "record",
        "invoice",
        inv.id,
        {"number": body.number, "total": str(amount), "status": body.status},
    )
    db.commit()
    return _out(db, _load(db, inv.id))


def _external(db: Session, invoice_id: uuid.UUID) -> Invoice:
    inv = _load(db, invoice_id)
    if not inv.external:
        raise HTTPException(409, detail="invoice_not_external")
    return inv


@router.patch("/invoices/{invoice_id}/external", response_model=InvoiceOut)
def update_external(
    invoice_id: uuid.UUID,
    body: ExternalPatch,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Fix a recorded invoice's number, dates or amount."""
    inv = _external(db, invoice_id)
    if inv.status == "void":
        raise HTTPException(409, detail="invoice_void")
    data = patch_data(body, {"notes"})
    line = inv.lines[0] if inv.lines else None
    changes: dict = {}
    if "number" in data:
        changes |= apply_changes(inv, {"external_number": data.pop("number")})
    amount = data.pop("amount", None)
    description = data.pop("description", None)
    changes |= apply_changes(inv, data)
    if inv.due_date < inv.issue_date:
        raise HTTPException(422, detail="due_before_issue")
    inv.period_start = inv.period_end = inv.issue_date
    if line is not None and (amount is not None or description is not None):
        if amount is not None:
            amount = inv_svc.q2(amount)
            if line.kind == "fee" and line.scope_id:
                _check_part(amount, _fee_left(db, db.get(Scope, line.scope_id), exclude=inv.id))
            elif amount <= 0:
                raise HTTPException(422, detail="amount_must_be_positive")
        changes |= apply_changes(
            line,
            {
                k: v
                for k, v in (
                    ("unit_price", amount),
                    ("amount", amount),
                    ("description", description),
                )
                if v is not None
            },
        )
        inv_svc.recalc(inv)
        inv_svc.sync_fee_scopes(db, inv)
    if changes:
        audit.record(db, principal, "update", "invoice", inv.id, changes)
    db.commit()
    return _out(db, _load(db, inv.id))


def _limit_pdf(request: Request) -> None:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > PDF_MAX + 64 * 1024:
        raise HTTPException(413, detail="pdf_too_large")


@router.post(
    "/invoices/{invoice_id}/file", response_model=InvoiceOut, dependencies=[Depends(_limit_pdf)]
)
def attach_pdf(
    invoice_id: uuid.UUID,
    file: UploadFile = File(description="The PDF you sent, up to 20 MB"),
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    """Keep a copy of the PDF you sent with a recorded invoice."""
    inv = _external(db, invoice_id)
    data = file.file.read(PDF_MAX + 1)
    if len(data) > PDF_MAX:
        raise HTTPException(413, detail="pdf_too_large")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, detail="not_a_pdf")
    root = get_settings().data_dir
    path = root / "invoices" / "external" / f"{inv.id}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    inv.pdf_path = str(path.relative_to(root))
    audit.record(db, principal, "attach_pdf", "invoice", inv.id, {"bytes": len(data)})
    db.commit()
    return _out(db, _load(db, inv.id))


@router.delete("/invoices/{invoice_id}/file", response_model=InvoiceOut)
def remove_pdf(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_scope("invoice")),
    db: Session = Depends(get_db),
):
    inv = _external(db, invoice_id)
    if inv.pdf_path:
        (get_settings().data_dir / inv.pdf_path).unlink(missing_ok=True)
        inv.pdf_path = None
        audit.record(db, principal, "remove_pdf", "invoice", inv.id, {})
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
            _number(i),
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
            "yes" if i.external else "",
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
            "recorded_from_elsewhere",
        ],
        f"invoices-{year or 'all'}.csv",
    )


def as_local(dt: datetime, zone: ZoneInfo) -> datetime:
    return as_utc(dt).astimezone(zone)
