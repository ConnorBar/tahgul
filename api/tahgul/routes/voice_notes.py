"""Voice notes: upload (Yog or curl), list, play back with range requests, transcripts.

    curl -H "Authorization: Bearer thg_…" -F file=@note.m4a \\
         -F 'metadata={"title": "Drone surveys", "new_idea": {"title": "Drone surveys"}}' \\
         https://tahgul.<tailnet>.ts.net/api/v1/voice-notes
"""

import json
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from tahgul import audio, audit, lookup
from tahgul.config import get_settings
from tahgul.crud import get_live, require_admin, resolve_workspace
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import Project, Task, VoiceNote, Workspace
from tahgul.principal import Principal
from tahgul.routes.ideas import make_idea
from tahgul.timekeeping import ensure_aware
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1/voice-notes", tags=["voice notes"])


class NewIdea(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=20_000)


class NoteMeta(BaseModel):
    """The ``metadata`` form field (JSON). Every field is optional."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    yog_id: str | None = Field(None, max_length=100, description="Yog's id; sent twice = one note")
    recorded_at: datetime | None = Field(None, description="Default: now")
    duration_seconds: float | None = Field(None, ge=0, le=24 * 3600)
    title: str | None = Field(None, max_length=300)
    transcript: str | None = Field(None, max_length=500_000)
    workspace_id: uuid.UUID | None = None
    # Where it goes: one of these, else a new idea in the inbox.
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    project: str | None = Field(None, description="Project name instead of project_id")
    new_idea: NewIdea | None = None


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str | None
    recorded_at: datetime
    duration_seconds: float | None
    mime_type: str
    size_bytes: int
    project_id: uuid.UUID | None
    project_name: str | None = None
    project_status: str | None = None
    task_id: uuid.UUID | None
    task_ref: str | None = None
    task_title: str | None = None
    transcript: str | None
    transcript_source: str | None
    transcript_status: str
    yog_id: str | None
    source: str
    playback_url: str = ""
    download_url: str = ""
    duplicate: bool = Field(False, description="True when this yog_id was already stored")
    created_at: datetime


class NotePatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str | None = Field(None, max_length=300)
    transcript: str | None = Field(None, max_length=500_000)
    recorded_at: datetime | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None


def _out(n: VoiceNote, duplicate: bool = False) -> NoteOut:
    o = NoteOut.model_validate(n)
    if n.project is not None:
        o.project_name, o.project_status = n.project.name, n.project.status
    if n.task is not None:
        o.task_ref, o.task_title = f"#{n.task.number}", n.task.title
    o.playback_url = f"/api/v1/voice-notes/{n.id}/audio"
    o.download_url = f"/api/v1/voice-notes/{n.id}/audio?download=true"
    o.duplicate = duplicate
    return o


def _load(db: Session, note_id: uuid.UUID) -> VoiceNote:
    n = db.scalar(
        select(VoiceNote)
        .options(selectinload(VoiceNote.project), selectinload(VoiceNote.task))
        .where(VoiceNote.id == note_id, VoiceNote.deleted_at.is_(None))
    )
    if n is None:
        raise HTTPException(404, detail="voice_note_not_found")
    return n


def _whisper_on() -> bool:
    return bool(get_settings().whisper_url)


def _target(
    db: Session, principal: Principal, meta: NoteMeta, when: datetime
) -> tuple[Workspace, Project, Task | None]:
    if meta.task_id:
        task = get_live(db, Task, meta.task_id, "task")
        return task.project.workspace, task.project, task
    if meta.project_id:
        p = get_live(db, Project, meta.project_id, "project")
        return p.workspace, p, None
    ws = resolve_workspace(db, meta.workspace_id)
    if meta.project:
        return ws, lookup.find_project(db, ws.id, meta.project), None
    idea = meta.new_idea or NewIdea(
        title=meta.title or f"Voice note {when.astimezone(_zone(ws)):%b %-d, %-I:%M %p}"
    )
    return ws, make_idea(db, principal, ws.id, idea.title, idea.description), None


def _zone(ws: Workspace) -> ZoneInfo:
    return ZoneInfo(ws.timezone)


def _limit_size(request: Request) -> None:
    """Refuse an oversized upload before reading it, when the client says its size."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > audio.MAX_BYTES + 1024 * 1024:
        raise HTTPException(413, detail="audio_too_large")


