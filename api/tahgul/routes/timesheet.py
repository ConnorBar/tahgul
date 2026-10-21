"""Week timesheet: a grid of hours per project and activity per day (SPEC: time tracking).

Each cell is the sum of that day's entries (by local start date). Setting a cell adds
a "timesheet" entry for the difference, or shrinks manual/timesheet entries, newest
first. Timer and calendar entries are never shortened from the grid.
"""

import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from tahgul import audit, mirror
from tahgul import timekeeping as tk
from tahgul.crud import TwoPlaces, resolve_workspace
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import TimeEntry
from tahgul.principal import Principal
from tahgul.routes.time import _billable, _q, _refs, _source
from tahgul.timeutil import as_utc, utcnow

router = APIRouter(prefix="/api/v1/timesheet", tags=["timesheet"])

SHRINKABLE = {"manual", "timesheet", "agent"}
DAY_START = time(9, 0)


class Row(BaseModel):
    project_id: uuid.UUID | None
    project_name: str | None
    client_name: str | None
    colour: str | None
    activity_id: uuid.UUID | None
    activity_name: str | None
    hours: list[TwoPlaces] = Field(description="Monday to Sunday")
    total: TwoPlaces


class Week(BaseModel):
    workspace_id: uuid.UUID
    week_start: date
    days: list[date]
    rows: list[Row]
    day_totals: list[TwoPlaces]
    total: TwoPlaces
    running_entry_id: uuid.UUID | None = Field(
        None, description="A running timer in this week isn't counted until it stops."
    )


class CellIn(BaseModel):
    workspace_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    activity_id: uuid.UUID | None = None
    date: date
    hours: Decimal = Field(ge=0, le=24, decimal_places=2)


def _week_entries(db: Session, ws_id: uuid.UUID, first: date, last: date, zone):
    start, end = tk.day_range(first, last, zone)
    return db.scalars(
        _q()
        .where(
            TimeEntry.workspace_id == ws_id,
            TimeEntry.deleted_at.is_(None),
            TimeEntry.start_at >= start,
            TimeEntry.start_at < end,
        )
        .order_by(TimeEntry.start_at)
    ).all()


@router.get("", response_model=Week)
def get_week(
    workspace_id: uuid.UUID | None = None,
    week: date | None = Query(None, description="Any day in the week; defaults to this week"),
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    ws = resolve_workspace(db, workspace_id)
    zone = tk.tz(ws)
    today = datetime.now(zone).date()
    first = tk.week_start(week or today)
    days = [first + timedelta(days=i) for i in range(7)]

    seconds: dict[tuple, list[int]] = defaultdict(lambda: [0] * 7)
    meta: dict[tuple, TimeEntry] = {}
    running_id = None
    for e in _week_entries(db, ws.id, first, days[-1], zone):
        if e.running:
            running_id = e.id
            continue
        key = (e.project_id, e.activity_id)
        seconds[key][(tk.local_date(e.start_at, zone) - first).days] += e.duration_seconds or 0
        meta.setdefault(key, e)

    rows = []
    for key, secs in seconds.items():
        e = meta[key]
        rows.append(
            Row(
                project_id=e.project_id,
                project_name=e.project.name if e.project else None,
                client_name=e.project.client.name if e.project and e.project.client else None,
                colour=e.project.colour if e.project else None,
                activity_id=e.activity_id,
                activity_name=e.activity.name if e.activity else None,
                hours=[tk.hours(s) for s in secs],
                total=tk.hours(sum(secs)),
            )
        )
    rows.sort(
        key=lambda r: (
            (r.client_name or "~").lower(),
            (r.project_name or "~").lower(),
            (r.activity_name or "").lower(),
        )
    )
    day_secs = [sum(v[i] for v in seconds.values()) for i in range(7)]
    return Week(
        workspace_id=ws.id,
        week_start=first,
        days=days,
        rows=rows,
        day_totals=[tk.hours(s) for s in day_secs],
        total=tk.hours(sum(day_secs)),
        running_entry_id=running_id,
    )


@router.put("/cell", response_model=Week)
def set_cell(
    body: CellIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Set the hours for one project/activity on one day."""
    ws, project, activity = _refs(db, body.workspace_id, body.project_id, body.activity_id)
    zone = tk.tz(ws)
    day_entries = _week_entries(db, ws.id, body.date, body.date, zone)
    cell = [
        e
        for e in day_entries
        if not e.running and e.project_id == body.project_id and e.activity_id == body.activity_id
    ]
    current = sum(e.duration_seconds or 0 for e in cell)
    created: TimeEntry | None = None
    target = int((body.hours * 3600).to_integral_value())
    delta = target - current

    if delta > 0:
        # Append after the day's last entry, or at 09:00.
        ends = [as_utc(e.end_at) for e in day_entries if e.end_at]
        start = max(ends) if ends else datetime.combine(body.date, DAY_START, zone)
        day_end = tk.local_day_start(body.date + timedelta(days=1), zone)
        if start + timedelta(seconds=delta) > day_end:
            start = max(day_end - timedelta(seconds=delta), tk.local_day_start(body.date, zone))
        e = TimeEntry(
            workspace=ws,
            project=project,
            activity=activity,
            start_at=start,
            billable=_billable(activity),
            source=_source(principal, "timesheet"),
        )
        tk.close(e, start + timedelta(seconds=delta))
        tk.price(e)
        db.add(e)
        created = e
        db.flush()
        audit.record(db, principal, "create", "time_entry", e.id, {"timesheet": str(body.hours)})
    elif delta < 0:
        remaining = -delta
        for e in sorted(cell, key=lambda x: as_utc(x.start_at), reverse=True):
            if remaining <= 0:
                break
            if e.source not in SHRINKABLE:
                continue
            if e.invoice_id is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, detail="entry_invoiced")
            cut = min(remaining, e.duration_seconds or 0)
            remaining -= cut
            if cut == (e.duration_seconds or 0):
                e.deleted_at = utcnow()
                audit.record(db, principal, "delete", "time_entry", e.id, {"timesheet": True})
            else:
                tk.close(e, as_utc(e.end_at) - timedelta(seconds=cut))
                tk.price(e)
                audit.record(
                    db,
                    principal,
                    "update",
                    "time_entry",
                    e.id,
                    {"seconds": e.duration_seconds, "timesheet": True},
                )
        if remaining > 0:
            db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="cannot_shrink_tracked_time",
            )
    db.commit()
    mirror.mirror_entries(db, principal, cell + ([created] if created else []))
    return get_week(workspace_id=ws.id, week=body.date, _=principal, db=db)
