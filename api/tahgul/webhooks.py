"""Webhooks (SPEC: agent API, "POST to a URL on events like timer.stopped,
task.due_soon, calendar.event_ended so the agent can react").

How it fits together:

* A SQLAlchemy flush hook notices interesting changes wherever they happen (web UI,
  API, Siri, the agent, sync) and writes a ``WebhookDelivery`` row per subscribed
  webhook in the same transaction. Nothing is sent for work that's rolled back.
* Time-based events (a task coming due, a linked meeting ending) are found by a sweep
  every minute; each fires once per webhook.
* A dispatcher thread sends pending deliveries, signs them, and retries failures with
  backoff (1 min, 5 min, 30 min, 2 h, 6 h) before giving up.

Every request is a JSON POST signed with the webhook's secret:

    X-Tahgul-Event: timer.stopped
    X-Tahgul-Delivery: <delivery id>          (stable across retries)
    X-Tahgul-Timestamp: 1761030000
    X-Tahgul-Signature: sha256=<hex HMAC of "<timestamp>.<body>">
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time as _time
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, attributes

from tahgul import crypto
from tahgul.db import get_sessionmaker
from tahgul.models import (
    BoardColumn,
    Calendar,
    CalendarEvent,
    Invoice,
    Project,
    Task,
    TimeEntry,
    VoiceNote,
    Webhook,
    WebhookDelivery,
    Workspace,
)
from tahgul.timeutil import as_utc, utcnow

log = logging.getLogger("tahgul.webhooks")

EVENTS = {
    "timer.started": "The timer started",
    "timer.stopped": "The timer stopped (the finished time entry)",
    "time_entry.created": "Time logged by hand, from a meeting or by the agent",
    "task.created": "A task was added",
    "task.completed": "A task moved to a done column",
    "task.due_soon": "A task is due within the hour (date-only tasks: 9:00 that day)",
    "calendar.event_ended": "A meeting linked to a project ended and isn't logged yet",
    "invoice.issued": "An invoice got its number",
    "invoice.paid": "An invoice was marked paid",
    "invoice.voided": "An invoice was voided",
    "idea.created": "A new idea in the inbox",
    "idea.promoted": "An idea became a project",
    "voice_note.created": "A voice note was uploaded",
    "voice_note.transcribed": "Whisper finished a transcript",
}
BACKOFF = [timedelta(minutes=m) for m in (1, 5, 30, 120, 360)]
DUE_SOON = timedelta(minutes=60)
DATE_ONLY_DUE = time(9, 0)
ENDED_WINDOW = timedelta(minutes=30)
TIMEOUT = httpx.Timeout(10)
KEEP_DELIVERIES = timedelta(days=30)

# Tests swap in an httpx.MockTransport here.
_transport: httpx.BaseTransport | None = None
_wake = threading.Event()


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _iso(dt: datetime | None) -> str | None:
    return as_utc(dt).isoformat() if dt else None


def _num(x) -> str | None:
    return None if x is None else f"{x:.2f}"


# --- Payloads ----------------------------------------------------------------------------


def entry_data(e: TimeEntry) -> dict:
    p = e.project
    return {
        "id": str(e.id),
        "project_id": str(e.project_id) if e.project_id else None,
        "project_name": p.name if p else None,
        "client_name": p.client.name if p and p.client else None,
        "task_id": str(e.task_id) if e.task_id else None,
        "start_at": _iso(e.start_at),
        "end_at": _iso(e.end_at),
        "duration_seconds": e.duration_seconds,
        "hours": _num((e.duration_seconds or 0) / 3600) if e.end_at else None,
        "notes": e.notes,
        "billable": e.billable,
        "amount": _num(e.amount),
        "source": e.source,
    }


def task_data(t: Task, db: Session) -> dict:
    col = db.get(BoardColumn, t.column_id)
    return {
        "id": str(t.id),
        "ref": f"#{t.number}",
        "title": t.title,
        "project_id": str(t.project_id),
        "project_name": t.project.name if t.project else None,
        "status": col.name if col else None,
        "priority": t.priority,
        "labels": t.labels,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "due_time": t.due_time.isoformat(timespec="minutes") if t.due_time else None,
        "url": f"/projects/{t.project_id}?task={t.id}",
    }


def invoice_data(i: Invoice) -> dict:
    return {
        "id": str(i.id),
        "number": i.number,
        "client_id": str(i.client_id),
        "client_name": i.client.name if i.client else None,
        "status": i.status,
        "total": _num(i.total),
        "currency": i.currency,
        "issue_date": i.issue_date.isoformat(),
        "due_date": i.due_date.isoformat(),
        "paid_on": i.paid_on.isoformat() if i.paid_on else None,
        "url": f"/invoices/{i.id}",
    }


def project_data(p: Project) -> dict:
    return {"id": str(p.id), "name": p.name, "status": p.status, "url": f"/projects/{p.id}"}


def note_data(n: VoiceNote) -> dict:
    return {
        "id": str(n.id),
        "title": n.title,
        "project_id": str(n.project_id) if n.project_id else None,
        "task_id": str(n.task_id) if n.task_id else None,
        "recorded_at": _iso(n.recorded_at),
        "duration_seconds": n.duration_seconds,
        "transcript": n.transcript,
        "playback_url": f"/api/v1/voice-notes/{n.id}/audio",
    }


def event_data(e: CalendarEvent) -> dict:
    return {
        "id": str(e.id),
        "title": e.title,
        "start_at": _iso(e.start_at),
        "end_at": _iso(e.end_at),
        "project_id": str(e.project_id) if e.project_id else None,
        "project_name": e.project.name if e.project else None,
        "log_url": f"/api/v1/events/{e.id}/log-time",
    }


# --- Noticing changes --------------------------------------------------------------------


def _changed(obj, attr: str) -> tuple[Any, Any] | None:
    """(old, new) if the attribute changed in this flush."""
    h = attributes.get_history(obj, attr)
    if not h.has_changes():
        return None
    old = h.deleted[0] if h.deleted else None
    new = h.added[0] if h.added else None
    return old, new


def _collect(session: Session) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for obj in session.new:
        if isinstance(obj, TimeEntry) and obj.deleted_at is None:
            out.append(
                ("timer.started" if obj.end_at is None else "time_entry.created", entry_data(obj))
            )
        elif isinstance(obj, Task):
            out.append(("task.created", task_data(obj, session)))
        elif isinstance(obj, Project) and obj.status == "idea":
            out.append(("idea.created", project_data(obj)))
        elif isinstance(obj, VoiceNote):
            out.append(("voice_note.created", note_data(obj)))
    for obj in session.dirty:
        if isinstance(obj, TimeEntry):
            c = _changed(obj, "end_at")
            if c and c[0] is None and c[1] is not None:
                out.append(("timer.stopped", entry_data(obj)))
        elif isinstance(obj, Task):
            c = _changed(obj, "completed_at")
            if c and c[0] is None and c[1] is not None and obj.deleted_at is None:
                out.append(("task.completed", task_data(obj, session)))
        elif isinstance(obj, Invoice):
            c = _changed(obj, "status")
            if c:
                name = {"sent": "invoice.issued", "paid": "invoice.paid", "void": "invoice.voided"}
                if c[1] in name and not (c[0] == "paid" and c[1] == "sent"):
                    out.append((name[c[1]], invoice_data(obj)))
        elif isinstance(obj, Project):
            c = _changed(obj, "status")
            if c and c[0] == "idea" and c[1] in ("active", "paused"):
                out.append(("idea.promoted", project_data(obj)))
        elif isinstance(obj, VoiceNote):
            c = _changed(obj, "transcript_status")
            if c and c[1] == "done" and obj.transcript_source == "whisper":
                out.append(("voice_note.transcribed", note_data(obj)))
    return out


def _subscribers(session: Session, event_name: str) -> list[Webhook]:
    hooks = session.info.get("_webhooks")
    if hooks is None:
        hooks = session.scalars(
            select(Webhook).where(Webhook.active.is_(True), Webhook.deleted_at.is_(None))
        ).all()
        session.info["_webhooks"] = hooks
    return [h for h in hooks if "*" in h.events or event_name in h.events]


def _payload(event_name: str, data: dict, delivery_id: uuid.UUID) -> dict:
    return {
        "id": str(delivery_id),
        "event": event_name,
        "created_at": utcnow().isoformat(),
        "data": data,
    }


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, _ctx) -> None:
    if session.info.get("_webhooks_off"):
        return
    try:
        with session.no_autoflush:
            found = _collect(session)
    except Exception:  # noqa: BLE001 - never break the real write
        log.exception("Couldn't build webhook payloads")
        return
    if found:
        session.info.setdefault("_webhook_events", []).extend(found)


@event.listens_for(Session, "after_flush_postexec")
def _after_flush_postexec(session: Session, _ctx) -> None:
    found = session.info.pop("_webhook_events", None)
    if not found:
        return
    try:
        with session.no_autoflush:
            now = utcnow()
            for name, data in found:
                for hook in _subscribers(session, name):
                    d_id = uuid.uuid4()
                    session.add(
                        WebhookDelivery(
                            id=d_id,
                            webhook_id=hook.id,
                            event=name,
                            payload=_payload(name, data, d_id),
                            next_attempt_at=now,
                        )
                    )
                    session.info["_webhooks_queued"] = True
    except Exception:  # noqa: BLE001
        log.exception("Couldn't queue webhook deliveries")


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    session.info.pop("_webhooks", None)
    if session.info.pop("_webhooks_queued", False):
        _wake.set()


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    for k in ("_webhooks", "_webhook_events", "_webhooks_queued"):
        session.info.pop(k, None)


# --- Time-based events -------------------------------------------------------------------


def _queue_once(db: Session, hook: Webhook, name: str, key: str, data: dict) -> bool:
    exists = db.scalar(
        select(WebhookDelivery.id).where(
            WebhookDelivery.webhook_id == hook.id, WebhookDelivery.dedupe_key == key
        )
    )
    if exists:
        return False
    d_id = uuid.uuid4()
    try:
        with db.begin_nested():
            db.add(
                WebhookDelivery(
                    id=d_id,
                    webhook_id=hook.id,
                    event=name,
                    payload=_payload(name, data, d_id),
                    next_attempt_at=utcnow(),
                    dedupe_key=key,
                )
            )
    except IntegrityError:
        return False
    return True


def scan(db: Session, now: datetime | None = None) -> int:
    """Queue task.due_soon and calendar.event_ended. Returns how many were queued."""
    now = now or utcnow()
    due_hooks = _subscribers(db, "task.due_soon")
    ended_hooks = _subscribers(db, "calendar.event_ended")
    db.info.pop("_webhooks", None)
    queued = 0
    if due_hooks:
        zones = {w.id: ZoneInfo(w.timezone) for w in db.scalars(select(Workspace)).all()}
        horizon = (now + DUE_SOON + timedelta(days=1)).date()
        tasks = db.scalars(
            select(Task)
            .join(BoardColumn, Task.column_id == BoardColumn.id)
            .where(
                Task.deleted_at.is_(None),
                Task.due_date.is_not(None),
                Task.due_date >= (now - timedelta(days=1)).date(),
                Task.due_date <= horizon,
                BoardColumn.is_done.is_(False),
            )
        ).all()
        for t in tasks:
            zone = zones.get(t.workspace_id, UTC)
            due = datetime.combine(t.due_date, t.due_time or DATE_ONLY_DUE, zone)
            # Within the hour before it's due (or just past, if the sweep was late).
            if not (now - timedelta(minutes=10) <= due <= now + DUE_SOON):
                continue
            key = f"task.due_soon:{t.id}:{due.isoformat()}"
            data = task_data(t, db) | {"due_at": due.astimezone(UTC).isoformat()}
            for h in due_hooks:
                queued += _queue_once(db, h, "task.due_soon", key, data)
    if ended_hooks:
        events = db.scalars(
            select(CalendarEvent)
            .join(CalendarEvent.calendar)
            .where(
                Calendar.enabled.is_(True),
                CalendarEvent.deleted_at.is_(None),
                CalendarEvent.project_id.is_not(None),
                CalendarEvent.time_entry_id.is_(None),
                CalendarEvent.log_dismissed.is_(False),
                CalendarEvent.all_day.is_(False),
                CalendarEvent.recurring.is_(False),
                CalendarEvent.origin.is_distinct_from("time_mirror"),
                CalendarEvent.end_at <= now,
                CalendarEvent.end_at >= now - ENDED_WINDOW,
            )
        ).all()
        for e in events:
            key = f"calendar.event_ended:{e.id}:{as_utc(e.end_at).isoformat()}"
            for h in ended_hooks:
                queued += _queue_once(db, h, "calendar.event_ended", key, event_data(e))
    db.commit()
    if queued:
        _wake.set()
    return queued


# --- Sending -----------------------------------------------------------------------------


def send(hook: Webhook, delivery: WebhookDelivery) -> tuple[int | None, str | None]:
    """POST one delivery. Returns (status code, error)."""
    body = json.dumps(delivery.payload, separators=(",", ":"), ensure_ascii=False).encode()
    ts = int(_time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Tahgul-Webhooks/1",
        "X-Tahgul-Event": delivery.event,
        "X-Tahgul-Delivery": str(delivery.id),
        "X-Tahgul-Timestamp": str(ts),
        "X-Tahgul-Signature": sign(crypto.decrypt(hook.secret_encrypted), ts, body),
    }
    try:
        with httpx.Client(timeout=TIMEOUT, transport=_transport, follow_redirects=False) as http:
            r = http.post(hook.url, content=body, headers=headers)
    except httpx.HTTPError as e:
        return None, f"{type(e).__name__}: {e}"[:500]
    if 200 <= r.status_code < 300:
        return r.status_code, None
    return r.status_code, f"HTTP {r.status_code}: {r.text[:200]}"


def deliver_pending(limit: int = 50, now: datetime | None = None) -> int:
    """Send what's due. Returns how many succeeded."""
    ok = 0
    with get_sessionmaker()() as db:
        db.info["_webhooks_off"] = True
        now = now or utcnow()
        due = db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= now)
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(limit)
        ).all()
        for d in due:
            hook = d.webhook
            if hook is None or hook.deleted_at is not None or not hook.active:
                d.status, d.error = "failed", "Webhook removed or turned off."
                db.commit()
                continue
            code, err = send(hook, d)
            d.attempts += 1
            d.response_code, d.error = code, err
            hook.last_delivery_at = utcnow()
            if err is None:
                d.status = "sent"
                hook.last_status = "ok"
                ok += 1
            elif d.attempts > len(BACKOFF):
                d.status = "failed"
                hook.last_status = "failed"
            else:
                d.next_attempt_at = utcnow() + BACKOFF[d.attempts - 1]
                hook.last_status = "retrying"
            db.commit()
    return ok


def prune(db: Session) -> None:
    cutoff = utcnow() - KEEP_DELIVERIES
    for d in db.scalars(
        select(WebhookDelivery).where(
            WebhookDelivery.status != "pending", WebhookDelivery.created_at < cutoff
        )
    ).all():
        db.delete(d)
    db.commit()


def sweep() -> None:
    """The scheduler's job: time-based events, then anything waiting to be sent."""
    try:
        with get_sessionmaker()() as db:
            scan(db)
            prune(db)
        deliver_pending()
    except Exception:  # keep the scheduler alive
        log.exception("Webhook sweep failed")


def dispatch_forever(stop: threading.Event) -> None:
    """Background thread: send soon after a change is committed, and every 30 s."""
    while not stop.is_set():
        _wake.wait(30)
        _wake.clear()
        if stop.is_set():
            break
        try:
            deliver_pending()
        except Exception:  # noqa: BLE001
            log.exception("Webhook delivery failed")
