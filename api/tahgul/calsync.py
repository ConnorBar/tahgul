"""Two-way calendar sync (SPEC: "Sync rules").

* Pull: sync tokens (only changes since last sync), every few minutes and on demand.
* Push: right away when an event is created, moved or deleted in Tahgul or by the agent.
* Each mirrored event keeps the server's etag; writes send If-Match, so a change made on
  the phone is never silently overwritten.
* Conflict (both sides changed): the newest change wins and the loser is kept in the
  audit log.
* Tahgul only ever writes to calendars marked writable.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events
from sqlalchemy import select
from sqlalchemy.orm import Session

from tahgul import audit, crypto
from tahgul.caldav import CalDAVClient, CalDAVError, PreconditionFailed
from tahgul.models import Calendar, CalendarAccount, CalendarEvent, Workspace
from tahgul.principal import Principal
from tahgul.timeutil import as_utc, utcnow

log = logging.getLogger("tahgul.sync")
_lock = threading.Lock()


def _sync_actor() -> Principal:
    """Audit rows written by sync show actor type and name "sync"."""
    return Principal(kind="sync", name="sync")


# --- iCalendar <-> fields --------------------------------------------------------------


def _to_utc(value, zone: ZoneInfo) -> tuple[datetime, bool]:
    """(UTC instant, all_day) for a DTSTART/DTEND value; floating times use ``zone``."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=zone)
        return value.astimezone(UTC), False
    if isinstance(value, date):
        return datetime.combine(value, time(0), zone).astimezone(UTC), True
    raise ValueError(f"unsupported date value {value!r}")


def _master(cal: icalendar.Calendar) -> icalendar.Event | None:
    events = [c for c in cal.walk("VEVENT")]
    for e in events:
        if e.get("RECURRENCE-ID") is None:
            return e
    return events[0] if events else None


def parse(ics: str, zone: ZoneInfo) -> dict | None:
    """Fields Tahgul shows, from an iCalendar object. None if it has no VEVENT."""
    try:
        cal = icalendar.Calendar.from_ical(ics)
    except ValueError:
        return None
    ev = _master(cal)
    if ev is None or ev.get("DTSTART") is None:
        return None
    start, all_day = _to_utc(ev.decoded("DTSTART"), zone)
    if ev.get("DTEND") is not None:
        end, _ = _to_utc(ev.decoded("DTEND"), zone)
    elif ev.get("DURATION") is not None:
        end = start + ev.decoded("DURATION")
    else:
        end = start + (timedelta(days=1) if all_day else timedelta(0))
    modified = ev.get("LAST-MODIFIED") or ev.get("DTSTAMP")
    mod_dt = modified.dt if modified is not None else None
    if isinstance(mod_dt, datetime):
        mod_dt = (mod_dt if mod_dt.tzinfo else mod_dt.replace(tzinfo=UTC)).astimezone(UTC)
    else:
        mod_dt = None
    return {
        "uid": str(ev.get("UID", "")) or str(uuid.uuid4()),
        "title": str(ev.get("SUMMARY", "")),
        "description": str(ev.get("DESCRIPTION")) if ev.get("DESCRIPTION") else None,
        "location": str(ev.get("LOCATION")) if ev.get("LOCATION") else None,
        "start_at": start,
        "end_at": max(end, start),
        "all_day": all_day,
        "recurring": ev.get("RRULE") is not None or len(list(cal.walk("VEVENT"))) > 1,
        "remote_modified": mod_dt,
    }


