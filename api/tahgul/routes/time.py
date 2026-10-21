"""The running timer and time entries (SPEC: features, time tracking)."""

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from tahgul import audit
from tahgul import timekeeping as tk
from tahgul.crud import (
    RateIn,
    TwoPlaces,
    apply_changes,
    get_live,
    guard_rate_fields,
    patch_data,
    require_admin,
    resolve_workspace,
    snapshot,
)
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import Activity, Project, TimeEntry, Workspace
from tahgul.principal import Principal
from tahgul.timeutil import as_utc, utcnow

router = APIRouter(prefix="/api/v1", tags=["time"])

RATE_FIELDS = {"rate_override"}
# Changing any of these re-resolves the frozen rate and amount.
PRICING_FIELDS = {"project_id", "activity_id", "rate_override", "start_at", "end_at"}


# --- Schemas -------------------------------------------------------------------------


class TimeEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID | None
    project_name: str | None = None
    client_id: uuid.UUID | None = None
    client_name: str | None = None
    activity_id: uuid.UUID | None
    activity_name: str | None = None
    task_id: uuid.UUID | None
    start_at: datetime
    end_at: datetime | None
    duration_seconds: int = Field(description="Elapsed so far while running.")
    hours: TwoPlaces
    notes: str | None
    billable: bool
    rate_override: TwoPlaces | None
    rate: TwoPlaces | None = Field(description="Frozen when the entry was saved.")
    rate_source: str | None
    amount: TwoPlaces | None
    source: str
    invoice_id: uuid.UUID | None
    running: bool
    overlaps: list[uuid.UUID] | None = Field(
        None, description="Other entries this one overlaps. Only on writes; a warning."
    )


class EntryFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    project_id: uuid.UUID | None = None
    activity_id: uuid.UUID | None = None
    notes: str | None = Field(None, max_length=5000)
    billable: bool | None = Field(None, description="Defaults to the activity's setting.")
    rate_override: RateIn | None = None


class TimeEntryIn(EntryFields):
    workspace_id: uuid.UUID | None = None
    start_at: datetime = Field(description="Naive times are read in the workspace time zone.")
    end_at: datetime | None = None
    duration_minutes: int | None = Field(None, gt=0, le=7 * 24 * 60)

    @model_validator(mode="after")
    def end_or_duration(self):
        if (self.end_at is None) == (self.duration_minutes is None):
            raise ValueError("give either end_at or duration_minutes")
        return self


class TimeEntryPatch(EntryFields):
    start_at: datetime | None = None
    end_at: datetime | None = None


class TimerStart(EntryFields):
    workspace_id: uuid.UUID | None = None
    start_at: datetime | None = Field(None, description="Defaults to now.")


class TimerStop(BaseModel):
    end_at: datetime | None = Field(None, description="Defaults to now; earlier trims it.")


class TimerPatch(EntryFields):
    start_at: datetime | None = None


class TimerOut(BaseModel):
    running: TimeEntryOut | None
    elapsed_seconds: int
    long_running: bool = Field(description="Past the workspace's warning limit.")
    warning_after_minutes: int | None
    stopped: TimeEntryOut | None = Field(None, description="Entry stopped by this start.")


class RepriceIn(BaseModel):
    workspace_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None


class RepriceOut(BaseModel):
    checked: int
    changed: int


# --- Helpers -------------------------------------------------------------------------


def _q():
    return select(TimeEntry).options(
        selectinload(TimeEntry.workspace),
        selectinload(TimeEntry.activity),
        selectinload(TimeEntry.project).selectinload(Project.client),
        selectinload(TimeEntry.project).selectinload(Project.workspace),
    )


def load(db: Session, entry_id: uuid.UUID) -> TimeEntry:
    e = db.scalar(_q().where(TimeEntry.id == entry_id, TimeEntry.deleted_at.is_(None)))
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="time_entry_not_found")
    return e


