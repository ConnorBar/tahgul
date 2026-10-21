"""Tasks, board columns, links, comments, history and saved views."""

import uuid
from collections import defaultdict
from datetime import date, datetime, time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from tahgul import audit, lookup, quickadd
from tahgul import tasks_service as ts
from tahgul.crud import apply_changes, get_live, patch_data, require_admin, resolve_workspace
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import (
    AuditLog,
    BoardColumn,
    Client,
    Comment,
    Project,
    SavedView,
    Task,
    TaskLink,
    TimeEntry,
    Workspace,
)
from tahgul.principal import Principal
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1", tags=["tasks"])

TaskType = Literal["task", "bug", "idea", "request"]
Priority = Literal["none", "low", "medium", "high", "urgent"]
PRIORITY_RANK = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
Label = Annotated[str, Field(min_length=1, max_length=40)]


def _clean_labels(v: list[str] | None) -> list[str] | None:
    if v is None:
        return v
    out: list[str] = []
    for label in v:
        label = label.strip().lstrip("#").lower()
        if label and label not in out:
            out.append(label)
    return out[:20]


# --- Schemas ---------------------------------------------------------------------------


class ColumnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    position: int
    is_done: bool
    task_count: int = 0


class ColumnIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=50)
    is_done: bool = False
    position: int | None = Field(None, description="0-based; default after the last column")


class ColumnPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str | None = Field(None, min_length=1, max_length=50)
    is_done: bool | None = None
    position: int | None = Field(None, ge=0)


class TaskFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    description: str | None = Field(None, max_length=50_000, description="Markdown")
    type: TaskType | None = None
    priority: Priority | None = None
    labels: list[Label] | None = None
    due_date: date | None = None
    due_time: time | None = None
    estimate_minutes: int | None = Field(None, ge=0, le=100_000)
    parent_id: uuid.UUID | None = None

    _labels = field_validator("labels")(_clean_labels)


class TaskIn(TaskFields):
    workspace_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    project: str | None = Field(None, description="Project (or client) name, instead of an id")
    title: str = Field(min_length=1, max_length=300)
    status: str | None = Field(None, description="Column id or name; default the first column")


class TaskPatch(TaskFields):
    title: str | None = Field(None, min_length=1, max_length=300)
    status: str | None = Field(None, description="Column id or name (moves to the bottom)")
    done: bool | None = Field(
        None, description="true = first done column, false = first open column"
    )
    project_id: uuid.UUID | None = Field(None, description="Move to another project")


class MoveIn(BaseModel):
    column_id: uuid.UUID
    index: int = Field(ge=0, description="0 = top of the column")


class TaskOut(BaseModel):
    id: uuid.UUID
    number: int
    ref: str
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    client_name: str | None
    colour: str
    column_id: uuid.UUID
    status: str
    done: bool
    title: str
    description: str | None
    type: str
    priority: str
    labels: list[str]
    due_date: date | None
    due_time: time | None
    overdue: bool
    estimate_minutes: int | None
    parent_id: uuid.UUID | None
    position: float
    completed_at: datetime | None
    subtask_count: int = 0
    subtasks_done: int = 0
    blocked_by: list[uuid.UUID] = []
    blocks: list[uuid.UUID] = []
    blocked: bool = Field(False, description="Waiting on an unfinished blocker")
    tracked_seconds: int = 0
    comment_count: int = 0
    created_at: datetime
    updated_at: datetime


class TaskDetail(TaskOut):
    subtasks: list[TaskOut] = []
    blockers: list[TaskOut] = []
    blocking: list[TaskOut] = []


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    task_id: uuid.UUID | None
    author_type: str
    author_name: str
    body: str
    created_at: datetime


class HistoryItem(BaseModel):
    kind: Literal["comment", "change"]
    at: datetime
    actor_type: str
    actor_name: str
    action: str | None = None
    changes: dict[str, Any] | None = None
    body: str | None = None
    comment_id: uuid.UUID | None = None


