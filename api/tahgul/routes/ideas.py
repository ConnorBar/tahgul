"""Ideas: projects in status "idea", usually without a client (SPEC: ideas inbox).

Voice notes attach here in milestone 8; "promote" turns an idea into a project.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tahgul import audit, tasks_service
from tahgul.crud import get_live, resolve_workspace
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import Project
from tahgul.principal import Principal
from tahgul.routes.projects import ProjectOut, _load, _out

router = APIRouter(prefix="/api/v1/ideas", tags=["ideas"])


class IdeaIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    workspace_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=20_000)


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


@router.get("", response_model=list[ProjectOut])
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
    return [_out(_load(db, p.id)) for p in rows]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_idea(
    body: IdeaIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Create an idea project (a voice note can follow via /voice-notes in milestone 8)."""
    ws = resolve_workspace(db, body.workspace_id)
    p = Project(
        workspace_id=ws.id,
        name=_free_name(db, ws.id, body.title),
        description=body.description,
        status="idea",
        colour="#8a5a9e",
    )
    db.add(p)
    db.flush()
    tasks_service.ensure_columns(db, p)
    audit.record(db, principal, "create", "project", p.id, {"idea": p.name})
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
