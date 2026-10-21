"""Calendar side effects of time and tasks (SPEC: "What shows up where").

* Time entries can mirror to a "Billable" calendar, so the phone shows what was billed.
* Tasks with a due time can be placed on the calendar as a time block.

Both are best effort: if the calendar server is down the change is kept and pushed on
the next sync.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from tahgul import calsync
from tahgul.models import Calendar, CalendarEvent, Task, TimeEntry
from tahgul.principal import Principal
from tahgul.timeutil import as_utc, utcnow


def _mirror_calendars(db: Session, workspace_id) -> list[Calendar]:
    return list(
        db.scalars(
            select(Calendar).where(
                Calendar.workspace_id == workspace_id,
                Calendar.deleted_at.is_(None),
                Calendar.enabled.is_(True),
                Calendar.writable.is_(True),
                Calendar.mirror_time_entries.is_(True),
            )
        ).all()
    )


def _entry_title(e: TimeEntry) -> str:
    who = e.project.name if e.project else "No project"
    return f"{who} – {e.notes}" if e.notes else who


def _entry_description(e: TimeEntry) -> str:
    parts = [f"{(e.duration_seconds or 0) / 3600:.2f} h"]
    if e.billable and e.amount is not None:
        parts.append(f"{e.amount} {e.workspace.currency}")
    elif not e.billable:
        parts.append("not billable")
    if e.project and e.project.client:
        parts.append(e.project.client.name)
    return " · ".join(parts) + "\n\nLogged in Tahgul."


def mirror_entries(db: Session, principal: Principal, entries: list[TimeEntry]) -> list:
    """Create, update or remove each entry's event on the workspace's Billable calendar."""
    from tahgul.routes.calendar import create_event_row, rewrite_event

    touched: list[CalendarEvent] = []
    for e in entries:
        cals = _mirror_calendars(db, e.workspace_id)
        existing = {
            ev.calendar_id: ev
            for ev in db.scalars(
                select(CalendarEvent).where(
                    CalendarEvent.time_entry_id == e.id,
                    CalendarEvent.origin == "time_mirror",
                    CalendarEvent.deleted_at.is_(None),
                )
            ).all()
        }
        gone = e.deleted_at is not None or e.end_at is None
        for cal in cals:
            ev = existing.pop(cal.id, None)
            if gone:
                if ev is not None and not ev.pending_delete:
                    ev.pending_delete, ev.dirty, ev.modified_at = True, True, utcnow()
                    touched.append(ev)
                continue
            fields = {
                "title": _entry_title(e),
                "start_at": as_utc(e.start_at),
                "end_at": as_utc(e.end_at),
                "all_day": False,
                "description": _entry_description(e),
            }
            if ev is None:
                ev = create_event_row(
                    db,
                    principal,
                    cal,
                    title=fields["title"],
                    start=fields["start_at"],
                    end=fields["end_at"],
                    description=fields["description"],
                    project=e.project,
                    time_entry=e,
                    origin="time_mirror",
                )
                touched.append(ev)
            elif any(
                getattr(ev, k) != v
                if k not in ("start_at", "end_at")
                else as_utc(getattr(ev, k)) != v
                for k, v in fields.items()
            ):
                rewrite_event(ev, **fields)
                touched.append(ev)
        # Calendars no longer mirroring keep what's there; nothing to do for leftovers.
    db.commit()
    for ev in touched:
        calsync.push_now(db, ev)
    if touched:
        db.commit()
    return touched


def refresh_task_block(db: Session, principal: Principal, task: Task) -> CalendarEvent | None:
    """Keep a task's time block in step with its title and due date/time."""
    from tahgul.routes.calendar import rewrite_event

    ev = db.scalar(
        select(CalendarEvent).where(
            CalendarEvent.task_id == task.id,
            CalendarEvent.origin == "task_block",
            CalendarEvent.deleted_at.is_(None),
            CalendarEvent.pending_delete.is_(False),
        )
    )
    if ev is None:
        return None
    if task.deleted_at is not None or task.due_date is None or task.due_time is None:
        ev.pending_delete, ev.dirty, ev.modified_at = True, True, utcnow()
    else:
        keep = int((as_utc(ev.end_at) - as_utc(ev.start_at)).total_seconds() // 60)
        start, end = block_times(task, ev.calendar, keep)
        title = block_title(task)
        if (title, start, end) != (ev.title, as_utc(ev.start_at), as_utc(ev.end_at)):
            rewrite_event(ev, title=title, start_at=start, end_at=end)
        else:
            return ev
    db.commit()
    calsync.push_now(db, ev)
    db.commit()
    return ev


def block_title(task: Task) -> str:
    return f"{task.title} (#{task.number})"


def block_times(task: Task, cal: Calendar, minutes: int | None = None):
    zone = ZoneInfo(cal.workspace.timezone)
    start = datetime.combine(task.due_date, task.due_time or time(9), zone)
    length = minutes or task.estimate_minutes or 60
    return as_utc(start), as_utc(start + timedelta(minutes=length))