def build_ics(
    existing: str | None,
    *,
    uid: str,
    title: str,
    start: datetime,
    end: datetime,
    all_day: bool,
    zone: ZoneInfo,
    description: str | None = None,
    location: str | None = None,
) -> str:
    """Write our fields into the event, keeping every other property as it was."""
    if existing:
        cal = icalendar.Calendar.from_ical(existing)
        ev = _master(cal)
    else:
        cal = icalendar.Calendar()
        cal.add("prodid", "-//Tahgul//Tahgul//EN")
        cal.add("version", "2.0")
        ev = icalendar.Event()
        ev.add("uid", uid)
        cal.add_component(ev)
    now = datetime.now(UTC).replace(microsecond=0)
    for key in (
        "SUMMARY",
        "DTSTART",
        "DTEND",
        "DURATION",
        "DESCRIPTION",
        "LOCATION",
        "DTSTAMP",
        "LAST-MODIFIED",
    ):
        if key in ev:
            del ev[key]
    ev.add("summary", title)
    if all_day:
        s_local = as_utc(start).astimezone(zone).date()
        e_local = as_utc(end).astimezone(zone).date()
        ev.add("dtstart", s_local)
        ev.add("dtend", max(e_local, s_local + timedelta(days=1)))
    else:
        # Always UTC: unambiguous everywhere and no VTIMEZONE needed.
        ev.add("dtstart", as_utc(start).astimezone(UTC).replace(microsecond=0))
        ev.add("dtend", as_utc(end).astimezone(UTC).replace(microsecond=0))
    if description:
        ev.add("description", description)
    if location:
        ev.add("location", location)
    ev.add("dtstamp", now)
    ev.add("last-modified", now)
    seq = int(ev.get("SEQUENCE", 0))
    if "SEQUENCE" in ev:
        del ev["SEQUENCE"]
    ev.add("sequence", seq + 1)
    return cal.to_ical().decode()


def occurrences(
    ev: CalendarEvent, start: datetime, end: datetime, zone: ZoneInfo
) -> Iterator[tuple[datetime, datetime]]:
    """(start, end) of each instance overlapping the range, recurrences expanded."""
    if not ev.recurring:
        if as_utc(ev.start_at) < end and as_utc(ev.end_at) > start:
            yield as_utc(ev.start_at), as_utc(ev.end_at)
        return
    try:
        cal = icalendar.Calendar.from_ical(ev.ics)
        for inst in recurring_ical_events.of(cal).between(start, end):
            s, _ = _to_utc(inst.decoded("DTSTART"), zone)
            if inst.get("DTEND") is not None:
                e, _ = _to_utc(inst.decoded("DTEND"), zone)
            else:
                e = s + (
                    timedelta(days=1)
                    if not isinstance(inst.decoded("DTSTART"), datetime)
                    else timedelta(0)
                )
            yield s, max(e, s)
    except Exception:  # a malformed rule shouldn't break the whole calendar view
        log.exception("Couldn't expand recurring event %s", ev.uid)


# --- Accounts and discovery ------------------------------------------------------------


def client_for(account: CalendarAccount) -> CalDAVClient:
    return CalDAVClient(account.url, account.username, crypto.decrypt(account.password_enc))


def _default_workspace(db: Session) -> Workspace:
    return db.scalars(
        select(Workspace).where(Workspace.deleted_at.is_(None)).order_by(Workspace.created_at)
    ).first()


def discover(db: Session, account: CalendarAccount) -> list[Calendar]:
    """Find the account's calendars and add new ones (read-only until you say otherwise)."""
    client = client_for(account)
    try:
        remote = [c for c in client.calendars() if c.supports_events]
    finally:
        client.close()
    known = {
        c.url: c
        for c in db.scalars(select(Calendar).where(Calendar.account_id == account.id)).all()
    }
    ws = _default_workspace(db)
    for rc in remote:
        cal = known.get(rc.url)
        if cal is None:
            cal = Calendar(
                account_id=account.id,
                workspace_id=ws.id,
                url=rc.url,
                name=rc.name,
                colour=rc.colour,
                server_writable=rc.writable,
                writable=False,
            )
            db.add(cal)
        else:
            cal.name, cal.server_writable = rc.name, rc.writable
            cal.colour = cal.colour or rc.colour
            if not rc.writable:
                cal.writable = False
            cal.deleted_at = None
    gone = {u for u in known if u not in {rc.url for rc in remote}}
    for url in gone:
        known[url].deleted_at = known[url].deleted_at or utcnow()
    db.flush()
    return [
        c
        for c in db.scalars(
            select(Calendar).where(Calendar.account_id == account.id, Calendar.deleted_at.is_(None))
        ).all()
    ]


# --- Pull ------------------------------------------------------------------------------


def _apply_remote(ev: CalendarEvent, fields: dict, ics: str, etag: str) -> None:
    ev.ics, ev.etag = ics, etag
    for k in (
        "uid",
        "title",
        "description",
        "location",
        "start_at",
        "end_at",
        "all_day",
        "recurring",
    ):
        setattr(ev, k, fields[k])
    ev.modified_at = fields["remote_modified"] or utcnow()
    ev.dirty = False
    ev.pending_delete = False