@router.post("", response_model=NoteOut, status_code=201, dependencies=[Depends(_limit_size)])
def upload(
    response: Response,
    file: UploadFile = File(description="m4a, mp3, wav, webm or ogg; up to 200 MB"),
    metadata: str | None = Form(None, description="JSON, see NoteMeta"),
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Store a recording and attach it to a project, task or a new idea (the default)."""
    try:
        meta = NoteMeta.model_validate(json.loads(metadata) if metadata else {})
    except (json.JSONDecodeError, ValidationError) as e:
        raise HTTPException(422, detail=f"invalid_metadata: {e}") from None
    if meta.yog_id:
        existing = db.scalar(
            select(VoiceNote).where(VoiceNote.yog_id == meta.yog_id, VoiceNote.deleted_at.is_(None))
        )
        if existing is not None:
            response.status_code = 200
            return _out(_load(db, existing.id), duplicate=True)

    now = utcnow()
    when = meta.recorded_at or now
    if when.tzinfo is None:
        when = when.replace(tzinfo=_zone(resolve_workspace(db, meta.workspace_id)))
    when = when.astimezone(UTC)
    try:
        stored = audio.store(file.file, when)
    except audio.AudioError as e:
        raise HTTPException(413 if str(e) == "audio_too_large" else 415, detail=str(e)) from None
    try:
        ws, project, task = _target(db, principal, meta, when)
        n = VoiceNote(
            workspace_id=ws.id,
            project_id=project.id,
            task_id=task.id if task else None,
            title=meta.title or (file.filename or None),
            recorded_at=when,
            duration_seconds=meta.duration_seconds or audio.duration(stored.path),
            path=stored.path,
            mime_type=stored.mime_type,
            size_bytes=stored.size,
            sha256=stored.sha256,
            original_filename=(file.filename or "")[:300] or None,
            transcript=meta.transcript,
            transcript_source="yog" if meta.transcript else None,
            transcript_status="done"
            if meta.transcript
            else ("pending" if _whisper_on() else "none"),
            yog_id=meta.yog_id,
            source="yog"
            if meta.yog_id
            else principal.kind
            if principal.kind != "owner"
            else "upload",
        )
        db.add(n)
        db.flush()
        audit.record(
            db,
            principal,
            "create",
            "voice_note",
            n.id,
            {"project": project.name, "bytes": stored.size, "yog_id": meta.yog_id},
        )
        db.commit()
    except IntegrityError:
        # Two uploads of the same yog_id at once: keep the first.
        db.rollback()
        audio.remove(stored.path)
        existing = db.scalar(select(VoiceNote).where(VoiceNote.yog_id == meta.yog_id))
        response.status_code = 200
        return _out(_load(db, existing.id), duplicate=True)
    except BaseException:
        db.rollback()
        audio.remove(stored.path)
        raise
    return _out(_load(db, n.id))


@router.get("", response_model=list[NoteOut])
def list_notes(
    workspace_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    unfiled: bool = Query(False, description="Only notes whose project or task was deleted"),
    q: str | None = Query(None, description="Search titles and transcripts"),
    limit: int = Query(100, ge=1, le=500),
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Newest first."""
    query = (
        select(VoiceNote)
        .options(selectinload(VoiceNote.project), selectinload(VoiceNote.task))
        .where(VoiceNote.deleted_at.is_(None))
    )
    if project_id:
        query = query.where(VoiceNote.project_id == project_id)
    if task_id:
        query = query.where(VoiceNote.task_id == task_id)
    if not (project_id or task_id):
        query = query.where(VoiceNote.workspace_id == resolve_workspace(db, workspace_id).id)
    if unfiled:
        query = query.where(VoiceNote.project_id.is_(None))
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(VoiceNote.title).like(like),
                func.lower(VoiceNote.transcript).like(like),
            )
        )
    rows = db.scalars(query.order_by(VoiceNote.recorded_at.desc()).limit(limit)).all()
    return [_out(n) for n in rows]