class LinkIn(BaseModel):
    task_id: uuid.UUID = Field(description="The task that has to finish first")


class QuickIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    workspace_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = Field(None, description="Default when the text names none")
    dry_run: bool = Field(False, description="Parse only; don't create")


class QuickOut(BaseModel):
    title: str
    priority: str | None
    labels: list[str]
    due_date: date | None
    due_time: time | None
    project_id: uuid.UUID | None
    project_name: str | None
    matched: str | None
    needs_confirmation: bool = False
    candidates: list[dict[str, str]] = []
    task: TaskOut | None = None


class ViewIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    workspace_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    filters: dict[str, Any] = Field(default_factory=dict)


class ViewPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str | None = Field(None, min_length=1, max_length=100)
    filters: dict[str, Any] | None = None
    position: int | None = None


class ViewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    filters: dict[str, Any]
    position: int


# --- Output assembly (batched so lists stay cheap) -------------------------------------


def _task_q():
    return select(Task).options(
        selectinload(Task.column),
        selectinload(Task.project).selectinload(Project.client),
    )


def _load(db: Session, task_id: uuid.UUID) -> Task:
    t = db.scalar(_task_q().where(Task.id == task_id, Task.deleted_at.is_(None)))
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task_not_found")
    return t


def _today(db: Session, ws_id: uuid.UUID) -> date:
    from zoneinfo import ZoneInfo

    tzname = db.scalar(select(Workspace.timezone).where(Workspace.id == ws_id)) or "UTC"
    return datetime.now(ZoneInfo(tzname)).date()


