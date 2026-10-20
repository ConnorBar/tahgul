from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from ledger import __version__
from ledger.db import get_db

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
