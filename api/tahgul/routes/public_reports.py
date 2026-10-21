"""The client-facing side of report links: /r/<token>. No login, read-only.

This router is all that tahgul.public serves, so exposing that app (and only it) with
Tailscale Funnel can never expose anything else.
"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from tahgul import client_report as cr
from tahgul.db import get_db
from tahgul.timeutil import utcnow

router = APIRouter(include_in_schema=False)

HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
    "img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
}

GONE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Link not available</title><style>body{font:16px/1.5 system-ui,sans-serif;display:grid;
place-items:center;min-height:90vh;color:#444;text-align:center;padding:1rem}</style></head>
<body><div><h1 style="font-size:1.3rem">This report link isn't available</h1>
<p>It may have expired or been turned off. Ask for a new one.</p></div></body></html>"""


def _period(period: str) -> str:
    return period if period in cr.PERIODS else "this-month"


@router.get("/r/{token}", response_class=HTMLResponse)
def view(token: str, period: str = Query("this-month"), db: Session = Depends(get_db)):
    link = cr.find(db, token)
    if link is None:
        return HTMLResponse(GONE, status_code=404, headers=HEADERS)
    ctx = cr.build(db, link, _period(period))
    html = cr.render({**ctx, "link_base": token})
    link.view_count += 1
    link.last_viewed_at = utcnow()
    db.commit()
    return HTMLResponse(html, headers=HEADERS)


@router.get("/r/{token}/pdf")
def pdf(token: str, period: str = Query("this-month"), db: Session = Depends(get_db)):
    link = cr.find(db, token)
    if link is None:
        return HTMLResponse(GONE, status_code=404, headers=HEADERS)
    ctx = cr.build(db, link, _period(period))
    name = "".join(c if c.isalnum() else "-" for c in link.client.name).strip("-") or "report"
    return Response(
        cr.render_pdf({**ctx, "link_base": token}),
        media_type="application/pdf",
        headers={**HEADERS, "Content-Disposition": f'inline; filename="{name}-{ctx["last"]}.pdf"'},
    )
