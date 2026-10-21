"""The public face of Tahgul: client report links and nothing else.

    uvicorn tahgul.public:app --port 8001

Run as its own container (compose profile "public-reports") and expose it with
Tailscale Funnel; the main app stays on the tailnet only. It has no login, no API and
no static files, so there is nothing else to reach through it.
"""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from tahgul.routes import public_reports


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(public_reports.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return PlainTextResponse("ok")

    return app


app = create_app()