def running_entry(db: Session) -> TimeEntry | None:
    return db.scalar(_q().where(TimeEntry.end_at.is_(None), TimeEntry.deleted_at.is_(None)))


def out(e: TimeEntry, overlaps: list[TimeEntry] | None = None) -> TimeEntryOut:
    secs = tk.elapsed_seconds(e)
    o = TimeEntryOut.model_validate(
        {
            **{c: getattr(e, c) for c in TimeEntryOut.model_fields if hasattr(e, c)},
            "duration_seconds": secs,
            "hours": tk.hours(secs),
            "running": e.running,
        }
    )
    o.project_name = e.project.name if e.project else None
    o.client_id = e.project.client_id if e.project else None
    o.client_name = e.project.client.name if e.project and e.project.client else None
    o.activity_name = e.activity.name if e.activity else None
    if overlaps is not None:
        o.overlaps = [x.id for x in overlaps]
    return o


def _refs(
    db: Session, workspace_id: uuid.UUID | None, project_id, activity_id
) -> tuple[Workspace, Project | None, Activity | None]:
    """Resolve the workspace from the project when given, and check everything matches."""
    project = get_live(db, Project, project_id, "project") if project_id else None
    if project is not None and workspace_id is None:
        workspace_id = project.workspace_id
    ws = resolve_workspace(db, workspace_id)
    if project is not None and project.workspace_id != ws.id:
        raise HTTPException(422, detail="project_in_other_workspace")
    activity = get_live(db, Activity, activity_id, "activity") if activity_id else None
    if activity is not None and activity.workspace_id != ws.id:
        raise HTTPException(422, detail="activity_in_other_workspace")
    return ws, project, activity


def _source(principal: Principal, default: str) -> str:
    return "agent" if principal.kind == "token" else default


def _check_span(start: datetime, end: datetime) -> None:
    if end <= start:
        raise HTTPException(422, detail="end_before_start")
    if end - start > tk.MAX_ENTRY:
        raise HTTPException(422, detail="entry_too_long")


def _locked(e: TimeEntry) -> None:
    if e.invoice_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="entry_invoiced")


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:  # the one-running-timer index
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="timer_already_running") from exc


def _timer_out(db: Session, stopped: TimeEntry | None = None) -> TimerOut:
    e = running_entry(db)
    secs = tk.elapsed_seconds(e) if e else 0
    limit = e.workspace.timer_warning_minutes if e else None
    return TimerOut(
        running=out(e) if e else None,
        elapsed_seconds=secs,
        long_running=bool(e and limit and secs > limit * 60),
        warning_after_minutes=limit,
        stopped=out(stopped) if stopped else None,
    )


# --- Timer ---------------------------------------------------------------------------


@router.get("/timer", response_model=TimerOut, tags=["timer"])
def get_timer(_: Principal = Depends(require_scope("read")), db: Session = Depends(get_db)):
    """What's running and for how long."""
    return _timer_out(db)