def outs(db: Session, tasks: list[Task]) -> list[TaskOut]:
    if not tasks:
        return []
    ids = [t.id for t in tasks]
    subs = defaultdict(lambda: [0, 0])
    for parent, done in db.execute(
        select(Task.parent_id, Task.completed_at.is_not(None)).where(
            Task.parent_id.in_(ids), Task.deleted_at.is_(None)
        )
    ).all():
        subs[parent][0] += 1
        subs[parent][1] += int(bool(done))
    blocked_by, blocks = defaultdict(list), defaultdict(list)
    blocker_ids = set()
    for t_id, b_id in db.execute(
        select(TaskLink.task_id, TaskLink.blocker_id).where(
            TaskLink.deleted_at.is_(None),
            or_(TaskLink.task_id.in_(ids), TaskLink.blocker_id.in_(ids)),
        )
    ).all():
        if t_id in ids:
            blocked_by[t_id].append(b_id)
            blocker_ids.add(b_id)
        if b_id in ids:
            blocks[b_id].append(t_id)
    open_blockers = (
        set(
            db.scalars(
                select(Task.id).where(
                    Task.id.in_(blocker_ids), Task.completed_at.is_(None), Task.deleted_at.is_(None)
                )
            ).all()
        )
        if blocker_ids
        else set()
    )
    tracked = dict(
        db.execute(
            select(TimeEntry.task_id, func.coalesce(func.sum(TimeEntry.duration_seconds), 0))
            .where(TimeEntry.task_id.in_(ids), TimeEntry.deleted_at.is_(None))
            .group_by(TimeEntry.task_id)
        ).all()
    )
    comments = dict(
        db.execute(
            select(Comment.task_id, func.count())
            .where(Comment.task_id.in_(ids), Comment.deleted_at.is_(None))
            .group_by(Comment.task_id)
        ).all()
    )
    today = _today(db, tasks[0].workspace_id)
    result = []
    for t in tasks:
        done = t.completed_at is not None
        result.append(
            TaskOut(
                id=t.id,
                number=t.number,
                ref=f"#{t.number}",
                workspace_id=t.workspace_id,
                project_id=t.project_id,
                project_name=t.project.name,
                client_name=t.project.client.name if t.project.client else None,
                colour=t.project.colour,
                column_id=t.column_id,
                status=t.column.name,
                done=done,
                title=t.title,
                description=t.description,
                type=t.type,
                priority=t.priority,
                labels=t.labels or [],
                due_date=t.due_date,
                due_time=t.due_time,
                overdue=bool(t.due_date and not done and t.due_date < today),
                estimate_minutes=t.estimate_minutes,
                parent_id=t.parent_id,
                position=t.position,
                completed_at=t.completed_at,
                subtask_count=subs[t.id][0],
                subtasks_done=subs[t.id][1],
                blocked_by=blocked_by[t.id],
                blocks=blocks[t.id],
                blocked=any(b in open_blockers for b in blocked_by[t.id]),
                tracked_seconds=int(tracked.get(t.id, 0) or 0),
                comment_count=int(comments.get(t.id, 0)),
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
        )
    return result


def out(db: Session, t: Task) -> TaskOut:
    return outs(db, [t])[0]


def detail(db: Session, t: Task) -> TaskDetail:
    base = out(db, t)
    subtasks = db.scalars(
        _task_q().where(Task.parent_id == t.id, Task.deleted_at.is_(None)).order_by(Task.created_at)
    ).all()
    blockers = (
        db.scalars(_task_q().where(Task.id.in_(base.blocked_by), Task.deleted_at.is_(None))).all()
        if base.blocked_by
        else []
    )
    blocking = (
        db.scalars(_task_q().where(Task.id.in_(base.blocks), Task.deleted_at.is_(None))).all()
        if base.blocks
        else []
    )
    return TaskDetail(
        **base.model_dump(),
        subtasks=outs(db, list(subtasks)),
        blockers=outs(db, list(blockers)),
        blocking=outs(db, list(blocking)),
    )


# --- Columns ---------------------------------------------------------------------------


def _column_out(db: Session, cols: list[BoardColumn]) -> list[ColumnOut]:
    counts = (
        dict(
            db.execute(
                select(Task.column_id, func.count())
                .where(
                    Task.column_id.in_([c.id for c in cols]),
                    Task.deleted_at.is_(None),
                    Task.parent_id.is_(None),
                )
                .group_by(Task.column_id)
            ).all()
        )
        if cols
        else {}
    )
    res = []
    for c in cols:
        o = ColumnOut.model_validate(c)
        o.task_count = counts.get(c.id, 0)
        res.append(o)
    return res


def _renumber(cols: list[BoardColumn]) -> None:
    for i, c in enumerate(cols):
        c.position = i


@router.get("/projects/{project_id}/columns", response_model=list[ColumnOut], tags=["board"])
def list_columns(
    project_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    project = get_live(db, Project, project_id, "project")
    cols = ts.ensure_columns(db, project)
    db.commit()
    return _column_out(db, cols)


@router.post(
    "/projects/{project_id}/columns",
    response_model=list[ColumnOut],
    status_code=status.HTTP_201_CREATED,
    tags=["board"],
)
def create_column(
    project_id: uuid.UUID,
    body: ColumnIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    project = get_live(db, Project, project_id, "project")
    cols = ts.ensure_columns(db, project)
    if any(c.name.lower() == body.name.lower() for c in cols):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="name_taken")
    col = BoardColumn(project_id=project.id, name=body.name, is_done=body.is_done)
    db.add(col)
    pos = len(cols) if body.position is None else min(body.position, len(cols))
    cols.insert(pos, col)
    _renumber(cols)
    db.flush()
    audit.record(
        db, principal, "create", "board_column", col.id, {"project": project.name, "name": col.name}
    )
    db.commit()
    return _column_out(db, ts.columns(db, project.id))


@router.patch("/columns/{column_id}", response_model=list[ColumnOut], tags=["board"])
def update_column(
    column_id: uuid.UUID,
    body: ColumnPatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    col = get_live(db, BoardColumn, column_id, "column")
    data = patch_data(body, set())
    cols = ts.columns(db, col.project_id)
    if "name" in data and any(
        c.id != col.id and c.name.lower() == data["name"].lower() for c in cols
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="name_taken")
    position = data.pop("position", None)
    changes = apply_changes(col, data)
    if position is not None:
        cols = [c for c in cols if c.id != col.id]
        cols.insert(min(position, len(cols)), col)
        _renumber(cols)
        changes["position"] = position
    if "is_done" in changes:
        # Re-mark tasks in this column as done or not.
        for t in db.scalars(
            select(Task).where(Task.column_id == col.id, Task.deleted_at.is_(None))
        ).all():
            ts.mark_done(t, col)
    if changes:
        audit.record(db, principal, "update", "board_column", col.id, changes)
    db.commit()
    return _column_out(db, ts.columns(db, col.project_id))


@router.delete("/columns/{column_id}", response_model=list[ColumnOut], tags=["board"])
def delete_column(
    column_id: uuid.UUID,
    move_to: uuid.UUID | None = Query(None, description="Column to move its tasks to"),
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    require_admin(principal, "deleting")
    col = get_live(db, BoardColumn, column_id, "column")
    cols = ts.columns(db, col.project_id)
    if len(cols) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="last_column")
    tasks = db.scalars(
        select(Task).where(Task.column_id == col.id, Task.deleted_at.is_(None))
    ).all()
    if tasks:
        if move_to is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="column_has_tasks")
        target = get_live(db, BoardColumn, move_to, "column")
        if target.project_id != col.project_id or target.id == col.id:
            raise HTTPException(422, detail="bad_move_target")
        for t in sorted(tasks, key=lambda x: x.position):
            ts.place(db, t, target, None)
    col.deleted_at = utcnow()
    _renumber([c for c in cols if c.id != col.id])
    audit.record(db, principal, "delete", "board_column", col.id, {"name": col.name})
    db.commit()
    return _column_out(db, ts.columns(db, col.project_id))


# --- Tasks -----------------------------------------------------------------------------


def _create_task(
    db: Session,
    principal: Principal,
    project: Project,
    title: str,
    status_ref: str | None,
    fields: dict,
) -> Task:
    if project.deleted_at is not None:
        raise HTTPException(404, detail="project_not_found")
    column = ts.find_column(db, project, status_ref)
    parent_id = fields.get("parent_id")
    if parent_id is not None:
        parent = get_live(db, Task, parent_id, "parent_task")
        if parent.project_id != project.id:
            raise HTTPException(422, detail="parent_in_other_project")
    t = Task(
        workspace_id=project.workspace_id,
        number=ts.next_number(db, project.workspace_id),
        project_id=project.id,
        column_id=column.id,
        title=title,
        position=ts.end_position(db, column.id),
        **{k: v for k, v in fields.items() if v is not None},
    )
    t.column = column
    ts.mark_done(t, column)
    db.add(t)
    db.flush()
    audit.record(
        db,
        principal,
        "create",
        "task",
        t.id,
        {"title": title, "project": project.name, "status": column.name},
    )
    return t


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Create a task (title, project, due, priority, labels)."""
    if body.project_id is not None:
        project = get_live(db, Project, body.project_id, "project")
    elif body.project:
        project = lookup.find_project(db, resolve_workspace(db, body.workspace_id).id, body.project)
    else:
        raise HTTPException(422, detail="project_required")
    fields = body.model_dump(exclude={"workspace_id", "project_id", "project", "title", "status"})
    t = _create_task(db, principal, project, body.title, body.status, fields)
    db.commit()
    return out(db, _load(db, t.id))


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    workspace_id: uuid.UUID | None = None,
    project_ref: str | None = Query(None, alias="project", description="Project id or name"),
    client_ref: str | None = Query(None, alias="client", description="Client id or name"),
    status_: str | None = Query(None, alias="status", description="Column name"),
    open_: bool | None = Query(None, alias="open", description="true = not done"),
    due_before: date | None = Query(None, description="Due on or before"),
    due_after: date | None = Query(None, description="Due on or after"),
    overdue: bool | None = None,
    label: str | None = None,
    priority: list[Priority] | None = Query(None),
    type_: list[TaskType] | None = Query(None, alias="type"),
    q: str | None = None,
    parent_id: uuid.UUID | None = None,
    top_level: bool | None = Query(None, description="Only tasks without a parent"),
    sort: Literal["board", "due", "priority", "created", "updated", "number"] = "board",
    limit: int = Query(500, ge=1, le=2000),
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Search and list tasks."""
    as_id = lookup._as_uuid(project_ref) if project_ref else None
    if as_id is not None:
        workspace_id = workspace_id or get_live(db, Project, as_id, "project").workspace_id
    ws = resolve_workspace(db, workspace_id)
    project_id = lookup.find_project(db, ws.id, project_ref).id if project_ref else None
    client_id = lookup.find_client(db, ws.id, client_ref).id if client_ref else None
    query = _task_q().join(Task.column).where(Task.workspace_id == ws.id, Task.deleted_at.is_(None))
    if project_id:
        query = query.where(Task.project_id == project_id)
    if client_id:
        query = query.where(
            Task.project_id.in_(select(Project.id).where(Project.client_id == client_id))
        )
    if status_:
        query = query.where(func.lower(BoardColumn.name) == status_.strip().lower())
    if open_ is not None:
        query = query.where(
            Task.completed_at.is_(None) if open_ else Task.completed_at.is_not(None)
        )
    if due_before:
        query = query.where(Task.due_date <= due_before)
    if due_after:
        query = query.where(Task.due_date >= due_after)
    if overdue:
        query = query.where(Task.completed_at.is_(None), Task.due_date < _today(db, ws.id))
    if priority:
        query = query.where(Task.priority.in_(priority))
    if type_:
        query = query.where(Task.type.in_(type_))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Task.title.ilike(like), Task.description.ilike(like)))
    if parent_id:
        query = query.where(Task.parent_id == parent_id)
    if top_level:
        query = query.where(Task.parent_id.is_(None))
    tasks = list(db.scalars(query.limit(5000)).all())
    if label:
        want = label.strip().lstrip("#").lower()
        tasks = [t for t in tasks if want in (t.labels or [])]

    far = date.max
    keys = {
        "board": lambda t: (t.column.position, t.position),
        "due": lambda t: (t.due_date or far, t.due_time or time.max, PRIORITY_RANK[t.priority]),
        "priority": lambda t: (PRIORITY_RANK[t.priority], t.due_date or far),
        "created": lambda t: t.created_at,
        "updated": lambda t: t.updated_at,
        "number": lambda t: t.number,
    }
    tasks.sort(key=keys[sort], reverse=sort in ("created", "updated"))
    return outs(db, tasks[:limit])


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def get_task(
    task_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return detail(db, _load(db, task_id))


@router.get("/tasks/by-number/{number}", response_model=TaskDetail)
def get_task_by_number(
    number: int,
    workspace_id: uuid.UUID | None = None,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Look a task up by its short reference (#12)."""
    ws = resolve_workspace(db, workspace_id)
    t = db.scalar(
        _task_q().where(
            Task.workspace_id == ws.id, Task.number == number, Task.deleted_at.is_(None)
        )
    )
    if t is None:
        raise HTTPException(404, detail="task_not_found")
    return detail(db, t)


NULLABLE = {"description", "labels", "due_date", "due_time", "estimate_minutes", "parent_id"}


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: uuid.UUID,
    body: TaskPatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Update fields or move to another column (status)."""
    t = _load(db, task_id)
    data = patch_data(body, NULLABLE)
    changes: dict = {}

    if "project_id" in data and data["project_id"] != t.project_id:
        project = get_live(db, Project, data.pop("project_id"), "project")
        if project.workspace_id != t.workspace_id:
            raise HTTPException(422, detail="project_in_other_workspace")
        old = t.project.name
        t.project_id, t.project = project.id, project
        # Same-named column in the new project, else its first open column.
        names = {c.name.lower(): c for c in ts.ensure_columns(db, project)}
        target = names.get(t.column.name.lower()) or ts.find_column(db, project, None)
        ts.place(db, t, target, None)
        if t.parent_id:
            t.parent_id = None
        changes["project"] = [old, project.name]
    else:
        data.pop("project_id", None)

    status_ref = data.pop("status", None)
    done = data.pop("done", None)
    if status_ref is not None or done is not None:
        if status_ref is not None:
            col = ts.find_column(db, t.project, status_ref)
        elif done == t.column.is_done:
            col = t.column
        else:
            col = ts.find_column(db, t.project, None, default_done=done)
        if col.id != t.column_id:
            changes["status"] = [t.column.name, col.name]
            ts.place(db, t, col, None)

    if data.get("parent_id") is not None:
        parent = get_live(db, Task, data["parent_id"], "parent_task")
        if parent.project_id != t.project_id:
            raise HTTPException(422, detail="parent_in_other_project")
        if ts.parent_cycle(db, t, parent.id):
            raise HTTPException(422, detail="parent_cycle")
    if "labels" in data and data["labels"] is None:
        data["labels"] = []

    changes |= apply_changes(t, data)
    if changes:
        audit.record(db, principal, "update", "task", t.id, changes)
        db.commit()
    return out(db, _load(db, task_id))


@router.post("/tasks/{task_id}/move", response_model=TaskOut, tags=["board"])
def move_task(
    task_id: uuid.UUID,
    body: MoveIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Drag-and-drop: put a task in a column at a position."""
    t = _load(db, task_id)
    col = get_live(db, BoardColumn, body.column_id, "column")
    if col.project_id != t.project_id:
        raise HTTPException(422, detail="column_in_other_project")
    before = t.column.name
    ts.place(db, t, col, body.index)
    if before != col.name:
        audit.record(db, principal, "update", "task", t.id, {"status": [before, col.name]})
    db.commit()
    return out(db, _load(db, task_id))


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    require_admin(principal, "deleting")
    t = _load(db, task_id)
    now = utcnow()
    # Subtasks go with their parent; time logged on them stays (task link cleared).
    doomed = [t] + list(
        db.scalars(select(Task).where(Task.parent_id == t.id, Task.deleted_at.is_(None))).all()
    )
    for d in doomed:
        d.deleted_at = now
    for link in db.scalars(
        select(TaskLink).where(
            or_(
                TaskLink.task_id.in_([d.id for d in doomed]),
                TaskLink.blocker_id.in_([d.id for d in doomed]),
            )
        )
    ).all():
        link.deleted_at = now
    audit.record(db, principal, "delete", "task", t.id, {"title": t.title})
    db.commit()


# --- Blocked-by links ------------------------------------------------------------------


@router.post("/tasks/{task_id}/blocked-by", response_model=TaskDetail)
def add_blocker(
    task_id: uuid.UUID,
    body: LinkIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    t = _load(db, task_id)
    blocker = _load(db, body.task_id)
    if blocker.workspace_id != t.workspace_id:
        raise HTTPException(422, detail="task_in_other_workspace")
    if ts.would_cycle(db, t.id, blocker.id):
        raise HTTPException(422, detail="link_cycle")
    existing = db.scalar(
        select(TaskLink).where(TaskLink.task_id == t.id, TaskLink.blocker_id == blocker.id)
    )
    if existing is None:
        db.add(TaskLink(task_id=t.id, blocker_id=blocker.id))
    elif existing.deleted_at is not None:
        existing.deleted_at = None
    else:
        return detail(db, t)
    audit.record(db, principal, "link", "task", t.id, {"blocked_by": f"#{blocker.number}"})
    db.commit()
    return detail(db, _load(db, task_id))


@router.delete("/tasks/{task_id}/blocked-by/{blocker_id}", response_model=TaskDetail)
def remove_blocker(
    task_id: uuid.UUID,
    blocker_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    t = _load(db, task_id)
    link = db.scalar(
        select(TaskLink).where(
            TaskLink.task_id == t.id,
            TaskLink.blocker_id == blocker_id,
            TaskLink.deleted_at.is_(None),
        )
    )
    if link is None:
        raise HTTPException(404, detail="link_not_found")
    link.deleted_at = utcnow()
    audit.record(db, principal, "unlink", "task", t.id, {"blocked_by": str(blocker_id)})
    db.commit()
    return detail(db, _load(db, task_id))


# --- Comments and history --------------------------------------------------------------


@router.get("/tasks/{task_id}/comments", response_model=list[CommentOut])
def list_comments(
    task_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    _load(db, task_id)
    return db.scalars(
        select(Comment)
        .where(Comment.task_id == task_id, Comment.deleted_at.is_(None))
        .order_by(Comment.created_at)
    ).all()


@router.post(
    "/tasks/{task_id}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED
)
def add_comment(
    task_id: uuid.UUID,
    body: CommentIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    t = _load(db, task_id)
    c = Comment(
        task_id=t.id, author_type=principal.kind, author_name=principal.name, body=body.body.strip()
    )
    db.add(c)
    db.flush()
    audit.record(db, principal, "comment", "task", t.id, {"comment": str(c.id)})
    db.commit()
    return c


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(
    comment_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    c = get_live(db, Comment, comment_id, "comment")
    # Your own comment can go; anyone else's needs admin.
    if not (c.author_type == principal.kind and c.author_name == principal.name):
        require_admin(principal, "deleting")
    c.deleted_at = utcnow()
    db.commit()


@router.get("/tasks/{task_id}/history", response_model=list[HistoryItem])
def task_history(
    task_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Comments and every change, oldest first, with who made them (you, an agent, sync)."""
    _load(db, task_id)
    items: list[HistoryItem] = []
    for a in db.scalars(
        select(AuditLog).where(
            AuditLog.entity == "task",
            AuditLog.entity_id == str(task_id),
            AuditLog.action != "comment",
        )
    ).all():
        items.append(
            HistoryItem(
                kind="change",
                at=a.created_at,
                actor_type=a.actor_type,
                actor_name=a.actor_name,
                action=a.action,
                changes=a.changes,
            )
        )
    for c in db.scalars(
        select(Comment).where(Comment.task_id == task_id, Comment.deleted_at.is_(None))
    ).all():
        items.append(
            HistoryItem(
                kind="comment",
                at=c.created_at,
                actor_type=c.author_type,
                actor_name=c.author_name,
                body=c.body,
                comment_id=c.id,
            )
        )
    items.sort(key=lambda x: x.at)
    return items


# --- Quick add -------------------------------------------------------------------------


def _targets(db: Session, ws_id: uuid.UUID) -> list[quickadd.Target]:
    projects = db.scalars(
        select(Project).where(Project.workspace_id == ws_id, Project.deleted_at.is_(None))
    ).all()
    clients = db.scalars(
        select(Client).where(Client.workspace_id == ws_id, Client.deleted_at.is_(None))
    ).all()
    open_by_client = defaultdict(list)
    for p in projects:
        if p.client_id and p.status in ("active", "paused"):
            open_by_client[p.client_id].append(str(p.id))
    return [
        *(quickadd.Target("project", str(p.id), p.name) for p in projects if p.status != "done"),
        *(
            quickadd.Target("client", str(c.id), c.name, c.code, open_by_client[c.id])
            for c in clients
        ),
    ]


def quick_task(
    db: Session,
    principal: Principal,
    text: str,
    ws_id: uuid.UUID,
    default_project: Project | None,
    dry_run: bool,
) -> QuickOut:
    parsed = quickadd.parse(text, _today(db, ws_id), _targets(db, ws_id))
    project_id = parsed.project_id or (
        str(default_project.id) if default_project and not parsed.client_id else None
    )
    candidates = []
    if parsed.candidates and not project_id:
        rows = db.scalars(
            select(Project).where(Project.id.in_([uuid.UUID(x) for x in parsed.candidates]))
        ).all()
        candidates = [{"id": str(p.id), "name": p.name} for p in rows]
        if default_project and str(default_project.id) in parsed.candidates:
            project_id = str(default_project.id)
    project = db.get(Project, uuid.UUID(project_id)) if project_id else None
    result = QuickOut(
        title=parsed.title,
        priority=parsed.priority,
        labels=parsed.labels,
        due_date=parsed.due_date,
        due_time=parsed.due_time,
        project_id=project.id if project else None,
        project_name=project.name if project else None,
        matched=parsed.matched,
        needs_confirmation=project is None,
        candidates=candidates if project is None else [],
    )
    if dry_run or project is None:
        return result
    fields = {
        "priority": parsed.priority,
        "labels": parsed.labels,
        "due_date": parsed.due_date,
        "due_time": parsed.due_time,
    }
    t = _create_task(db, principal, project, parsed.title, None, fields)
    db.commit()
    result.task = out(db, _load(db, t.id))
    return result


@router.post("/tasks/quick", response_model=QuickOut)
def quick_add_task(
    body: QuickIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Quick add: "Fix VPN for Acme tomorrow !high #network". Use dry_run to preview."""
    default_project = get_live(db, Project, body.project_id, "project") if body.project_id else None
    ws = resolve_workspace(
        db, body.workspace_id or (default_project and default_project.workspace_id)
    )
    return quick_task(db, principal, body.text, ws.id, default_project, body.dry_run)


# --- Saved views -----------------------------------------------------------------------

VIEW_KEYS = {
    "project",
    "client",
    "status",
    "open",
    "due_before",
    "due_after",
    "overdue",
    "label",
    "priority",
    "type",
    "q",
    "top_level",
    "sort",
    "due_within_days",
}


def _check_filters(f: dict | None) -> dict | None:
    if f is None:
        return f
    bad = set(f) - VIEW_KEYS
    if bad:
        raise HTTPException(422, detail=f"unknown_filters:{','.join(sorted(bad))}")
    return f


@router.get("/views", response_model=list[ViewOut], tags=["views"])
def list_views(
    workspace_id: uuid.UUID | None = None,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    ws = resolve_workspace(db, workspace_id)
    return db.scalars(
        select(SavedView)
        .where(SavedView.workspace_id == ws.id, SavedView.deleted_at.is_(None))
        .order_by(SavedView.position, SavedView.created_at)
    ).all()


@router.post("/views", response_model=ViewOut, status_code=status.HTTP_201_CREATED, tags=["views"])
def create_view(
    body: ViewIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    ws = resolve_workspace(db, body.workspace_id)
    _check_filters(body.filters)
    count = (
        db.scalar(
            select(func.count())
            .select_from(SavedView)
            .where(SavedView.workspace_id == ws.id, SavedView.deleted_at.is_(None))
        )
        or 0
    )
    v = SavedView(workspace_id=ws.id, name=body.name, filters=body.filters, position=count)
    db.add(v)
    db.flush()
    audit.record(db, principal, "create", "saved_view", v.id, {"name": v.name})
    db.commit()
    return v


@router.patch("/views/{view_id}", response_model=ViewOut, tags=["views"])
def update_view(
    view_id: uuid.UUID,
    body: ViewPatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    v = get_live(db, SavedView, view_id, "view")
    data = patch_data(body, set())
    _check_filters(data.get("filters"))
    changes = apply_changes(v, data)
    if changes:
        audit.record(db, principal, "update", "saved_view", v.id, changes)
        db.commit()
    return v


@router.delete("/views/{view_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["views"])
def delete_view(
    view_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    v = get_live(db, SavedView, view_id, "view")
    v.deleted_at = utcnow()
    audit.record(db, principal, "delete", "saved_view", v.id, {"name": v.name})
    db.commit()
