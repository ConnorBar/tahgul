import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tahgul import audit
from tahgul.models import User, Workspace
from tahgul.security import MIN_PASSWORD_LENGTH, hash_password

log = logging.getLogger("tahgul")


class OwnerExists(Exception):
    pass


def create_owner(db: Session, email: str, password: str) -> User:
    """Create the single owner account. Refuses if one already exists."""
    if db.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None))):
        raise OwnerExists("an owner account already exists")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    user = User(email=email.strip().lower(), password_hash=hash_password(password))
    db.add(user)
    db.flush()
    audit.record(db, None, "create", "user", user.id, {"email": user.email})
    # Start with one workspace so clients and projects have somewhere to live.
    if not db.scalar(select(func.count()).select_from(Workspace)):
        ws = Workspace(name="Consulting")
        db.add(ws)
        db.flush()
        audit.record(db, None, "create", "workspace", ws.id, {"name": ws.name})
    db.commit()
    return user


def bootstrap_owner_from_env(db: Session, email: str | None, password: str | None) -> None:
    if not email or not password:
        return
    try:
        create_owner(db, email, password)
        log.info("Created owner account %s from environment", email)
    except OwnerExists:
        pass
