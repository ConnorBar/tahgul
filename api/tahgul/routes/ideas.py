"""Ideas: projects in status "idea", usually without a client (SPEC: ideas inbox).

Voice notes attach here in milestone 8; "promote" turns an idea into a project.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tahgul import audit, tasks_service
from tahgul.crud import get_live, resolve_workspace
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import BoardColumn, Project, Task, VoiceNote
from tahgul.principal import Principal
from tahgul.routes.projects import ProjectOut, _load, _out

router = APIRouter(prefix="/api/v1/ideas", tags=["ideas"])


class IdeaIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    workspace_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=20_000)
    voice_note_id: uuid.UUID | None = Field(None, description="Move an existing note onto it")


class IdeaOut(ProjectOut):
    voice_note_count: int = 0
    last_note_at: datetime | None = None
    open_task_count: int = 0


class PromoteIn(BaseModel):
    client_id: uuid.UUID | None = None
    status: str = Field("active", pattern="^(active|paused)$")


def _free_name(db: Session, ws_id: uuid.UUID, title: str) -> str:
    """Ideas are captured fast; a clashing name gets a number instead of an error."""
    taken = {
        n.lower()
        for n in db.scalars(
            select(Project.name).where(
                Project.workspace_id == ws_id,
                Project.deleted_at.is_(None),
                func.lower(Project.name).like(f"{title.lower()[:150]}%"),
            )
        ).all()
    }
    if title.lower() not in taken:
        return title
    n = 2
    while f"{title} ({n})".lower() in taken:
        n += 1
    return f"{title} ({n})"


def make_idea(
    db: Session, principal: Principal, ws_id: uuid.UUID, title: str, description: str | None
) -> Project:
    p = Project(
        workspace_id=ws_id,
        name=_free_name(db, ws_id, title[:200]),
        description=description,
        status="idea",
        colour="#8a5a9e",
    )
    db.add(p)
    db.flush()
    tasks_service.ensure_columns(db, p)
    audit.record(db, principal, "create", "project", p.id, {"idea": p.name})
    return p


@router.get("", response_model=list[IdeaOut])
def list_ideas(
    workspace_id: uuid.UUID | None = None,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    ws = resolve_workspace(db, workspace_id)
    rows = db.scalars(
        select(Project)
        .where(
            Project.workspace_id == ws.id, Project.status == "idea", Project.deleted_at.is_(None)
        )
        .order_by(Project.created_at.desc())
    ).all()
    notes = {
        pid: (n, last)
        for pid, n, last in db.execute(
            select(VoiceNote.project_id, func.count(), func.max(VoiceNote.recorded_at))
            .where(VoiceNote.project_id.in_([p.id for p in rows]), VoiceNote.deleted_at.is_(None))
            .group_by(VoiceNote.project_id)
        ).all()
    }
    open_tasks = dict(
        db.execute(
            select(Task.project_id, func.count())
            .join(BoardColumn, Task.column_id == BoardColumn.id)
            .where(
                Task.project_id.in_([p.id for p in rows]),
                Task.deleted_at.is_(None),
                BoardColumn.is_done.is_(False),
            )
            .group_by(Task.project_id)
        ).all()
    )
    out = []
    for p in rows:
        o = IdeaOut.model_validate(_out(_load(db, p.id)).model_dump())
        o.voice_note_count, o.last_note_at = notes.get(p.id, (0, None))
        o.open_task_count = open_tasks.get(p.id, 0)
        out.append(o)
    # Ideas you talked about most recently first.
    out.sort(key=lambda o: o.last_note_at or o.created_at, reverse=True)
    return out


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_idea(
    body: IdeaIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Create an idea project. Attach audio with /voice-notes (new_idea or project_id)."""
    ws = resolve_workspace(db, body.workspace_id)
    p = make_idea(db, principal, ws.id, body.title, body.description)
    if body.voice_note_id:
        note = get_live(db, VoiceNote, body.voice_note_id, "voice_note")
        if note.workspace_id != ws.id:
            raise HTTPException(422, detail="voice_note_in_other_workspace")
        note.project_id, note.task_id = p.id, None
    db.commit()
    return _out(_load(db, p.id))


@router.post("/{project_id}/promote", response_model=ProjectOut)
def promote(
    project_id: uuid.UUID,
    body: PromoteIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Turn an idea into a real project, keeping its notes and tasks."""
    p = get_live(db, Project, project_id, "project")
    if p.status != "idea":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="not_an_idea")
    if body.client_id:
        from tahgul.models import Client

        c = get_live(db, Client, body.client_id, "client")
        if c.workspace_id != p.workspace_id:
            raise HTTPException(422, detail="client_in_other_workspace")
        p.client_id = c.id
    p.status = body.status
    audit.record(db, principal, "promote", "project", p.id, {"status": ["idea", body.status]})
    db.commit()
    return _out(_load(db, p.id))