def _snapshot(ev: CalendarEvent) -> dict:
    return {
        "title": ev.title,
        "start": as_utc(ev.start_at).isoformat(),
        "end": as_utc(ev.end_at).isoformat(),
        "location": ev.location,
    }


def pull(db: Session, client: CalDAVClient, cal: Calendar) -> dict:
    zone = ZoneInfo(cal.workspace.timezone)
    ch = client.changes(cal.url, cal.sync_token)
    rows = {
        e.href: e
        for e in db.scalars(
            select(CalendarEvent).where(
                CalendarEvent.calendar_id == cal.id, CalendarEvent.deleted_at.is_(None)
            )
        ).all()
    }
    stats = {"added": 0, "updated": 0, "deleted": 0, "conflicts": 0}

    deleted = set(ch.deleted)
    if ch.full:  # first sync or expired token: anything we know that isn't listed is gone
        deleted |= {h for h, e in rows.items() if h not in ch.changed and e.etag}
    for href in deleted:
        ev = rows.get(href)
        if ev is None:
            continue
        if ev.dirty and not ev.pending_delete:
            stats["conflicts"] += 1
            audit.record(
                db,
                _sync_actor(),
                "conflict",
                "calendar_event",
                ev.id,
                {"winner": "remote delete", "lost_local": _snapshot(ev)},
            )
        ev.deleted_at = utcnow()
        stats["deleted"] += 1

    need = [h for h, etag in ch.changed.items() if h not in rows or rows[h].etag != etag]
    for href, (etag, ics) in client.fetch(cal.url, need).items() if need else []:
        fields = parse(ics, zone)
        if fields is None:
            continue
        ev = rows.get(href)
        if ev is None:
            ev = CalendarEvent(calendar_id=cal.id, href=href, modified_at=utcnow())
            _apply_remote(ev, fields, ics, etag)
            db.add(ev)
            stats["added"] += 1
            continue
        if ev.dirty:
            remote_time = fields["remote_modified"] or utcnow()
            if as_utc(ev.modified_at) > remote_time:
                # Ours is newer: keep it, but adopt the server's etag so our push overwrites.
                ev.etag = etag
                stats["conflicts"] += 1
                audit.record(
                    db,
                    _sync_actor(),
                    "conflict",
                    "calendar_event",
                    ev.id,
                    {
                        "winner": "tahgul",
                        "lost_remote": {
                            "title": fields["title"],
                            "start": fields["start_at"].isoformat(),
                        },
                    },
                )
                continue
            stats["conflicts"] += 1
            audit.record(
                db,
                _sync_actor(),
                "conflict",
                "calendar_event",
                ev.id,
                {"winner": "remote", "lost_local": _snapshot(ev)},
            )
        _apply_remote(ev, fields, ics, etag)
        stats["updated"] += 1

    cal.sync_token = ch.sync_token
    cal.last_synced_at = utcnow()
    return stats


# --- Push ------------------------------------------------------------------------------


def push(db: Session, client: CalDAVClient, ev: CalendarEvent) -> None:
    """Send one local change to the server. Raises CalDAVError on network trouble."""
    cal = ev.calendar
    if not cal.writable:
        ev.dirty = False  # never write to a read-only calendar
        return
    if ev.pending_delete:
        try:
            client.delete(ev.href, ev.etag)
        except PreconditionFailed:
            # It changed on the phone after we decided to delete: newest wins.
            fetched = client.fetch(cal.url, [ev.href]).get(ev.href)
            fields = parse(fetched[1], ZoneInfo(cal.workspace.timezone)) if fetched else None
            if fields and (fields["remote_modified"] or utcnow()) > as_utc(ev.modified_at):
                audit.record(
                    db,
                    _sync_actor(),
                    "conflict",
                    "calendar_event",
                    ev.id,
                    {"winner": "remote", "lost_local": "delete"},
                )
                _apply_remote(ev, fields, fetched[1], fetched[0])
                ev.deleted_at = None
                return
            client.delete(ev.href, None)
        ev.deleted_at = ev.deleted_at or utcnow()
        ev.dirty = ev.pending_delete = False
        return
    try:
        new_etag = client.put(ev.href, ev.ics, ev.etag)
    except PreconditionFailed:
        fetched = client.fetch(cal.url, [ev.href]).get(ev.href)
        if fetched is None:  # deleted remotely meanwhile; recreate ours
            new_etag = client.put(ev.href, ev.ics, None)
        else:
            fields = parse(fetched[1], ZoneInfo(cal.workspace.timezone))
            remote_time = (fields or {}).get("remote_modified") or utcnow()
            if fields and remote_time >= as_utc(ev.modified_at):
                audit.record(
                    db,
                    _sync_actor(),
                    "conflict",
                    "calendar_event",
                    ev.id,
                    {"winner": "remote", "lost_local": _snapshot(ev)},
                )
                _apply_remote(ev, fields, fetched[1], fetched[0])
                return
            audit.record(
                db,
                _sync_actor(),
                "conflict",
                "calendar_event",
                ev.id,
                {"winner": "tahgul", "lost_remote": {"title": (fields or {}).get("title")}},
            )
            new_etag = client.put(ev.href, ev.ics, fetched[0])
    ev.etag = new_etag or client.etag(ev.href)
    ev.dirty = False


