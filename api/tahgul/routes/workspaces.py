import uuid
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from tahgul import audit, numbering
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
    snapshot,
)
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import Activity, Client, Project, Workspace
from tahgul.principal import Principal
from tahgul.timekeeping import ROUNDING_MINUTES
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1", tags=["workspaces"])

RATE_FIELDS = {"default_rate"}


def _check_currency(v: str | None) -> str | None:
    if v is None:
        return v
    v = v.strip().upper()
    if len(v) != 3 or not v.isalpha():
        raise ValueError("use a three-letter ISO currency code, e.g. USD")
    return v


def _check_timezone(v: str | None) -> str | None:
    if v is None:
        return v
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("use an IANA time zone, e.g. America/Chicago") from exc
    return v


def _check_rounding(v: int | None) -> int | None:
    if v is not None and v not in ROUNDING_MINUTES:
        raise ValueError(f"use one of {', '.join(map(str, ROUNDING_MINUTES))} (0 = no rounding)")
    return v


def _check_pattern(v: str | None) -> str | None:
    if v is None:
        return v
    errors = numbering.validate_pattern(v)
    if errors:
        raise ValueError(" ".join(errors))
    return v


class WorkspaceIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: Name
    currency: str = "USD"
    default_rate: RateIn | None = None
    tax_rate: PercentIn | None = Field(None, description="Percent. Blank = no tax line.")
    payment_terms_days: int = Field(30, ge=0, le=365)
    invoice_number_pattern: str = numbering.PRESETS[0].pattern
    timezone: str = "America/Chicago"
    rounding_minutes: int = Field(0, description="Round billed time to this many minutes.")
    rounding_mode: Literal["up", "nearest", "down"] = "up"
    timer_warning_minutes: int = Field(240, ge=15, le=24 * 60)

    _currency = field_validator("currency")(_check_currency)
    _tz = field_validator("timezone")(_check_timezone)
    _pattern = field_validator("invoice_number_pattern")(_check_pattern)
    _rounding = field_validator("rounding_minutes")(_check_rounding)


class WorkspacePatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: Name | None = None
    currency: str | None = None
    default_rate: RateIn | None = None
    tax_rate: PercentIn | None = None
    payment_terms_days: int | None = Field(None, ge=0, le=365)
    invoice_number_pattern: str | None = None
    timezone: str | None = None
    rounding_minutes: int | None = None
    rounding_mode: Literal["up", "nearest", "down"] | None = None
    timer_warning_minutes: int | None = Field(None, ge=15, le=24 * 60)

    _currency = field_validator("currency")(_check_currency)
    _tz = field_validator("timezone")(_check_timezone)
    _pattern = field_validator("invoice_number_pattern")(_check_pattern)
    _rounding = field_validator("rounding_minutes")(_check_rounding)


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    currency: str
    default_rate: TwoPlaces | None
    tax_rate: PercentOut | None
    payment_terms_days: int
    invoice_number_pattern: str
    timezone: str
    rounding_minutes: int
    rounding_mode: str
    timer_warning_minutes: int
    created_at: datetime
    updated_at: datetime


# Fields that may be cleared with an explicit null in a PATCH.
NULLABLE = {"default_rate", "tax_rate"}


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(_: Principal = Depends(require_scope("read")), db: Session = Depends(get_db)):
    return db.scalars(
        select(Workspace).where(Workspace.deleted_at.is_(None)).order_by(Workspace.created_at)
    ).all()


@router.post("/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(
    body: WorkspaceIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    data = body.model_dump()
    guard_rate_fields(principal, {k: v for k, v in data.items() if v is not None}, RATE_FIELDS)
    ensure_unique_name(db, Workspace, body.name)
    ws = Workspace(**data)
    db.add(ws)
    db.flush()
    audit.record(db, principal, "create", "workspace", ws.id, snapshot(data))
    db.commit()
    return ws


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(
    workspace_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return get_live(db, Workspace, workspace_id, "workspace")


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    workspace_id: uuid.UUID,
    body: WorkspacePatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    ws = get_live(db, Workspace, workspace_id, "workspace")
    data = patch_data(body, NULLABLE)
    guard_rate_fields(principal, data, RATE_FIELDS)
    if "name" in data:
        ensure_unique_name(db, Workspace, data["name"], exclude_id=ws.id)
    changes = apply_changes(ws, data)
    if changes:
        audit.record(db, principal, "update", "workspace", ws.id, changes)
        db.commit()
        db.refresh(ws)
    return ws


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(
    workspace_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    require_admin(principal, "deleting")
    ws = get_live(db, Workspace, workspace_id, "workspace")
    for model in (Client, Project, Activity):
        in_use = db.scalar(
            select(model.id).where(model.workspace_id == ws.id, model.deleted_at.is_(None)).limit(1)
        )
        if in_use is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="workspace_not_empty")
    ws.deleted_at = utcnow()
    audit.record(db, principal, "delete", "workspace", ws.id, {"name": ws.name})
    db.commit()


# --- Invoice number patterns -------------------------------------------------


class PresetOut(BaseModel):
    pattern: str
    label: str
    resets: str
    examples: list[str]


class PatternCheck(BaseModel):
    pattern: str
    valid: bool
    errors: list[str]
    examples: list[str]


@router.get("/invoice-numbers/presets", response_model=list[PresetOut], tags=["invoicing"])
def invoice_number_presets(_: Principal = Depends(require_scope("read"))):
    today = date.today()
    return [
        PresetOut(
            pattern=p.pattern,
            label=p.label,
            resets=p.resets,
            examples=numbering.examples(p.pattern, on=today),
        )
        for p in numbering.PRESETS
    ]


@router.get("/invoice-numbers/preview", response_model=PatternCheck, tags=["invoicing"])
def invoice_number_preview(
    pattern: str = Query(..., max_length=200),
    _: Principal = Depends(require_scope("read")),
):
    errors = numbering.validate_pattern(pattern)
    return PatternCheck(
        pattern=pattern,
        valid=not errors,
        errors=errors,
        examples=[] if errors else numbering.examples(pattern, on=date.today()),
    )
