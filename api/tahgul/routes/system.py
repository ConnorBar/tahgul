from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from tahgul import __version__
from tahgul.config import get_settings
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.principal import Principal

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """Liveness and database check. No auth, so Docker and Tailscale can probe it."""
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - only when the DB is down
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="database_unavailable"
        ) from exc
    return {"status": "ok", "version": __version__}


@router.get("/features")
def features(_: Principal = Depends(require_scope("read"))):
    """Optional services the server has, so clients can show or hide their buttons."""
    s = get_settings()
    return {"transcription": bool(s.whisper_url), "calendar_secret_key": bool(s.secret_key)}
