import re
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from tahgul import audit, rates
from tahgul.crud import (
    HoursIn,
    MoneyIn,
    Name,
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
from tahgul.models import Activity, Client, Project
from tahgul.principal import Principal
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

Status = Literal["idea", "active", "paused", "done"]
RATE_FIELDS = {"rate"}
NULLABLE = {"client_id", "description", "rate", "budget_hours", "budget_amount"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _check_colour(v: str | None) -> str | None:
    if v is None:
        return v
    if not HEX.match(v):
        raise ValueError("use a hex colour like #2f5d50")
    return v.lower()


class ProjectFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    client_id: uuid.UUID | None = None
    description: str | None = Field(None, max_length=20000)
    rate: RateIn | None = None
    budget_hours: HoursIn | None = None
    budget_amount: MoneyIn | None = None
    colour: str | None = None

    _colour = field_validator("colour")(_check_colour)


class ProjectIn(ProjectFields):
    workspace_id: uuid.UUID | None = Field(
        None, description="Optional when there is only one workspace or a client is given."
    )
    name: Name
    status: Status = "active"
    colour: str = "#2f5d50"


class ProjectPatch(ProjectFields):
    name: Name | None = None
    status: Status | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    client_id: uuid.UUID | None
    client_name: str | None = None
    name: str
    description: str | None
    status: str
    rate: TwoPlaces | None
    budget_hours: TwoPlaces | None
    budget_amount: TwoPlaces | None
    colour: str
    effective_rate: TwoPlaces | None = Field(
        None, description="Rate for work with no activity override, after resolution."
    )
    rate_source: str | None = Field(None, description="project, client or workspace")
    created_at: datetime
    updated_at: datetime


class RateLevel(BaseModel):
    level: str
    name: str | None
    rate: TwoPlaces | None


class RateOut(BaseModel):
    rate: TwoPlaces | None
    source: str | None
    currency: str
    levels: list[RateLevel] = Field(description="Every level, most specific first.")


def _out(p: Project) -> ProjectOut:
    out = ProjectOut.model_validate(p)
    out.client_name = p.client.name if p.client else None
    resolved = rates.project_rate(p)
    out.effective_rate, out.rate_source = resolved.rate, resolved.source
    return out


def _client_in_workspace(db: Session, client_id: uuid.UUID, workspace_id: uuid.UUID) -> Client:
    client = get_live(db, Client, client_id, "client")
    if client.workspace_id != workspace_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="client_in_other_workspace"
        )
    return client


def _load(db: Session, project_id: uuid.UUID) -> Project:
    p = db.scalar(
        select(Project)
        .options(selectinload(Project.client), selectinload(Project.workspace))
        .where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="project_not_found")
    return p


@router.get("", response_model=list[ProjectOut])
def list_projects(
    workspace_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    status_: Status | None = Query(None, alias="status"),
    q: str | None = None,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    query = (
        select(Project)
        .options(selectinload(Project.client), selectinload(Project.workspace))
        .where(Project.deleted_at.is_(None))
        .order_by(func.lower(Project.name))
    )
    if workspace_id:
        query = query.where(Project.workspace_id == workspace_id)
    if client_id:
        query = query.where(Project.client_id == client_id)
    if status_:
        query = query.where(Project.status == status_)
    if q:
        query = query.where(Project.name.ilike(f"%{q.strip()}%"))
    return [_out(p) for p in db.scalars(query).all()]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    if body.workspace_id is None and body.client_id is not None:
        workspace_id = get_live(db, Client, body.client_id, "client").workspace_id
    else:
        workspace_id = resolve_workspace(db, body.workspace_id).id
    if body.client_id is not None:
        _client_in_workspace(db, body.client_id, workspace_id)
    data = body.model_dump(exclude={"workspace_id"})
    guard_rate_fields(principal, {k: v for k, v in data.items() if v is not None}, RATE_FIELDS)
    ensure_unique_name(db, Project, body.name, workspace_id=workspace_id)
    p = Project(workspace_id=workspace_id, **data)
    db.add(p)
    db.flush()
    audit.record(db, principal, "create", "project", p.id, snapshot(data))
    db.commit()
    return _out(_load(db, p.id))


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return _out(_load(db, project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: uuid.UUID,
    body: ProjectPatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    p = _load(db, project_id)
    data = patch_data(body, NULLABLE)
    guard_rate_fields(principal, data, RATE_FIELDS)
    if data.get("client_id") is not None:
        _client_in_workspace(db, data["client_id"], p.workspace_id)
    if "name" in data:
        ensure_unique_name(db, Project, data["name"], exclude_id=p.id, workspace_id=p.workspace_id)
    changes = apply_changes(p, data)
    if changes:
        audit.record(db, principal, "update", "project", p.id, changes)
        db.commit()
        db.expire_all()
    return _out(_load(db, project_id))


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    require_admin(principal, "deleting")
    p = _load(db, project_id)
    p.deleted_at = utcnow()
    audit.record(db, principal, "delete", "project", p.id, {"name": p.name})
    db.commit()


@router.get("/{project_id}/rate", response_model=RateOut)
def effective_rate(
    project_id: uuid.UUID,
    activity_id: uuid.UUID | None = None,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """The rate work on this project would be billed at, and where it comes from."""
    p = _load(db, project_id)
    activity = None
    if activity_id is not None:
        activity = get_live(db, Activity, activity_id, "activity")
        if activity.workspace_id != p.workspace_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail="activity_in_other_workspace"
            )
    resolved = rates.project_rate(p, activity)
    client = p.client if p.client and not p.client.deleted_at else None
    levels = [
        RateLevel(
            level="activity",
            name=activity.name if activity else None,
            rate=activity.rate if activity else None,
        ),
        RateLevel(level="project", name=p.name, rate=p.rate),
        RateLevel(
            level="client",
            name=client.name if client else None,
            rate=client.rate if client else None,
        ),
        RateLevel(level="workspace", name=p.workspace.name, rate=p.workspace.default_rate),
    ]
    return RateOut(
        rate=resolved.rate, source=resolved.source, currency=p.workspace.currency, levels=levels
    )
