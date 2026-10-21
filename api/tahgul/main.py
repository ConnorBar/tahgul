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
from tahgul.routes import (
    activities,
    auth,
    clients,
    projects,
    reports,
    system,
    time,
    timesheet,
    tokens,
    workspaces,
)

log = logging.getLogger("tahgul")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.owner_email and s.owner_password:
        try:
            with get_sessionmaker()() as db:
                bootstrap_owner_from_env(db, s.owner_email, s.owner_password)
        except SQLAlchemyError:
            log.exception("Owner bootstrap failed; have migrations been run?")
    yield


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
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
