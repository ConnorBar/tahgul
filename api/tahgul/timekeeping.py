"""Time-entry rules: pricing, overlaps, rounding and local days."""

import math
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from tahgul import rates
from tahgul.models import TimeEntry, Workspace
from tahgul.timeutil import as_utc, utcnow

CENT = Decimal("0.01")
ROUNDING_MINUTES = (0, 1, 5, 6, 10, 15, 30, 60)
ROUNDING_MODES = ("up", "nearest", "down")
MAX_ENTRY = timedelta(days=7)


def amount_for(rate: Decimal | None, seconds: int | None) -> Decimal | None:
    if rate is None or seconds is None:
        return None
    return (Decimal(rate) * Decimal(seconds) / Decimal(3600)).quantize(CENT, ROUND_HALF_UP)


def price(entry: TimeEntry) -> None:
    """Resolve and freeze the rate and amount onto the entry."""
    if entry.project is not None:
        resolved = rates.project_rate(entry.project, entry.activity, entry.rate_override)
    else:
        resolved = rates.resolve_rate(
            workspace=entry.workspace.default_rate,
            activity=entry.activity.rate if entry.activity else None,
            entry=entry.rate_override,
        )
    entry.rate, entry.rate_source = resolved.rate, resolved.source
    entry.amount = amount_for(entry.rate, entry.duration_seconds)


def close(entry: TimeEntry, end: datetime) -> None:
    entry.end_at = end
    entry.duration_seconds = max(0, int((as_utc(end) - as_utc(entry.start_at)).total_seconds()))


def elapsed_seconds(entry: TimeEntry, now: datetime | None = None) -> int:
    if entry.duration_seconds is not None:
        return entry.duration_seconds
    now = now or utcnow()
    return max(0, int((now - as_utc(entry.start_at)).total_seconds()))


def overlapping(db: Session, entry: TimeEntry) -> list[TimeEntry]:
    """Other entries whose time overlaps this one (a running entry ends now)."""
    start = as_utc(entry.start_at)
    end = as_utc(entry.end_at) if entry.end_at else utcnow()
    candidates = db.scalars(
        select(TimeEntry).where(
            TimeEntry.deleted_at.is_(None),
            TimeEntry.id != entry.id,
            TimeEntry.start_at < end,
            or_(TimeEntry.end_at.is_(None), TimeEntry.end_at > start),
        )
    ).all()
    # Re-check in Python so SQLite's naive datetimes compare correctly too.
    out = []
    for other in candidates:
        o_start = as_utc(other.start_at)
        o_end = as_utc(other.end_at) if other.end_at else utcnow()
        if o_start < end and o_end > start:
            out.append(other)
    return out


def round_seconds(seconds: int, minutes: int, mode: str) -> int:
    """Billing rounding, e.g. up to the next 6 minutes. Used at invoice time."""
    if minutes <= 0 or seconds <= 0:
        return max(seconds, 0)
    step = minutes * 60
    units = seconds / step
    if mode == "up":
        n = math.ceil(units)
    elif mode == "down":
        n = math.floor(units)
    else:
        n = math.floor(units + 0.5)
    return int(n * step)


# --- Local days ------------------------------------------------------------------


def tz(ws: Workspace) -> ZoneInfo:
    return ZoneInfo(ws.timezone)


def local_day_start(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), zone)


def day_range(first: date, last: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """[start of first day, start of the day after last) in UTC-aware datetimes."""
    return local_day_start(first, zone), local_day_start(last + timedelta(days=1), zone)


def local_date(dt: datetime, zone: ZoneInfo) -> date:
    return as_utc(dt).astimezone(zone).date()


def week_start(day: date) -> date:
    """Monday of the ISO week containing ``day``."""
    return day - timedelta(days=day.weekday())


def ensure_aware(dt: datetime, zone: ZoneInfo) -> datetime:
    """Treat a naive datetime from a client as the workspace's local time."""
    return dt if dt.tzinfo else dt.replace(tzinfo=zone)


def hours(seconds: int | float | Decimal) -> Decimal:
    return (Decimal(seconds) / Decimal(3600)).quantize(CENT, ROUND_HALF_UP)
