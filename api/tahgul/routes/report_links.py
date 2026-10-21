"""Managing client report links (owner, or an admin token: they share client data)."""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from tahgul import audit, crypto
from tahgul import client_report as cr
from tahgul.config import get_settings
from tahgul.crud import apply_changes, get_live, require_admin
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import Client, Project, ReportLink
from tahgul.principal import Principal
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1", tags=["report links"])


def admin(principal: Principal = Depends(require_scope("read"))) -> Principal:
    require_admin(principal, "sharing reports")
    return principal


class LinkIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    label: str = Field("Progress report", min_length=1, max_length=100)
    project_id: uuid.UUID | None = Field(None, description="One project, or all of the client's")
    show_amounts: bool = Field(False, description="Money and invoices")
    show_tasks: bool = True
    show_notes: bool = Field(False, description="Time-entry notes as a work log")
    expires_in_days: int | None = Field(90, ge=1, le=3650, description="null = never")


class LinkPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    label: str | None = Field(None, min_length=1, max_length=100)
    show_amounts: bool | None = None
    show_tasks: bool | None = None
    show_notes: bool | None = None
    expires_at: datetime | None = None


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    client_id: uuid.UUID
    project_id: uuid.UUID | None
    project_name: str | None = None
    label: str
    show_amounts: bool
    show_tasks: bool
    show_notes: bool
    expires_at: datetime | None
    revoked_at: datetime | None
    active: bool = True
    last_viewed_at: datetime | None
    view_count: int
    path: str = Field("", description="/r/<token> on this app (tailnet)")
    public_url: str | None = Field(None, description="On TAHGUL_PUBLIC_REPORT_URL, if set")
    created_at: datetime


def _out(link: ReportLink) -> LinkOut:
    o = LinkOut.model_validate(link)
    token = crypto.decrypt(link.token_enc)
    o.path = f"/r/{token}"
    base = get_settings().public_report_url
    o.public_url = f"{base.rstrip('/')}/r/{token}" if base else None
    o.project_name = link.project.name if link.project else None
    o.active = cr.find_by_link(link)
    return o


@router.get("/clients/{client_id}/report-links", response_model=list[LinkOut])
def list_links(client_id: uuid.UUID, _: Principal = Depends(admin), db: Session = Depends(get_db)):
    get_live(db, Client, client_id, "client")
    rows = db.scalars(
        select(ReportLink)
        .where(ReportLink.client_id == client_id, ReportLink.deleted_at.is_(None))
        .order_by(ReportLink.created_at.desc())
    ).all()
    return [_out(r) for r in rows]


@router.post("/clients/{client_id}/report-links", response_model=LinkOut, status_code=201)
def create_link(
    client_id: uuid.UUID,
    body: LinkIn,
    principal: Principal = Depends(admin),
    db: Session = Depends(get_db),
):
    client = get_live(db, Client, client_id, "client")
    if body.project_id:
        p = get_live(db, Project, body.project_id, "project")
        if p.client_id != client.id:
            raise HTTPException(422, detail="project_of_other_client")
    token = cr.new_token()
    link = ReportLink(
        workspace_id=client.workspace_id,
        client_id=client.id,
        project_id=body.project_id,
        label=body.label,
        token_hash=cr.token_hash(token),
        token_enc=crypto.encrypt(token),
        show_amounts=body.show_amounts,
        show_tasks=body.show_tasks,
        show_notes=body.show_notes,
        expires_at=utcnow() + timedelta(days=body.expires_in_days)
        if body.expires_in_days
        else None,
    )
    db.add(link)
    db.flush()
    audit.record(
        db,
        principal,
        "create",
        "report_link",
        link.id,
        {"client": client.name, "amounts": body.show_amounts},
    )
    db.commit()
    return _out(link)


@router.patch("/report-links/{link_id}", response_model=LinkOut)
def update_link(
    link_id: uuid.UUID,
    body: LinkPatch,
    principal: Principal = Depends(admin),
    db: Session = Depends(get_db),
):
    link = get_live(db, ReportLink, link_id, "report_link")
    changes = apply_changes(link, body.model_dump(exclude_unset=True))
    if changes:
        audit.record(db, principal, "update", "report_link", link.id, changes)
        db.commit()
    return _out(link)


@router.delete("/report-links/{link_id}", status_code=204)
def revoke_link(
    link_id: uuid.UUID, principal: Principal = Depends(admin), db: Session = Depends(get_db)
):
    """Turn the link off for good. The URL stops working immediately."""
    link = get_live(db, ReportLink, link_id, "report_link")
    link.revoked_at = link.revoked_at or utcnow()
    audit.record(db, principal, "revoke", "report_link", link.id, {"label": link.label})
    db.commit()
