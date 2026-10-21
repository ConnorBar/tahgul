import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from tahgul import __version__
from tahgul.bootstrap import bootstrap_owner_from_env
from tahgul.config import get_settings
from tahgul.db import get_sessionmaker
from tahgul.idempotency import IdempotencyMiddleware
from tahgul.routes import (
    activities,
    auth,
    calendar,
    clients,
    ideas,
    invoices,
    projects,
    quick,
    reports,
    scopes,
    system,
    tasks,
    time,
    timesheet,
    tokens,
    voice_notes,
    workspaces,
)

log = logging.getLogger("tahgul")


def _scheduled_sync() -> None:
    """Pull every calendar and retry pending pushes (SPEC: pull every 2–5 minutes)."""
    from tahgul import calsync

    try:
        with get_sessionmaker()() as db:
            try:
                calsync.ensure_stack_account(db)
            except Exception:
                db.rollback()
                log.warning("Radicale isn't reachable yet; will try again next run")
            calsync.sync_all(db)
    except Exception:  # keep the scheduler alive whatever happens
        log.exception("Scheduled calendar sync failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.owner_email and s.owner_password:
        try:
            with get_sessionmaker()() as db:
                bootstrap_owner_from_env(db, s.owner_email, s.owner_password)
        except SQLAlchemyError:
            log.exception("Owner bootstrap failed; have migrations been run?")
    scheduler = None
    if s.scheduler_enabled:
        from datetime import UTC, datetime, timedelta

        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            _scheduled_sync,
            "interval",
            minutes=max(1, s.sync_minutes),
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(UTC) + timedelta(seconds=20),
        )
        if s.whisper_url:
            from tahgul import transcribe

            scheduler.add_job(
                transcribe.run_pending,
                "interval",
                minutes=1,
                max_instances=1,
                coalesce=True,
                next_run_time=datetime.now(UTC) + timedelta(seconds=30),
            )
        scheduler.start()
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Tahgul API",
        version=__version__,
        description=(
            "One API for the web app, Siri Shortcuts, scripts and the local agent. "
            "Authenticate with `Authorization: Bearer <token>`; create tokens under Settings."
        ),
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(IdempotencyMiddleware)
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(tokens.router)
    app.include_router(workspaces.router)
    app.include_router(clients.router)
    app.include_router(projects.router)
    app.include_router(activities.router)
    app.include_router(time.router)
    app.include_router(timesheet.router)
    app.include_router(reports.router)
    app.include_router(tasks.router)
    app.include_router(ideas.router)
    app.include_router(quick.router)
    app.include_router(calendar.router)
    app.include_router(scopes.router)
    app.include_router(invoices.router)
    app.include_router(voice_notes.router)
    _mount_web(app)
    return app


def _mount_web(app: FastAPI) -> None:
    """Serve the built React app, falling back to index.html for client-side routes."""
    static = get_settings().static_dir
    index = static / "index.html"
    if not index.is_file():
        log.info("No web build at %s; serving the API only", static)
        return
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, detail="not_found")
        candidate = (static / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(static.resolve()):
            # The service worker and manifest must update promptly.
            fresh = path in ("sw.js", "manifest.webmanifest", "offline.html")
            media = "application/manifest+json" if path.endswith(".webmanifest") else None
            return FileResponse(
                candidate,
                media_type=media,
                headers={"Cache-Control": "no-cache"} if fresh else None,
            )
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
