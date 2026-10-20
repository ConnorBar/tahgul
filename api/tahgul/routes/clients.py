import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tahgul import audit
from tahgul.crud import (
    Name,
    PercentIn,
    PercentOut,
    RateIn,
    TwoPlaces,
    apply_changes,
    ensure_unique_name,
    get_live,
    guard_rate_fields,
    patch_data,
    require_admin,
    resolve_workspace,
    snapshot,
)
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import Client, Project
from tahgul.principal import Principal
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1/clients", tags=["clients"])

RATE_FIELDS = {"rate"}
NULLABLE = {
    "code",
    "contact_email",
    "billing_address",
    "rate",
    "payment_terms_days",
    "tax_rate",
    "notes",
}


def _check_code(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    v = v.strip().upper()
    if not v.replace("-", "").isalnum() or len(v) > 20:
        raise ValueError("use up to 20 letters, digits or dashes, e.g. ACME")
    return v


class ClientFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    code: str | None = Field(None, description="Short code for invoice numbers, e.g. ACME")
    contact_email: EmailStr | None = None
    billing_address: str | None = Field(None, max_length=2000)
    rate: RateIn | None = None
    payment_terms_days: int | None = Field(None, ge=0, le=365)
    tax_rate: PercentIn | None = None
    notes: str | None = Field(None, max_length=10000)

    _code = field_validator("code")(_check_code)


class ClientIn(ClientFields):
    workspace_id: uuid.UUID | None = Field(
        None, description="Optional when there is only one workspace."
    )
    name: Name


class ClientPatch(ClientFields):
    name: Name | None = None


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    code: str | None
    contact_email: str | None
    billing_address: str | None
    rate: TwoPlaces | None
    payment_terms_days: int | None
    tax_rate: PercentOut | None
    notes: str | None
    project_count: int = 0
    created_at: datetime
    updated_at: datetime


def _counts(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = db.execute(
        select(Project.client_id, func.count())
        .where(Project.client_id.in_(ids), Project.deleted_at.is_(None))
        .group_by(Project.client_id)
    ).all()
    return dict(rows)


def _out(client: Client, count: int) -> ClientOut:
    out = ClientOut.model_validate(client)
    out.project_count = count
    return out


@router.get("", response_model=list[ClientOut])
def list_clients(
    workspace_id: uuid.UUID | None = None,
    q: str | None = None,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    query = select(Client).where(Client.deleted_at.is_(None)).order_by(func.lower(Client.name))
    if workspace_id:
        query = query.where(Client.workspace_id == workspace_id)
    if q:
        query = query.where(Client.name.ilike(f"%{q.strip()}%"))
    clients = db.scalars(query).all()
    counts = _counts(db, [c.id for c in clients])
    return [_out(c, counts.get(c.id, 0)) for c in clients]


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(
    body: ClientIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    ws = resolve_workspace(db, body.workspace_id)
    data = body.model_dump(exclude={"workspace_id"})
    guard_rate_fields(principal, {k: v for k, v in data.items() if v is not None}, RATE_FIELDS)
    ensure_unique_name(db, Client, body.name, workspace_id=ws.id)
    client = Client(workspace_id=ws.id, **data)
    db.add(client)
    db.flush()
    audit.record(db, principal, "create", "client", client.id, snapshot(data))
    db.commit()
    return _out(client, 0)


@router.get("/{client_id}", response_model=ClientOut)
def get_client(
    client_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    client = get_live(db, Client, client_id, "client")
    return _out(client, _counts(db, [client.id]).get(client.id, 0))


@router.patch("/{client_id}", response_model=ClientOut)
def update_client(
    client_id: uuid.UUID,
    body: ClientPatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    client = get_live(db, Client, client_id, "client")
    data = patch_data(body, NULLABLE)
    guard_rate_fields(principal, data, RATE_FIELDS)
    if "name" in data:
        ensure_unique_name(
            db, Client, data["name"], exclude_id=client.id, workspace_id=client.workspace_id
        )
    changes = apply_changes(client, data)
    if changes:
        audit.record(db, principal, "update", "client", client.id, changes)
        db.commit()
        db.refresh(client)
    return _out(client, _counts(db, [client.id]).get(client.id, 0))


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(
    client_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    require_admin(principal, "deleting")
    client = get_live(db, Client, client_id, "client")
    if _counts(db, [client.id]).get(client.id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="client_has_projects")
    client.deleted_at = utcnow()
    audit.record(db, principal, "delete", "client", client.id, {"name": client.name})
    db.commit()