@router.post("/timer/start", response_model=TimerOut, tags=["timer"])
def start_timer(
    body: TimerStart,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Start the timer. A timer already running is stopped first (one at a time)."""
    guard_rate_fields(principal, body.model_dump(exclude_none=True), RATE_FIELDS)
    ws, project, activity = _refs(db, body.workspace_id, body.project_id, body.activity_id)
    now = utcnow()
    start = as_utc(tk.ensure_aware(body.start_at, tk.tz(ws))) if body.start_at else now
    if start > now + timedelta(minutes=1):
        raise HTTPException(422, detail="start_in_future")

    stopped = running_entry(db)
    if stopped is not None:
        tk.close(stopped, max(start, as_utc(stopped.start_at)))
        tk.price(stopped)
        audit.record(db, principal, "stop", "time_entry", stopped.id)
        db.flush()

    e = TimeEntry(
        workspace=ws,
        project=project,
        activity=activity,
        start_at=start,
        notes=body.notes,
        billable=body.billable if body.billable is not None else _billable(activity),
        rate_override=body.rate_override,
        source=_source(principal, "timer"),
    )
    tk.price(e)
    db.add(e)
    db.flush()
    audit.record(
        db, principal, "start", "time_entry", e.id, snapshot({"project": project and project.name})
    )
    _commit(db)
    return _timer_out(db, stopped)


def _billable(activity: Activity | None) -> bool:
    return activity.billable if activity else True


@router.post("/timer/stop", response_model=TimeEntryOut, tags=["timer"])
def stop_timer(
    body: TimerStop | None = None,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Stop the running timer and return the finished entry."""
    e = running_entry(db)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no_timer_running")
    now = utcnow()
    end = now
    if body and body.end_at:
        end = as_utc(tk.ensure_aware(body.end_at, tk.tz(e.workspace)))
        if end > now:
            end = now
    if end <= as_utc(e.start_at):
        raise HTTPException(422, detail="end_before_start")
    tk.close(e, end)
    tk.price(e)
    audit.record(db, principal, "stop", "time_entry", e.id, {"seconds": e.duration_seconds})
    db.commit()
    return out(load(db, e.id), tk.overlapping(db, e))


@router.patch("/timer", response_model=TimerOut, tags=["timer"])
def update_timer(
    body: TimerPatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Change what the running timer is for, or when it started."""
    e = running_entry(db)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no_timer_running")
    _apply_patch(db, e, body, principal)
    return _timer_out(db)


@router.delete("/timer", status_code=status.HTTP_204_NO_CONTENT, tags=["timer"])
def discard_timer(
    principal: Principal = Depends(require_scope("write")), db: Session = Depends(get_db)
):
    """Throw away the running timer without logging it."""
    e = running_entry(db)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no_timer_running")
    e.deleted_at = utcnow()
    audit.record(db, principal, "discard", "time_entry", e.id)
    db.commit()


# --- Entries -------------------------------------------------------------------------


@router.get("/time-entries", response_model=list[TimeEntryOut])
def list_entries(
    workspace_id: uuid.UUID | None = None,
    from_: date | None = Query(None, alias="from", description="Local date, inclusive"),
    to: date | None = Query(None, description="Local date, inclusive"),
    project_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    billable: bool | None = None,
    invoiced: bool | None = None,
    limit: int = Query(500, ge=1, le=5000),
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    ws = resolve_workspace(db, workspace_id)
    q = _q().where(TimeEntry.workspace_id == ws.id, TimeEntry.deleted_at.is_(None))
    zone = tk.tz(ws)
    if from_:
        q = q.where(TimeEntry.start_at >= tk.local_day_start(from_, zone))
    if to:
        q = q.where(TimeEntry.start_at < tk.local_day_start(to + timedelta(days=1), zone))
    if project_id:
        q = q.where(TimeEntry.project_id == project_id)
    if client_id:
        q = q.where(
            TimeEntry.project_id.in_(select(Project.id).where(Project.client_id == client_id))
        )
    if billable is not None:
        q = q.where(TimeEntry.billable.is_(billable))
    if invoiced is not None:
        q = q.where(
            TimeEntry.invoice_id.is_not(None) if invoiced else TimeEntry.invoice_id.is_(None)
        )
    q = q.order_by(TimeEntry.start_at.desc()).limit(limit)
    return [out(e) for e in db.scalars(q).all()]


@router.post("/time-entries", response_model=TimeEntryOut, status_code=status.HTTP_201_CREATED)
def create_entry(
    body: TimeEntryIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Log time after the fact, e.g. two hours on Acme yesterday afternoon."""
    guard_rate_fields(principal, body.model_dump(exclude_none=True), RATE_FIELDS)
    ws, project, activity = _refs(db, body.workspace_id, body.project_id, body.activity_id)
    zone = tk.tz(ws)
    start = as_utc(tk.ensure_aware(body.start_at, zone))
    end = (
        as_utc(tk.ensure_aware(body.end_at, zone))
        if body.end_at
        else start + timedelta(minutes=body.duration_minutes)
    )
    _check_span(start, end)
    e = TimeEntry(
        workspace=ws,
        project=project,
        activity=activity,
        start_at=start,
        notes=body.notes,
        billable=body.billable if body.billable is not None else _billable(activity),
        rate_override=body.rate_override,
        source=_source(principal, "manual"),
    )
    tk.close(e, end)
    tk.price(e)
    db.add(e)
    db.flush()
    audit.record(
        db,
        principal,
        "create",
        "time_entry",
        e.id,
        snapshot({"project": project and project.name, "seconds": e.duration_seconds}),
    )
    db.commit()
    return out(load(db, e.id), tk.overlapping(db, e))


@router.get("/time-entries/{entry_id}", response_model=TimeEntryOut)
def get_entry(
    entry_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return out(load(db, entry_id))


def _apply_patch(db: Session, e: TimeEntry, body: BaseModel, principal: Principal) -> TimeEntry:
    data = patch_data(body, {"project_id", "activity_id", "notes", "rate_override"})
    guard_rate_fields(principal, data, RATE_FIELDS)
    if "end_at" in data and e.running:
        raise HTTPException(422, detail="use_timer_stop")
    _, project, activity = _refs(
        db,
        e.workspace_id,
        data.get("project_id", e.project_id),
        data.get("activity_id", e.activity_id),
    )
    zone = tk.tz(e.workspace)
    for k in ("start_at", "end_at"):
        if k in data:
            data[k] = as_utc(tk.ensure_aware(data[k], zone))
    start = data.get("start_at", as_utc(e.start_at))
    if e.running:
        if start > utcnow():
            raise HTTPException(422, detail="start_in_future")
    else:
        _check_span(start, data.get("end_at", as_utc(e.end_at)))

    changes = apply_changes(e, data)
    if not changes:
        return e
    if "project_id" in data:
        e.project = project
    if "activity_id" in data:
        e.activity = activity
    if not e.running and ({"start_at", "end_at"} & changes.keys()):
        tk.close(e, e.end_at)
    if PRICING_FIELDS & changes.keys():
        tk.price(e)
    audit.record(db, principal, "update", "time_entry", e.id, changes)
    db.commit()
    return e


@router.patch("/time-entries/{entry_id}", response_model=TimeEntryOut)
def update_entry(
    entry_id: uuid.UUID,
    body: TimeEntryPatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    e = load(db, entry_id)
    _locked(e)
    _apply_patch(db, e, body, principal)
    return out(load(db, entry_id), tk.overlapping(db, e))


@router.delete("/time-entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(
    entry_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    require_admin(principal, "deleting")
    e = load(db, entry_id)
    _locked(e)
    e.deleted_at = utcnow()
    audit.record(db, principal, "delete", "time_entry", e.id)
    db.commit()


@router.post("/time-entries/reprice", response_model=RepriceOut)
def reprice(
    body: RepriceIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Re-price unbilled entries at today's rates ("re-price unbilled entries")."""
    require_admin(principal, "re-pricing")
    ws = resolve_workspace(db, body.workspace_id)
    q = _q().where(
        TimeEntry.workspace_id == ws.id,
        TimeEntry.deleted_at.is_(None),
        TimeEntry.invoice_id.is_(None),
        TimeEntry.end_at.is_not(None),
    )
    if body.project_id:
        q = q.where(TimeEntry.project_id == body.project_id)
    if body.client_id:
        q = q.where(
            TimeEntry.project_id.in_(select(Project.id).where(Project.client_id == body.client_id))
        )
    entries = db.scalars(q).all()
    changed = 0
    for e in entries:
        before = (e.rate, e.amount)
        tk.price(e)
        if (e.rate, e.amount) != before:
            changed += 1
    if changed:
        audit.record(
            db,
            principal,
            "reprice",
            "time_entry",
            None,
            {"checked": len(entries), "changed": changed},
        )
    db.commit()
    return RepriceOut(checked=len(entries), changed=changed)
