"""Reminder lists: tasks in Apple Reminders via CalDAV to-dos. Owner only, like calendars."""

import re
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from tahgul import audit, todosync
from tahgul.caldav import CalDAVError
from tahgul.calsync import client_for
from tahgul.crud import get_live, resolve_workspace
from tahgul.db import get_db
from tahgul.deps import require_owner
from tahgul.models import CalendarAccount, Project, ReminderList, TaskTodo
from tahgul.principal import Principal
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1", tags=["reminders"])

Include = Literal["due", "open"]


class ListIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    account_id: uuid.UUID
    workspace_id: uuid.UUID | None = None
    url: str | None = Field(None, description="An existing to-do list; default: make one")
    name: str = Field("Tahgul", min_length=1, max_length=100)
    include: Include = Field("due", description="due = open tasks with a due date; open = all")
    inbox_project_id: uuid.UUID | None = Field(
        None, description="New reminders on the phone become tasks here"
    )


class ListPatch(BaseModel):
    include: Include | None = None
    inbox_project_id: uuid.UUID | None = None
    enabled: bool | None = None


class ListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    account_id: uuid.UUID
    account_name: str = ""
    workspace_id: uuid.UUID
    url: str
    name: str
    enabled: bool
    include: str
    inbox_project_id: uuid.UUID | None
    inbox_project_name: str | None = None
    task_count: int = 0
    last_synced_at: datetime | None
    last_error: str | None


class RemoteList(BaseModel):
    url: str
    name: str
    in_use: bool


def _out(db: Session, lst: ReminderList) -> ListOut:
    o = ListOut.model_validate(lst)
    o.account_name = lst.account.name
    o.inbox_project_name = lst.inbox_project.name if lst.inbox_project else None
    o.task_count = len(
        db.scalars(
            select(TaskTodo.id).where(
                TaskTodo.list_id == lst.id,
                TaskTodo.deleted_at.is_(None),
                TaskTodo.removed_remotely_at.is_(None),
            )
        ).all()
    )
    return o


def _check_inbox(db: Session, project_id: uuid.UUID | None, ws_id: uuid.UUID) -> None:
    if project_id is not None:
        p = get_live(db, Project, project_id, "project")
        if p.workspace_id != ws_id:
            raise HTTPException(422, detail="project_in_other_workspace")


def _sync_one(db: Session, lst: ReminderList) -> dict:
    client = client_for(lst.account)
    try:
        stats = todosync.sync_list(db, client, lst)
        db.commit()
        return stats
    except CalDAVError as exc:
        db.rollback()
        raise HTTPException(
            502, detail={"code": "calendar_server_error", "message": str(exc)}
        ) from None
    finally:
        client.close()


@router.get("/reminder-lists", response_model=list[ListOut])
def list_lists(_: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ReminderList)
        .where(ReminderList.deleted_at.is_(None))
        .order_by(ReminderList.created_at)
    ).all()
    return [_out(db, r) for r in rows]


@router.get("/calendar-accounts/{account_id}/todo-lists", response_model=list[RemoteList])
def remote_lists(
    account_id: uuid.UUID, _: Principal = Depends(require_owner), db: Session = Depends(get_db)
):
    """To-do lists already on the server (e.g. iCloud Reminders lists) you could link."""
    account = get_live(db, CalendarAccount, account_id, "calendar_account")
    client = client_for(account)
    try:
        remote = [c for c in client.calendars() if c.supports_todos]
    except CalDAVError as exc:
        raise HTTPException(
            502, detail={"code": "calendar_server_error", "message": str(exc)}
        ) from None
    finally:
        client.close()
    used = set(db.scalars(select(ReminderList.url).where(ReminderList.deleted_at.is_(None))))
    return [RemoteList(url=c.url, name=c.name, in_use=c.url in used) for c in remote]


@router.post("/reminder-lists", response_model=ListOut, status_code=201)
def create_list(
    body: ListIn, principal: Principal = Depends(require_owner), db: Session = Depends(get_db)
):
    """Link a to-do list (or make a new one) and push tasks to it right away."""
    account = get_live(db, CalendarAccount, body.account_id, "calendar_account")
    ws = resolve_workspace(db, body.workspace_id)
    _check_inbox(db, body.inbox_project_id, ws.id)
    url = body.url
    if url is None:
        slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-") or "tahgul"
        client = client_for(account)
        try:
            existing = {c.url.rstrip("/").rsplit("/", 1)[-1] for c in client.calendars()}
            base, n = f"{slug}-tasks", 2
            slug = base
            while slug in existing:
                slug, n = f"{base}-{n}", n + 1
            url = client.make_calendar(client.home(), slug, body.name, "#2f5d50", component="VTODO")
        except CalDAVError as exc:
            raise HTTPException(
                502, detail={"code": "calendar_server_error", "message": str(exc)}
            ) from None
        finally:
            client.close()
    if db.scalar(
        select(ReminderList.id).where(ReminderList.url == url, ReminderList.deleted_at.is_(None))
    ):
        raise HTTPException(409, detail="reminder_list_in_use")
    lst = ReminderList(
        account_id=account.id,
        workspace_id=ws.id,
        url=url,
        name=body.name,
        include=body.include,
        inbox_project_id=body.inbox_project_id,
    )
    db.add(lst)
    db.flush()
    audit.record(
        db,
        principal,
        "create",
        "reminder_list",
        lst.id,
        {"name": lst.name, "url": url, "include": lst.include},
    )
    db.commit()
    _sync_one(db, lst)
    return _out(db, lst)


@router.patch("/reminder-lists/{list_id}", response_model=ListOut)
def update_list(
    list_id: uuid.UUID,
    body: ListPatch,
    principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    lst = get_live(db, ReminderList, list_id, "reminder_list")
    data = body.model_dump(exclude_unset=True)
    if "inbox_project_id" in data:
        _check_inbox(db, data["inbox_project_id"], lst.workspace_id)
    for k, v in data.items():
        setattr(lst, k, v)
    audit.record(
        db, principal, "update", "reminder_list", lst.id, {k: str(v) for k, v in data.items()}
    )
    db.commit()
    if lst.enabled:
        _sync_one(db, lst)
    return _out(db, lst)


@router.delete("/reminder-lists/{list_id}", status_code=204)
def delete_list(
    list_id: uuid.UUID,
    remove_todos: bool = True,
    principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Stop syncing. By default the to-dos Tahgul put there are removed from the phone too."""
    lst = get_live(db, ReminderList, list_id, "reminder_list")
    links = db.scalars(
        select(TaskTodo).where(TaskTodo.list_id == lst.id, TaskTodo.deleted_at.is_(None))
    ).all()
    if remove_todos and links:
        client = client_for(lst.account)
        try:
            for link in links:
                if link.etag and link.removed_remotely_at is None:
                    client.delete(link.href, link.etag)
        except CalDAVError:
            pass  # the list can go even if the server is away
        finally:
            client.close()
    now = utcnow()
    for link in links:
        link.deleted_at = now
    lst.deleted_at, lst.enabled = now, False
    audit.record(db, principal, "delete", "reminder_list", lst.id, {"name": lst.name})
    db.commit()


@router.post("/reminder-lists/sync")
def sync_now(_: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    return todosync.sync_lists(db)
