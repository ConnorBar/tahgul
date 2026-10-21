"""Calendar accounts, calendars and events (SPEC: calendar, two-way Apple Calendar sync)."""

import uuid
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from tahgul import audit, calsync, crypto, lookup
from tahgul import timekeeping as tk
from tahgul.caldav import CalDAVError
from tahgul.crud import apply_changes, get_live, patch_data, require_admin, resolve_workspace
from tahgul.db import get_db
from tahgul.deps import require_owner, require_scope
from tahgul.models import (
    Calendar,
    CalendarAccount,
    CalendarEvent,
    Project,
    Task,
    TimeEntry,
    Workspace,
)
from tahgul.principal import Principal
from tahgul.timeutil import as_utc, utcnow

router = APIRouter(prefix="/api/v1", tags=["calendar"])

# How far back "log this as time?" looks.
PENDING_LOG_DAYS = 7


# --- Schemas -----------------------------------------------------------------------------


class AccountIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["radicale", "icloud", "caldav"] = "caldav"
    url: HttpUrl | None = Field(None, description="Not needed for iCloud")
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(
        min_length=1, max_length=500, description="For iCloud, an app-specific password"
    )

    @model_validator(mode="after")
    def need_url(self):
        if self.kind != "icloud" and self.url is None:
            raise ValueError("url is required")
        return self


class AccountPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str | None = Field(None, min_length=1, max_length=100)
    username: str | None = None
    password: str | None = None


class CalendarOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    account_id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    colour: str | None
    enabled: bool
    server_writable: bool
    writable: bool
    is_default: bool
    mirror_time_entries: bool
    last_synced_at: datetime | None


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    kind: str
    url: str
    username: str
    last_synced_at: datetime | None
    last_error: str | None
    calendars: list[CalendarOut] = []


class CalendarPatch(BaseModel):
    enabled: bool | None = None
    writable: bool | None = None
    is_default: bool | None = None
    mirror_time_entries: bool | None = None
    workspace_id: uuid.UUID | None = None
    colour: str | None = Field(None, pattern="^#[0-9a-fA-F]{6}$")


class EventOut(BaseModel):
    id: uuid.UUID
    calendar_id: uuid.UUID
    calendar_name: str
    colour: str | None
    writable: bool
    title: str
    description: str | None
    location: str | None
    start_at: datetime = Field(description="This occurrence's start")
    end_at: datetime
    all_day: bool
    recurring: bool
    project_id: uuid.UUID | None
    project_name: str | None
    task_id: uuid.UUID | None
    time_entry_id: uuid.UUID | None
    origin: str | None
    sync_pending: bool


class EventIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    calendar_id: uuid.UUID | None = Field(None, description="Default: your default calendar")
    title: str = Field(min_length=1, max_length=500)
    start_at: datetime = Field(description="Naive times are read in the workspace time zone")
    end_at: datetime | None = None
    duration_minutes: int | None = Field(None, gt=0, le=60 * 24 * 14)
    all_day: bool = False
    location: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=10_000)
    project_id: uuid.UUID | None = None
    project: str | None = Field(None, description="Project or client name")
    task_id: uuid.UUID | None = None


class EventPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str | None = Field(None, min_length=1, max_length=500)
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool | None = None
    location: str | None = None
    description: str | None = None
    project_id: uuid.UUID | None = None
    project: str | None = None


class LogTimeIn(BaseModel):
    activity_id: uuid.UUID | None = None
    notes: str | None = Field(None, max_length=5000)
    billable: bool | None = None


# --- Helpers ---------------------------------------------------------------------------

ICLOUD_URL = "https://caldav.icloud.com/"


def _account_out(a: CalendarAccount) -> AccountOut:
    out = AccountOut.model_validate(a)
    out.calendars = [CalendarOut.model_validate(c) for c in a.calendars if c.deleted_at is None]
    return out


