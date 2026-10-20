from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from ledger.models.base import Base, RecordMixin


class AuditLog(RecordMixin, Base):
    """Every write records who made it and what changed (SPEC: security and privacy)."""

    __tablename__ = "audit_log"

    # owner | token | system | sync
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSON)
