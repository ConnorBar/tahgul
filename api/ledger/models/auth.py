import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ledger.models.base import Base, RecordMixin


class User(RecordMixin, Base):
    """The single owner account."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Pending secret during setup, active once totp_enabled is true.
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Last accepted TOTP time step, so a code can't be replayed.
    totp_last_step: Mapped[int | None] = mapped_column(nullable=True)

    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user")


class UserSession(RecordMixin, Base):
    """A browser session. The cookie holds a random token; only its hash is stored."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(500))

    user: Mapped[User] = relationship(back_populates="sessions")


class ApiToken(RecordMixin, Base):
    """A per-client API token (Siri, agent, scripts). Stored hashed, shown once."""

    __tablename__ = "api_tokens"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginAttempt(RecordMixin, Base):
    """Failed logins, used to throttle password guessing."""

    __tablename__ = "login_attempts"

    ip: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
