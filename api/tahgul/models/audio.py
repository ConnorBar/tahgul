"""Voice notes (SPEC: "Audio and Yog integration")."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tahgul.models.base import Base, RecordMixin, UTCDateTime
from tahgul.models.tasks import Task
from tahgul.models.work import Project, Workspace

TRANSCRIPT_STATES = ("none", "pending", "done", "failed")


class VoiceNote(RecordMixin, Base):
    """An audio file on disk plus what it's about. Yog records; Tahgul stores and plays."""

    __tablename__ = "voice_notes"
    __table_args__ = (UniqueConstraint("workspace_id", "yog_id"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), index=True, nullable=False
    )
    # Where it belongs. Both empty = unfiled (its project or task was deleted).
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)

    title: Mapped[str | None] = mapped_column(String(300))
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    # File: relative to the data dir, audio/{yyyy}/{mm}/{uuid}.{ext}
    path: Mapped[str] = mapped_column(String(300), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(300))

    transcript: Mapped[str | None] = mapped_column(Text)
    # Who wrote the transcript: yog, whisper or owner (typed or corrected by hand).
    transcript_source: Mapped[str | None] = mapped_column(String(10))
    transcript_status: Mapped[str] = mapped_column(String(10), nullable=False, default="none")
    transcript_error: Mapped[str | None] = mapped_column(String(500))

    # Yog's own id: the same note sent twice is stored once.
    yog_id: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="upload")

    workspace: Mapped[Workspace] = relationship()
    project: Mapped[Project | None] = relationship()
    task: Mapped[Task | None] = relationship()
