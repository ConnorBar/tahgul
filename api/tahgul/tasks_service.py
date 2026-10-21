"""Board rules: columns, numbering, ordering, done-tracking and blockers."""

import uuid
from collections import defaultdict

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tahgul.models import BoardColumn, Project, Task, TaskLink, Workspace
from tahgul.models.tasks import DEFAULT_COLUMNS
from tahgul.timeutil import utcnow


def ensure_columns(db: Session, project: Project) -> list[BoardColumn]:
    """Every project starts with To do / In progress / Done."""
    cols = columns(db, project.id)
    if cols:
        return cols
    for i, (name, done) in enumerate(DEFAULT_COLUMNS):
        db.add(BoardColumn(project_id=project.id, name=name, position=i, is_done=done))
    db.flush()
    return columns(db, project.id)


def columns(db: Session, project_id: uuid.UUID) -> list[BoardColumn]:
    return list(
        db.scalars(
            select(BoardColumn)
            .where(BoardColumn.project_id == project_id, BoardColumn.deleted_at.is_(None))
            .order_by(BoardColumn.position, BoardColumn.created_at)
        ).all()
    )


def find_column(
    db: Session, project: Project, ref: uuid.UUID | str | None, *, default_done: bool = False
) -> BoardColumn:
    """A column by id or (case-insensitive) name; default is the first open column."""
    cols = ensure_columns(db, project)
    if ref is None or ref == "":
        if default_done:
            return next((c for c in cols if c.is_done), cols[-1])
        return next((c for c in cols if not c.is_done), cols[0])
    for c in cols:
        if str(c.id) == str(ref) or c.name.lower() == str(ref).strip().lower():
            return c
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "unknown_status", "choices": [c.name for c in cols]},
    )


def next_number(db: Session, workspace_id: uuid.UUID) -> int:
    # Lock the workspace row so two tasks never get the same number (Postgres).
    db.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    current = db.scalar(select(func.max(Task.number)).where(Task.workspace_id == workspace_id))
    return (current or 0) + 1


def end_position(db: Session, column_id: uuid.UUID) -> float:
    current = db.scalar(
        select(func.max(Task.position)).where(
            Task.column_id == column_id, Task.deleted_at.is_(None)
        )
    )
    return (current if current is not None else -1) + 1


def place(db: Session, task: Task, column: BoardColumn, index: int | None) -> None:
    """Put the task in a column at an index (None = bottom), renumbering that column."""
    moving_in = task.column_id != column.id
    task.column_id = column.id
    task.column = column
    if index is None and moving_in:
        task.position = end_position(db, column.id)
    elif index is not None:
        siblings = [
            t
            for t in db.scalars(
                select(Task)
                .where(Task.column_id == column.id, Task.deleted_at.is_(None), Task.id != task.id)
                .order_by(Task.position, Task.created_at)
            ).all()
        ]
        index = max(0, min(index, len(siblings)))
        siblings.insert(index, task)
        for i, t in enumerate(siblings):
            t.position = float(i)
    mark_done(task, column)


def mark_done(task: Task, column: BoardColumn) -> None:
    if column.is_done and task.completed_at is None:
        task.completed_at = utcnow()
    elif not column.is_done:
        task.completed_at = None


def would_cycle(db: Session, task_id: uuid.UUID, blocker_id: uuid.UUID) -> bool:
    """True if ``blocker`` already (transitively) waits on ``task``."""
    if task_id == blocker_id:
        return True
    edges: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for t, b in db.execute(
        select(TaskLink.task_id, TaskLink.blocker_id).where(TaskLink.deleted_at.is_(None))
    ).all():
        edges[t].append(b)
    seen, stack = set(), [blocker_id]
    while stack:
        cur = stack.pop()
        if cur == task_id:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(edges.get(cur, []))
    return False


def parent_cycle(db: Session, task: Task, parent_id: uuid.UUID) -> bool:
    cur: uuid.UUID | None = parent_id
    seen = set()
    while cur is not None and cur not in seen:
        if cur == task.id:
            return True
        seen.add(cur)
        cur = db.scalar(select(Task.parent_id).where(Task.id == cur))
    return False