@router.get("/{note_id}", response_model=NoteOut)
def get_note(
    note_id: uuid.UUID,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return _out(_load(db, note_id))


@router.get("/{note_id}/audio", response_class=FileResponse)
def play(
    note_id: uuid.UUID,
    download: bool = False,
    _: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """The audio itself. Supports Range requests, so long notes seek instantly."""
    n = _load(db, note_id)
    path = audio.data_path(n.path)
    if not path.exists():
        raise HTTPException(410, detail="audio_file_missing")
    ext = n.path.rsplit(".", 1)[-1]
    stem = (n.title or n.original_filename or "voice-note").rsplit(".", 1)[0]
    safe = "".join(c if c.isalnum() or c in " -_" else "-" for c in stem).strip()[:80] or "note"
    return FileResponse(
        path,
        media_type=n.mime_type,
        filename=f"{safe}.{ext}",
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@router.patch("/{note_id}", response_model=NoteOut)
def update_note(
    note_id: uuid.UUID,
    body: NotePatch,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Rename, correct the transcript, or move the note to another project or task."""
    n = _load(db, note_id)
    data = body.model_dump(exclude_unset=True)
    changes: dict = {}
    if "title" in data:
        changes["title"] = [n.title, data["title"] or None]
        n.title = data["title"] or None
    if "transcript" in data:
        n.transcript = data["transcript"] or None
        n.transcript_source = "owner" if n.transcript else None
        n.transcript_status = "done" if n.transcript else "none"
        n.transcript_error = None
        changes["transcript"] = "edited"
    if data.get("recorded_at"):
        n.recorded_at = ensure_aware(data["recorded_at"], _zone(n.workspace)).astimezone(UTC)
        changes["recorded_at"] = str(n.recorded_at)
    if "task_id" in data and data["task_id"]:
        task = get_live(db, Task, data["task_id"], "task")
        if task.workspace_id != n.workspace_id:
            raise HTTPException(422, detail="task_in_other_workspace")
        n.task_id, n.project_id = task.id, task.project_id
        changes["task"] = f"#{task.number}"
    elif "project_id" in data or "task_id" in data:
        if data.get("project_id"):
            p = get_live(db, Project, data["project_id"], "project")
            if p.workspace_id != n.workspace_id:
                raise HTTPException(422, detail="project_in_other_workspace")
            n.project_id = p.id
            changes["project"] = p.name
        if "task_id" in data or data.get("project_id"):
            n.task_id = None
    if changes:
        audit.record(db, principal, "update", "voice_note", n.id, changes)
        db.commit()
        db.expire(n)  # reload project/task after a move
    return _out(_load(db, n.id))


@router.post("/{note_id}/transcribe", response_model=NoteOut)
def queue_transcription(
    note_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Ask the Whisper service for a (new) transcript; it runs in the background."""
    if not _whisper_on():
        raise HTTPException(409, detail="whisper_not_configured")
    n = _load(db, note_id)
    n.transcript_status, n.transcript_error = "pending", None
    audit.record(db, principal, "transcribe", "voice_note", n.id, {})
    db.commit()
    return _out(n)


@router.delete("/{note_id}", status_code=204)
def delete_note(
    note_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Delete the note and its audio file. Can't be undone."""
    require_admin(principal, "deleting")
    n = _load(db, note_id)
    n.deleted_at = utcnow()
    audit.record(
        db,
        principal,
        "delete",
        "voice_note",
        n.id,
        {"title": n.title, "path": n.path, "yog_id": n.yog_id},
    )
    n.yog_id = None  # Yog sending it again later makes a fresh note
    db.commit()
    audio.remove(n.path)


def unfile_for_tasks(db: Session, task_ids: list[uuid.UUID]) -> int:
    """A deleted task's notes move up to its project; the audio is never deleted with it."""
    rows = db.scalars(
        select(VoiceNote).where(VoiceNote.task_id.in_(task_ids), VoiceNote.deleted_at.is_(None))
    ).all()
    for n in rows:
        n.task_id = None
    return len(rows)


def unfile_for_project(db: Session, project_id: uuid.UUID) -> int:
    """A deleted project's notes become unfiled (shown in the ideas inbox)."""
    rows = db.scalars(
        select(VoiceNote).where(VoiceNote.project_id == project_id, VoiceNote.deleted_at.is_(None))
    ).all()
    for n in rows:
        n.project_id = n.task_id = None
    return len(rows)