def _event_out(
    ev: CalendarEvent, start: datetime | None = None, end: datetime | None = None
) -> EventOut:
    cal = ev.calendar
    return EventOut(
        id=ev.id,
        calendar_id=cal.id,
        calendar_name=cal.name,
        colour=cal.colour,
        writable=cal.writable and not ev.recurring,
        title=ev.title,
        description=ev.description,
        location=ev.location,
        start_at=start or as_utc(ev.start_at),
        end_at=end or as_utc(ev.end_at),
        all_day=ev.all_day,
        recurring=ev.recurring,
        project_id=ev.project_id,
        project_name=ev.project.name if ev.project else None,
        task_id=ev.task_id,
        time_entry_id=ev.time_entry_id,
        origin=ev.origin,
        sync_pending=ev.dirty,
    )


def _load_event(db: Session, event_id: uuid.UUID) -> CalendarEvent:
    ev = db.scalar(
        select(CalendarEvent)
        .options(
            selectinload(CalendarEvent.calendar).selectinload(Calendar.account),
            selectinload(CalendarEvent.calendar).selectinload(Calendar.workspace),
            selectinload(CalendarEvent.project),
        )
        .where(CalendarEvent.id == event_id, CalendarEvent.deleted_at.is_(None))
    )
    if ev is None:
        raise HTTPException(404, detail="event_not_found")
    return ev


def default_calendar(db: Session, workspace_id: uuid.UUID | None = None) -> Calendar | None:
    q = select(Calendar).where(
        Calendar.deleted_at.is_(None), Calendar.enabled.is_(True), Calendar.writable.is_(True)
    )
    if workspace_id:
        q = q.where(Calendar.workspace_id == workspace_id)
    cals = db.scalars(q.order_by(Calendar.created_at)).all()
    return next((c for c in cals if c.is_default), cals[0] if cals else None)


def _writable(cal: Calendar) -> None:
    if not cal.writable or not cal.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="calendar_read_only")


def _sync_error(exc: CalDAVError) -> HTTPException:
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY, detail={"code": "calendar_server_error", "message": str(exc)}
    )


# --- Accounts (owner only: they hold credentials) --------------------------------------


@router.get("/calendar-accounts", response_model=list[AccountOut])
def list_accounts(_: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(CalendarAccount)
        .where(CalendarAccount.deleted_at.is_(None))
        .order_by(CalendarAccount.created_at)
    ).all()
    return [_account_out(a) for a in rows]


@router.post("/calendar-accounts", response_model=AccountOut, status_code=201)
def create_account(
    body: AccountIn, principal: Principal = Depends(require_owner), db: Session = Depends(get_db)
):
    """Connect a CalDAV server, check the login and list its calendars."""
    url = ICLOUD_URL if body.kind == "icloud" else str(body.url)
    a = CalendarAccount(
        name=body.name,
        kind=body.kind,
        url=url,
        username=body.username,
        password_enc=crypto.encrypt(body.password),
    )
    db.add(a)
    db.flush()
    try:
        calsync.discover(db, a)
    except CalDAVError as exc:
        db.rollback()
        raise _sync_error(exc) from exc
    audit.record(
        db,
        principal,
        "create",
        "calendar_account",
        a.id,
        {"name": a.name, "url": url, "username": a.username},
    )
    db.commit()
    db.refresh(a)
    return _account_out(a)


