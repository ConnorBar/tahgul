import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tahgul import audit
from tahgul.crud import (
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
from tahgul.models import Activity
from tahgul.principal import Principal
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1/activities", tags=["activities"])

RATE_FIELDS = {"rate"}
NULLABLE = {"rate"}
ActivityName = Field(min_length=1, max_length=100)


class ActivityIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    workspace_id: uuid.UUID | None = Field(
        None, description="Optional when there is only one workspace."
    )
    name: str = ActivityName
    rate: RateIn | None = None
    billable: bool = True


class ActivityPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(None, min_length=1, max_length=100)
    rate: RateIn | None = None
    billable: bool | None = None


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    rate: TwoPlaces | None
    billable: bool
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=list[ActivityOut])
def list_activities(
    workspace_id: uuid.UUID | None = None,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    q = select(Activity).where(Activity.deleted_at.is_(None)).order_by(func.lower(Activity.name))
    if workspace_id:
        q = q.where(Activity.workspace_id == workspace_id)
    return db.scalars(q).all()


@router.post("", response_model=ActivityOut, status_code=status.HTTP_201_CREATED)
def create_activity(
    body: ActivityIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    ws = resolve_workspace(db, body.workspace_id)
    data = body.model_dump(exclude={"workspace_id"})
    guard_rate_fields(principal, {k: v for k, v in data.items() if v is not None}, RATE_FIELDS)
    ensure_unique_name(db, Activity, body.name, workspace_id=ws.id)
    act = Activity(workspace_id=ws.id, **data)
    db.add(act)
    db.flush()
    audit.record(db, principal, "create", "activity", act.id, snapshot(data))
    db.commit()
    return act


@router.get("/{activity_id}", response_model=ActivityOut)
def get_activity(
    activity_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return get_live(db, Activity, activity_id, "activity")


@router.patch("/{activity_id}", response_model=ActivityOut)
def update_activity(
    activity_id: uuid.UUID,
    body: ActivityPatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    act = get_live(db, Activity, activity_id, "activity")
    data = patch_data(body, NULLABLE)
    guard_rate_fields(principal, data, RATE_FIELDS)
    if "name" in data:
        ensure_unique_name(
            db, Activity, data["name"], exclude_id=act.id, workspace_id=act.workspace_id
        )
    changes = apply_changes(act, data)
    if changes:
        audit.record(db, principal, "update", "activity", act.id, changes)
        db.commit()
        db.refresh(act)
    return act


@router.delete("/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_activity(
    activity_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    require_admin(principal, "deleting")
    act = get_live(db, Activity, activity_id, "activity")
    act.deleted_at = utcnow()
    audit.record(db, principal, "delete", "activity", act.id, {"name": act.name})
    db.commit()
