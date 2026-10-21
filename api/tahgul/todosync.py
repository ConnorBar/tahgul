"""Tasks as CalDAV to-dos, so they show in Apple Reminders (SPEC: calendar, "Later").

A reminder list is a VTODO collection on a connected CalDAV account (the Radicale in the
stack makes one; iCloud lists work too). Two-way:

* Tahgul → phone: open tasks with a due date (or every open task, per list) become
  to-dos with title, due date/time, priority and a link back. Finishing a task ticks it
  off; deleting it (or removing its due date, for "due" lists) removes the to-do.
* phone → Tahgul: ticking a reminder off finishes the task (first done column), unticking
  reopens it; editing the title or due date edits the task; a new reminder in the list
  becomes a task in the list's inbox project, if one is set. Deleting a reminder only
  unlinks it (the task stays) until the task changes again.

Conflicts follow the calendar rule: newest change wins, the loser goes to the audit log.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import icalendar
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from tahgul import audit
from tahgul import tasks_service as ts
from tahgul.caldav import CalDAVClient, CalDAVError, PreconditionFailed
from tahgul.calsync import _sync_actor, client_for
from tahgul.config import get_settings
from tahgul.db import get_sessionmaker
from tahgul.models import BoardColumn, Project, ReminderList, Task, TaskTodo
from tahgul.timeutil import as_utc, utcnow

log = logging.getLogger("tahgul.todos")
_lock = threading.Lock()
_wake = threading.Event()

PRIORITY_OUT = {"urgent": 1, "high": 1, "medium": 5, "low": 9, "none": 0}


def priority_in(value: int | None) -> str | None:
    if not value:
        return None
    return "high" if value <= 4 else "medium" if value == 5 else "low"


# --- iCalendar --------------------------------------------------------------------------


def todo_uid(task: Task) -> str:
    return f"task-{task.id}@tahgul"


def build_todo(task: Task, done: bool, zone: ZoneInfo, uid: str | None = None) -> str:
    cal = icalendar.Calendar()
    cal.add("prodid", "-//Tahgul//Tasks//EN")
    cal.add("version", "2.0")
    t = icalendar.Todo()
    t.add("uid", uid or todo_uid(task))  # a to-do made on the phone keeps its UID
    t.add("dtstamp", utcnow())
    t.add("last-modified", as_utc(task.updated_at))
    t.add("summary", task.title)
    lines = [f"#{task.number} · {task.project.name if task.project else ''}".strip(" ·")]
    base = get_settings().app_url
    if base:
        lines.append(f"{base.rstrip('/')}/projects/{task.project_id}?task={task.id}")
    t.add("description", "\n".join(lines))
    if task.due_date:
        if task.due_time:
            t.add("due", datetime.combine(task.due_date, task.due_time, zone).astimezone(UTC))
        else:
            t.add("due", task.due_date)
    pr = PRIORITY_OUT.get(task.priority, 0)
    if pr:
        t.add("priority", pr)
    if task.labels:
        t.add("categories", task.labels)
    if done:
        t.add("status", "COMPLETED")
        t.add("percent-complete", 100)
        t.add("completed", as_utc(task.completed_at or task.updated_at))
    else:
        t.add("status", "NEEDS-ACTION")
    cal.add_component(t)
    return cal.to_ical().decode()


def parse_todo(ics: str, zone: ZoneInfo) -> dict | None:
    try:
        cal = icalendar.Calendar.from_ical(ics)
    except ValueError:
        return None
    for comp in cal.walk("VTODO"):
        due = comp.get("due")
        due_date = due_time = None
        if due is not None:
            v = due.dt
            if isinstance(v, datetime):
                local = (v if v.tzinfo else v.replace(tzinfo=zone)).astimezone(zone)
                due_date, due_time = local.date(), local.time().replace(second=0, microsecond=0)
            elif isinstance(v, date):
                due_date = v
        lm = comp.get("last-modified") or comp.get("dtstamp")
        modified = None
        if lm is not None and isinstance(lm.dt, datetime):
            modified = lm.dt if lm.dt.tzinfo else lm.dt.replace(tzinfo=UTC)
        status = str(comp.get("status", "NEEDS-ACTION")).upper()
        return {
            "uid": str(comp.get("uid", "")),
            "title": str(comp.get("summary", "")).strip() or "Untitled",
            "done": status == "COMPLETED" or comp.get("completed") is not None,
            "due_date": due_date,
            "due_time": due_time,
            "priority": priority_in(int(comp.get("priority", 0) or 0)),
            "modified": modified,
        }
    return None


def _hash(ics_without_stamp: str) -> str:
    return hashlib.sha256(ics_without_stamp.encode()).hexdigest()


def _content_key(task: Task, done: bool) -> str:
    """What decides whether a to-do needs re-sending (not timestamps)."""
    return "|".join(
        str(x)
        for x in (
            task.title,
            task.due_date,
            task.due_time,
            task.priority,
            done,
            sorted(task.labels or []),
            task.project_id,
        )
    )


# --- Which tasks belong on a list -------------------------------------------------------


def _tasks(db: Session, lst: ReminderList) -> list[tuple[Task, bool]]:
    rows = db.execute(
        select(Task, BoardColumn.is_done)
        .join(BoardColumn, Task.column_id == BoardColumn.id)
        .join(Project, Task.project_id == Project.id)
        .where(
            Task.workspace_id == lst.workspace_id,
            Task.deleted_at.is_(None),
            Task.parent_id.is_(None),
            Project.deleted_at.is_(None),
            Project.status != "done",
        )
    ).all()
    return list(rows)


def _wanted(task: Task, done: bool, lst: ReminderList, linked: bool) -> bool:
    if done:
        return linked  # keep showing it ticked off, don't add finished work
    return lst.include == "open" or task.due_date is not None


# --- Sync -------------------------------------------------------------------------------


def _apply_remote(db: Session, task: Task, done_now: bool, f: dict, lst: ReminderList) -> dict:
    """Bring a task in line with its to-do. Returns the changes made."""
    changes: dict = {}
    if f["title"] != task.title:
        changes["title"] = [task.title, f["title"]]
        task.title = f["title"][:300]
    if (f["due_date"], f["due_time"]) != (task.due_date, task.due_time):
        changes["due"] = [str(task.due_date), str(f["due_date"])]
        task.due_date, task.due_time = f["due_date"], f["due_time"] if f["due_date"] else None
    if f["done"] != done_now:
        col = ts.find_column(db, task.project, None, default_done=f["done"])
        changes["status"] = [task.column.name if task.column else None, col.name]
        ts.place(db, task, col, None)
    if changes:
        audit.record(
            db,
            _sync_actor(),
            "update",
            "task",
            task.id,
            changes | {"via": f"Reminders ({lst.name})"},
        )
    return changes


def sync_list(db: Session, client: CalDAVClient, lst: ReminderList) -> dict:
    zone = ZoneInfo(lst.workspace.timezone)
    stats = {"pushed": 0, "pulled": 0, "created": 0, "removed": 0, "conflicts": 0}
    links = {
        t.href: t
        for t in db.scalars(
            select(TaskTodo).where(TaskTodo.list_id == lst.id, TaskTodo.deleted_at.is_(None))
        ).all()
    }
    by_task = {t.task_id: t for t in links.values()}

    # 1. Pull: what changed on the phone since last time.
    ch = client.changes(lst.url, lst.sync_token)
    remote = client.fetch(lst.url, list(ch.changed)) if ch.changed else {}
    deleted = set(ch.deleted)
    if ch.full:
        deleted |= {h for h in links if h not in ch.changed}
    for href in deleted:
        link = links.get(href)
        if link is not None and link.removed_remotely_at is None:
            link.removed_remotely_at = utcnow()
            link.etag = None
            stats["removed"] += 1
    states = {t.id: (t, done) for t, done in _tasks(db, lst)}
    for href, (etag, ics) in remote.items():
        f = parse_todo(ics, zone)
        if f is None:
            continue
        link = links.get(href)
        if link is None:
            # Ours from an earlier link (same UID), or new from the phone.
            if f["uid"].startswith("task-") and f["uid"].endswith("@tahgul"):
                try:
                    tid = uuid.UUID(f["uid"][5:-7])
                except ValueError:
                    continue
                if tid in by_task:
                    continue
                link = TaskTodo(list_id=lst.id, task_id=tid, href=href, uid=f["uid"], etag=etag)
                db.add(link)
                links[href], by_task[tid] = link, link
            elif lst.inbox_project_id and not f["done"]:
                proj = lst.inbox_project
                col = ts.find_column(db, proj, None)
                task = Task(
                    workspace_id=lst.workspace_id,
                    number=ts.next_number(db, lst.workspace_id),
                    project_id=proj.id,
                    column_id=col.id,
                    title=f["title"][:300],
                    due_date=f["due_date"],
                    due_time=f["due_time"],
                    priority=f["priority"] or "none",
                    position=ts.end_position(db, col.id),
                )
                db.add(task)
                db.flush()
                audit.record(
                    db,
                    _sync_actor(),
                    "create",
                    "task",
                    task.id,
                    {"title": task.title, "via": f"Reminders ({lst.name})"},
                )
                link = TaskTodo(
                    list_id=lst.id,
                    task_id=task.id,
                    href=href,
                    uid=f["uid"],
                    etag=etag,
                    pushed_hash=None,
                )
                db.add(link)
                links[href], by_task[task.id] = link, link
                states[task.id] = (task, False)
                stats["created"] += 1
                continue
            else:
                continue
        if link.etag == etag:
            continue  # our own write coming back
        state = states.get(link.task_id)
        link.etag, link.removed_remotely_at = etag, None
        if state is None:
            continue
        task, done_now = state
        local_changed = link.pushed_hash != _hash(_content_key(task, done_now))
        remote_newer = f["modified"] is None or f["modified"] >= as_utc(task.updated_at)
        if local_changed and not remote_newer:
            stats["conflicts"] += 1
            audit.record(
                db,
                _sync_actor(),
                "conflict",
                "task",
                task.id,
                {"winner": "Tahgul", "lost_remote": {"title": f["title"], "done": f["done"]}},
            )
            continue
        if local_changed:
            stats["conflicts"] += 1
            audit.record(
                db,
                _sync_actor(),
                "conflict",
                "task",
                task.id,
                {"winner": "Reminders", "lost_local": {"title": task.title}},
            )
        if _apply_remote(db, task, done_now, f, lst):
            stats["pulled"] += 1
            db.flush()
            db.refresh(task)
            done_now = task.column.is_done if task.column else f["done"]
            states[task.id] = (task, done_now)
        link.pushed_hash = _hash(_content_key(task, done_now))
    lst.sync_token = ch.sync_token
    db.flush()

    # 2. Push: Tahgul's view of every task that belongs on the list.
    for task, done in states.values():
        link = by_task.get(task.id)
        want = _wanted(task, done, lst, link is not None and link.removed_remotely_at is None)
        key = _hash(_content_key(task, done))
        if link is not None and link.removed_remotely_at is not None:
            # Deleted on the phone: leave it off until the task changes here.
            if as_utc(task.updated_at) <= as_utc(link.removed_remotely_at) or not want:
                continue
            link.removed_remotely_at = None
            link.etag = None
            link.pushed_hash = None
        if not want:
            if link is not None and link.etag:
                client.delete(link.href, link.etag)
                link.deleted_at = utcnow()
                stats["removed"] += 1
            continue
        if link is not None and link.pushed_hash == key and link.etag:
            continue
        if link is None:
            href = lst.url.rstrip("/") + f"/tahgul-task-{task.id}.ics"
            link = TaskTodo(list_id=lst.id, task_id=task.id, href=href, uid=todo_uid(task))
            db.add(link)
            by_task[task.id] = link
        try:
            new_etag = client.put(link.href, build_todo(task, done, zone, link.uid), link.etag)
        except PreconditionFailed:
            link.etag = None  # changed on the phone meanwhile: next pull sorts it out
            continue
        except CalDAVError as exc:
            if exc.status == 412:
                continue
            raise
        link.etag = new_etag or client.etag(link.href)
        link.pushed_hash = key
        stats["pushed"] += 1

    # Links to tasks that are gone (deleted in Tahgul).
    for link in list(by_task.values()):
        if link.task_id not in states and link.deleted_at is None:
            if link.etag:
                try:
                    client.delete(link.href, link.etag)
                except CalDAVError:
                    pass
            link.deleted_at = utcnow()
            stats["removed"] += 1

    # The sync token isn't moved past our own writes: the next pull sees them, matches
    # the etags and skips them, and can't miss a phone edit made in between.
    lst.last_synced_at, lst.last_error = utcnow(), None
    return stats


def sync_lists(db: Session) -> dict:
    """Every enabled list. Used by the scheduler, "sync now" and after task changes."""
    if not _lock.acquire(blocking=False):
        return {"skipped": "already running"}
    out: dict = {}
    try:
        lists = db.scalars(
            select(ReminderList).where(
                ReminderList.enabled.is_(True), ReminderList.deleted_at.is_(None)
            )
        ).all()
        for lst in lists:
            client = client_for(lst.account)
            try:
                out[lst.name] = sync_list(db, client, lst)
                db.commit()
            except CalDAVError as exc:
                db.rollback()
                lst = db.get(ReminderList, lst.id)
                lst.last_error = str(exc)
                db.commit()
                out[lst.name] = {"error": str(exc)}
            finally:
                client.close()
    finally:
        _lock.release()
    return out


# --- Prompt pushes after a task changes --------------------------------------------------


@event.listens_for(Session, "after_flush")
def _note_task_changes(session: Session, _ctx) -> None:
    if any(isinstance(o, Task) for o in (*session.new, *session.dirty, *session.deleted)):
        session.info["_todos_dirty"] = True


@event.listens_for(Session, "after_commit")
def _kick(session: Session) -> None:
    if session.info.pop("_todos_dirty", False):
        _wake.set()


def run_forever(stop: threading.Event) -> None:
    """Background thread: push task changes to Reminders a few seconds after they happen."""
    while not stop.is_set():
        if not _wake.wait(60):
            continue
        stop.wait(3)  # let a burst of edits settle
        _wake.clear()
        if stop.is_set():
            break
        try:
            with get_sessionmaker()() as db:
                if db.scalar(select(ReminderList.id).where(ReminderList.enabled.is_(True))):
                    sync_lists(db)
        except Exception:  # noqa: BLE001
            log.exception("Reminders sync failed")