@router.patch("/calendar-accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: uuid.UUID,
    body: AccountPatch,
    principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    a = get_live(db, CalendarAccount, account_id, "calendar_account")
    data = patch_data(body, set())
    if "password" in data:
        a.password_enc = crypto.encrypt(data.pop("password"))
        data_changes = {"password": "changed"}
    else:
        data_changes = {}
    changes = apply_changes(a, data) | data_changes
    if changes:
        audit.record(db, principal, "update", "calendar_account", a.id, changes)
    db.commit()
    return _account_out(a)


@router.delete("/calendar-accounts/{account_id}", status_code=204)
def delete_account(
    account_id: uuid.UUID,
    principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Disconnect. Mirrored events disappear from Tahgul; nothing is deleted on the server."""
    a = get_live(db, CalendarAccount, account_id, "calendar_account")
    now = utcnow()
    for cal in a.calendars:
        cal.deleted_at = cal.deleted_at or now
        for ev in db.scalars(
            select(CalendarEvent).where(
                CalendarEvent.calendar_id == cal.id, CalendarEvent.deleted_at.is_(None)
            )
        ):
            ev.deleted_at = now
    a.deleted_at = now
    audit.record(db, principal, "delete", "calendar_account", a.id, {"name": a.name})
    db.commit()


@router.post("/calendar-accounts/{account_id}/discover", response_model=AccountOut)
def rediscover(
    account_id: uuid.UUID, _: Principal = Depends(require_owner), db: Session = Depends(get_db)
):
    a = get_live(db, CalendarAccount, account_id, "calendar_account")
    try:
        calsync.discover(db, a)
    except CalDAVError as exc:
        raise _sync_error(exc) from exc
    db.commit()
    db.refresh(a)
    return _account_out(a)


@router.post("/calendar-accounts/{account_id}/new-calendar", response_model=AccountOut)
def create_remote_calendar(
    account_id: uuid.UUID,
    name: str = Query("Billable", min_length=1, max_length=100),
    mirror_time_entries: bool = True,
    principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Make a calendar on the server (e.g. "Billable", to mirror time entries) and enable it."""
    a = get_live(db, CalendarAccount, account_id, "calendar_account")
    client = calsync.client_for(a)
    try:
        slug = "tahgul-" + "".join(ch for ch in name.lower() if ch.isalnum())[:30]
        url = client.make_calendar(client.home(), slug, name, "#2f5d50")
    except CalDAVError as exc:
        raise _sync_error(exc) from exc
    finally:
        client.close()
    cals = calsync.discover(db, a)
    new = next(c for c in cals if c.url.rstrip("/") == url.rstrip("/"))
    new.writable = True
    new.mirror_time_entries = mirror_time_entries
    audit.record(db, principal, "create", "calendar", new.id, {"name": name})
    db.commit()
    db.refresh(a)
    return _account_out(a)


# --- Calendars ---------------------------------------------------------------------------


@router.get("/calendars", response_model=list[CalendarOut])
def list_calendars(_: Principal = Depends(require_scope("read")), db: Session = Depends(get_db)):
    return db.scalars(
        select(Calendar).where(Calendar.deleted_at.is_(None)).order_by(Calendar.created_at)
    ).all()


@router.patch("/calendars/{calendar_id}", response_model=CalendarOut)
def update_calendar(
    calendar_id: uuid.UUID,
    body: CalendarPatch,
    principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Choose which calendars sync, which Tahgul may write to, and the default."""
    cal = get_live(db, Calendar, calendar_id, "calendar")
    data = patch_data(body, set())
    if data.get("writable") and not cal.server_writable:
        raise HTTPException(409, detail="calendar_read_only_on_server")
    if data.get("workspace_id"):
        get_live(db, Workspace, data["workspace_id"], "workspace")
    if data.get("is_default"):
        for other in db.scalars(select(Calendar).where(Calendar.id != cal.id)).all():
            other.is_default = False
        data["writable"] = True if cal.server_writable else data.get("writable", cal.writable)
    if data.get("mirror_time_entries") and not (data.get("writable", cal.writable)):
        raise HTTPException(409, detail="calendar_read_only")
    changes = apply_changes(cal, data)
    if changes:
        audit.record(db, principal, "update", "calendar", cal.id, changes)
    db.commit()
    return cal


@router.post("/calendars/sync")
def sync_now(_: Principal = Depends(require_scope("write")), db: Session = Depends(get_db)):
    """Pull and push every account now ("sync now")."""
    return calsync.sync_all(db)


# --- Events ------------------------------------------------------------------------------


@router.get("/events", response_model=list[EventOut])
def list_events(
    from_: date = Query(..., alias="from", description="Local date, inclusive"),
    to: date = Query(..., description="Local date, inclusive"),
    workspace_id: uuid.UUID | None = None,
    calendar_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Events in a date range, recurring ones expanded into occurrences."""
    if to < from_ or (to - from_).days > 370:
        raise HTTPException(422, detail="bad_range")
    ws = resolve_workspace(db, workspace_id)
    zone = ZoneInfo(ws.timezone)
    start, end = tk.day_range(from_, to, zone)
    q = (
        select(CalendarEvent)
        .options(selectinload(CalendarEvent.calendar), selectinload(CalendarEvent.project))
        .join(CalendarEvent.calendar)
        .where(
            CalendarEvent.deleted_at.is_(None),
            CalendarEvent.pending_delete.is_(False),
            Calendar.deleted_at.is_(None),
            Calendar.enabled.is_(True),
            Calendar.workspace_id == ws.id,
            or_(
                CalendarEvent.recurring.is_(True),
                (CalendarEvent.start_at < end) & (CalendarEvent.end_at > start),
            ),
        )
    )
    if calendar_id:
        q = q.where(CalendarEvent.calendar_id == calendar_id)
    if project_id:
        q = q.where(CalendarEvent.project_id == project_id)
    out = []
    for ev in db.scalars(q).all():
        for s, e in calsync.occurrences(ev, start, end, zone):
            out.append(_event_out(ev, s, e))
    out.sort(key=lambda x: x.start_at)
    return out


def _times(body, zone: ZoneInfo, current: CalendarEvent | None = None):
    start = body.start_at if body.start_at is not None else (current and as_utc(current.start_at))
    start = as_utc(tk.ensure_aware(start, zone))
    if getattr(body, "end_at", None) is not None:
        end = as_utc(tk.ensure_aware(body.end_at, zone))
    elif getattr(body, "duration_minutes", None):
        end = start + timedelta(minutes=body.duration_minutes)
    elif current is not None:
        end = start + (as_utc(current.end_at) - as_utc(current.start_at))
    else:
        end = start + timedelta(hours=1)
    all_day = body.all_day if body.all_day is not None else (current.all_day if current else False)
    if all_day:
        # Whole local days; the end is exclusive, like iCalendar's DTEND for dates.
        s_local = start.astimezone(zone).date()
        e_wall = end.astimezone(zone)
        e_local = e_wall.date() + (timedelta(days=1) if e_wall.time() != time(0) else timedelta(0))
        e_local = max(e_local, s_local + timedelta(days=1))
        start = datetime.combine(s_local, time(0), zone).astimezone(ZoneInfo("UTC"))
        end = datetime.combine(e_local, time(0), zone).astimezone(ZoneInfo("UTC"))
    if end <= start:
        raise HTTPException(422, detail="end_before_start")
    return start, end, all_day


def create_event_row(
    db: Session,
    principal: Principal,
    cal: Calendar,
    *,
    title: str,
    start: datetime,
    end: datetime,
    all_day: bool = False,
    location: str | None = None,
    description: str | None = None,
    project: Project | None = None,
    task: Task | None = None,
    time_entry: TimeEntry | None = None,
    origin: str = "manual",
) -> CalendarEvent:
    href, uid = calsync.new_href(cal)
    zone = ZoneInfo(cal.workspace.timezone)
    ev = CalendarEvent(
        calendar=cal,
        calendar_id=cal.id,
        href=href,
        uid=uid,
        etag=None,
        ics=calsync.build_ics(
            None,
            uid=uid,
            title=title,
            start=start,
            end=end,
            all_day=all_day,
            zone=zone,
            description=description,
            location=location,
        ),
        title=title,
        description=description,
        location=location,
        start_at=start,
        end_at=end,
        all_day=all_day,
        recurring=False,
        modified_at=utcnow(),
        dirty=True,
        project_id=project.id if project else None,
        task_id=task.id if task else None,
        time_entry_id=time_entry.id if time_entry else None,
        origin=origin,
    )
    db.add(ev)
    db.flush()
    audit.record(
        db,
        principal,
        "create",
        "calendar_event",
        ev.id,
        {"title": title, "calendar": cal.name, "start": start.isoformat()},
    )
    return ev


def rewrite_event(ev: CalendarEvent, **fields) -> None:
    for k, v in fields.items():
        setattr(ev, k, v)
    zone = ZoneInfo(ev.calendar.workspace.timezone)
    ev.ics = calsync.build_ics(
        ev.ics,
        uid=ev.uid,
        title=ev.title,
        start=ev.start_at,
        end=ev.end_at,
        all_day=ev.all_day,
        zone=zone,
        description=ev.description,
        location=ev.location,
    )
    ev.modified_at = utcnow()
    ev.dirty = True


@router.post("/events", response_model=EventOut, status_code=201)
def create_event(
    body: EventIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Create an event; it's pushed to the calendar server straight away."""
    project = get_live(db, Project, body.project_id, "project") if body.project_id else None
    task = get_live(db, Task, body.task_id, "task") if body.task_id else None
    if task and project is None:
        project = task.project
    ws_id = project.workspace_id if project else None
    if body.calendar_id:
        cal = get_live(db, Calendar, body.calendar_id, "calendar")
    else:
        cal = default_calendar(db, ws_id) or default_calendar(db)
        if cal is None:
            raise HTTPException(409, detail="no_writable_calendar")
    _writable(cal)
    if project is None and body.project:
        project = lookup.find_project(db, cal.workspace_id, body.project)
    zone = ZoneInfo(cal.workspace.timezone)
    start, end, all_day = _times(body, zone)
    ev = create_event_row(
        db,
        principal,
        cal,
        title=body.title,
        start=start,
        end=end,
        all_day=all_day,
        location=body.location,
        description=body.description,
        project=project,
        task=task,
    )
    db.commit()
    calsync.push_now(db, ev)
    db.commit()
    return _event_out(_load_event(db, ev.id))


@router.get("/events/pending-log", response_model=list[EventOut])
def pending_log(
    workspace_id: uuid.UUID | None = None,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Ended events linked to a project with no time logged yet: "log this as time?"."""
    ws = resolve_workspace(db, workspace_id)
    now = utcnow()
    rows = db.scalars(
        select(CalendarEvent)
        .options(selectinload(CalendarEvent.calendar), selectinload(CalendarEvent.project))
        .join(CalendarEvent.calendar)
        .where(
            Calendar.workspace_id == ws.id,
            CalendarEvent.deleted_at.is_(None),
            CalendarEvent.project_id.is_not(None),
            CalendarEvent.time_entry_id.is_(None),
            CalendarEvent.log_dismissed.is_(False),
            CalendarEvent.all_day.is_(False),
            CalendarEvent.recurring.is_(False),
            CalendarEvent.origin.is_distinct_from("time_mirror"),
            CalendarEvent.end_at <= now,
            CalendarEvent.end_at >= now - timedelta(days=PENDING_LOG_DAYS),
        )
        .order_by(CalendarEvent.end_at.desc())
    ).all()
    return [_event_out(e) for e in rows]


@router.get("/events/{event_id}", response_model=EventOut)
def get_event(
    event_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return _event_out(_load_event(db, event_id))


@router.patch("/events/{event_id}", response_model=EventOut)
def update_event(
    event_id: uuid.UUID,
    body: EventPatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Move, rename or link an event. Linking a project works on read-only calendars too."""
    ev = _load_event(db, event_id)
    data = patch_data(body, {"location", "description", "project_id"})
    link = {}
    if "project_id" in data or data.get("project"):
        pid = data.pop("project_id", None)
        project = None
        if data.get("project"):
            project = lookup.find_project(db, ev.calendar.workspace_id, data["project"])
        elif pid:
            project = get_live(db, Project, pid, "project")
        link = {
            "project": [ev.project.name if ev.project else None, project.name if project else None]
        }
        ev.project = project
    data.pop("project", None)
    content = {k: v for k, v in data.items()}
    if content:
        _writable(ev.calendar)
        if ev.recurring:
            raise HTTPException(409, detail="recurring_event_edit_on_phone")
        zone = ZoneInfo(ev.calendar.workspace.timezone)
        start, end, all_day = _times(body, zone, ev)
        before = calsync._snapshot(ev)
        rewrite_event(
            ev,
            title=content.get("title", ev.title),
            location=content.get("location", ev.location),
            description=content.get("description", ev.description),
            start_at=start,
            end_at=end,
            all_day=all_day,
        )
        audit.record(
            db,
            principal,
            "update",
            "calendar_event",
            ev.id,
            {"before": before, "after": calsync._snapshot(ev)},
        )
    if link:
        audit.record(db, principal, "link", "calendar_event", ev.id, link)
    db.commit()
    if content:
        calsync.push_now(db, ev)
        db.commit()
    return _event_out(_load_event(db, event_id))


@router.delete("/events/{event_id}", status_code=204)
def delete_event(
    event_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    require_admin(principal, "deleting")
    ev = _load_event(db, event_id)
    _writable(ev.calendar)
    if ev.recurring:
        raise HTTPException(409, detail="recurring_event_edit_on_phone")
    ev.pending_delete = True
    ev.dirty = True
    ev.modified_at = utcnow()
    audit.record(db, principal, "delete", "calendar_event", ev.id, calsync._snapshot(ev))
    db.commit()
    calsync.push_now(db, ev)
    db.commit()


@router.post("/events/{event_id}/log-time", tags=["time"])
def log_event_as_time(
    event_id: uuid.UUID,
    body: LogTimeIn | None = None,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """ "I was in that meeting, bill it": turn an event into a time entry in one call."""
    from tahgul.routes import time as time_routes

    ev = _load_event(db, event_id)
    if ev.time_entry_id:
        raise HTTPException(409, detail="already_logged")
    if ev.all_day:
        raise HTTPException(422, detail="all_day_event")
    body = body or LogTimeIn()
    entry = time_routes.create_entry(
        time_routes.TimeEntryIn(
            workspace_id=ev.calendar.workspace_id,
            project_id=ev.project_id,
            task_id=ev.task_id,
            activity_id=body.activity_id,
            start_at=as_utc(ev.start_at),
            end_at=as_utc(ev.end_at),
            notes=body.notes or ev.title,
            billable=body.billable,
        ),
        principal,
        db,
    )
    row = db.get(TimeEntry, entry.id)
    row.source = "calendar"
    ev = _load_event(db, event_id)
    ev.time_entry_id = entry.id
    audit.record(db, principal, "link", "calendar_event", ev.id, {"time_entry": str(entry.id)})
    db.commit()
    return {"event": _event_out(ev), "entry": entry.model_copy(update={"source": "calendar"})}


@router.post("/events/{event_id}/dismiss-log", response_model=EventOut)
def dismiss_log(
    event_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Answer "no" to "log this as time?"."""
    ev = _load_event(db, event_id)
    ev.log_dismissed = True
    db.commit()
    return _event_out(ev)


# --- Tasks as time blocks ------------------------------------------------------------------


class BlockIn(BaseModel):
    calendar_id: uuid.UUID | None = None
    minutes: int | None = Field(
        None, gt=0, le=24 * 60, description="Default: the task's estimate, else an hour"
    )


@router.post("/tasks/{task_id}/block", response_model=EventOut, tags=["tasks"])
def block_task(
    task_id: uuid.UUID,
    body: BlockIn | None = None,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Put a task with a due date and time on the calendar as a time block."""
    from tahgul import mirror

    body = body or BlockIn()
    task = get_live(db, Task, task_id, "task")
    if task.due_date is None or task.due_time is None:
        raise HTTPException(422, detail="task_needs_due_time")
    existing = db.scalar(
        select(CalendarEvent).where(
            CalendarEvent.task_id == task.id,
            CalendarEvent.origin == "task_block",
            CalendarEvent.deleted_at.is_(None),
            CalendarEvent.pending_delete.is_(False),
        )
    )
    if existing:
        raise HTTPException(409, detail="already_blocked")
    cal = (
        get_live(db, Calendar, body.calendar_id, "calendar")
        if body.calendar_id
        else default_calendar(db, task.workspace_id)
    )
    if cal is None:
        raise HTTPException(409, detail="no_writable_calendar")
    _writable(cal)
    start, end = mirror.block_times(task, cal, body.minutes)
    ev = create_event_row(
        db,
        principal,
        cal,
        title=mirror.block_title(task),
        start=start,
        end=end,
        project=task.project,
        task=task,
        origin="task_block",
    )
    db.commit()
    calsync.push_now(db, ev)
    db.commit()
    return _event_out(_load_event(db, ev.id))


@router.delete("/tasks/{task_id}/block", status_code=204, tags=["tasks"])
def unblock_task(
    task_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Take the task's time block off the calendar (the task stays)."""
    ev = db.scalar(
        select(CalendarEvent).where(
            CalendarEvent.task_id == task_id,
            CalendarEvent.origin == "task_block",
            CalendarEvent.deleted_at.is_(None),
            CalendarEvent.pending_delete.is_(False),
        )
    )
    if ev is None:
        raise HTTPException(404, detail="no_block")
    ev.pending_delete, ev.dirty, ev.modified_at = True, True, utcnow()
    audit.record(db, principal, "unblock", "task", task_id, {"event": str(ev.id)})
    db.commit()
    calsync.push_now(db, ev)
    db.commit()
