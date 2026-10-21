from sqlalchemy import Index, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from tahgul.models.base import Base, RecordMixin


class IdempotencyKey(RecordMixin, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (Index("uq_idempotency_owner_key", "owner_hash", "key", unique=True),)

    owner_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    status_code: Mapped[int | None] = mapped_column()
    content_type: Mapped[str | None] = mapped_column(String(100))
    response_body: Mapped[bytes | None] = mapped_column(LargeBinary)