def push_now(db: Session, ev: CalendarEvent) -> bool:
    """Push right away; on failure leave it dirty for the next scheduled sync."""
    try:
        client = client_for(ev.calendar.account)
    except Exception:
        return False
    try:
        push(db, client, ev)
        return True
    except CalDAVError as exc:
        log.warning("Push failed, will retry: %s", exc)
        ev.calendar.account.last_error = str(exc)
        return False
    finally:
        client.close()


# --- Whole-account sync ----------------------------------------------------------------


def sync_account(db: Session, account: CalendarAccount) -> dict:
    totals = {"added": 0, "updated": 0, "deleted": 0, "conflicts": 0, "pushed": 0}
    client = client_for(account)
    try:
        cals = [c for c in account.calendars if c.enabled and c.deleted_at is None]
        for cal in cals:
            # Push first, so a pull doesn't mistake our pending edits for conflicts.
            for ev in db.scalars(
                select(CalendarEvent).where(
                    CalendarEvent.calendar_id == cal.id, CalendarEvent.dirty.is_(True)
                )
            ).all():
                push(db, client, ev)
                totals["pushed"] += 1
            for k, v in pull(db, client, cal).items():
                totals[k] += v
        account.last_synced_at = utcnow()
        account.last_error = None
    except CalDAVError as exc:
        account.last_error = str(exc)
        raise
    finally:
        client.close()
    return totals


def sync_all(db: Session) -> dict:
    """Used by the scheduler and "sync now". Never runs twice at once."""
    if not _lock.acquire(blocking=False):
        return {"skipped": "a sync is already running"}
    try:
        out = {}
        for account in db.scalars(
            select(CalendarAccount).where(CalendarAccount.deleted_at.is_(None))
        ).all():
            try:
                out[account.name] = sync_account(db, account)
            except CalDAVError as exc:
                out[account.name] = {"error": str(exc)}
            db.commit()
        return out
    finally:
        _lock.release()


def new_href(cal: Calendar) -> tuple[str, str]:
    uid = f"{uuid.uuid4()}@tahgul"
    return cal.url.rstrip("/") + "/" + uid.replace("@", "-") + ".ics", uid


def ensure_stack_account(db: Session) -> CalendarAccount | None:
    """Connect the Radicale in the Docker stack on first start, with a "Work" calendar.

    Does nothing once any calendar account exists, or when Radicale isn't configured.
    """
    from tahgul.config import get_settings

    s = get_settings()
    if not (s.radicale_url and s.radicale_user and s.radicale_password and s.secret_key):
        return None
    if db.scalar(select(CalendarAccount.id).limit(1)) is not None:
        return None
    if _default_workspace(db) is None:
        return None  # wait until the owner (and their workspace) exists
    account = CalendarAccount(
        name="Homelab",
        kind="radicale",
        url=s.radicale_url,
        username=s.radicale_user,
        password_enc=crypto.encrypt(s.radicale_password),
    )
    db.add(account)
    db.flush()
    cals = discover(db, account)
    if not cals:
        client = client_for(account)
        try:
            client.make_calendar(client.home(), "work", "Work", "#3b6ea5")
        finally:
            client.close()
        cals = discover(db, account)
    first = cals[0]
    first.writable = first.server_writable
    first.is_default = first.writable
    audit.record(
        db,
        _sync_actor(),
        "create",
        "calendar_account",
        account.id,
        {"name": account.name, "url": account.url, "auto": True},
    )
    db.commit()
    log.info("Connected the stack's Radicale as %s", account.name)
    return account
