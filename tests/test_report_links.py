"""Client-facing read-only report links (later milestone)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

API = "/api/v1"


@pytest.fixture
def acme(logged_in):
    """Acme with a budgeted project, a scope, tasks and two weeks of time; Beta on the side."""
    c = logged_in
    ws = c.get(f"{API}/workspaces").json()[0]
    c.patch(
        f"{API}/workspaces/{ws['id']}",
        json={"business_name": "Barnsley Tech", "default_rate": "100"},
    )
    acme = c.post(f"{API}/clients", json={"name": "Acme Farms", "rate": "125"}).json()
    beta = c.post(f"{API}/clients", json={"name": "Beta Secret Co"}).json()
    audit = c.post(
        f"{API}/projects",
        json={"name": "Network audit", "client_id": acme["id"], "budget_hours": "10"},
    ).json()
    other = c.post(f"{API}/projects", json={"name": "Beta thing", "client_id": beta["id"]}).json()
    scope = c.post(
        f"{API}/projects/{audit['id']}/scopes", json={"title": "Phase 1", "budget_hours": "8"}
    ).json()
    today = datetime.now(UTC).date()
    for days, mins, notes in ((1, 120, "Switch inventory"), (8, 180, "Firewall review")):
        d = today - timedelta(days=days)
        c.post(
            f"{API}/time-entries",
            json={
                "project_id": audit["id"],
                "scope_id": scope["id"],
                "start_at": f"{d}T15:00:00Z",
                "duration_minutes": mins,
                "notes": notes,
            },
        )
    c.post(
        f"{API}/time-entries",
        json={
            "project_id": other["id"],
            "start_at": f"{today - timedelta(days=1)}T15:00:00Z",
            "duration_minutes": 60,
            "notes": "Beta internal",
        },
    )
    done = c.post(
        f"{API}/tasks",
        json={
            "project_id": audit["id"],
            "title": "Map the closet",
            "description": "INTERNAL: they owe us",
        },
    ).json()
    c.patch(f"{API}/tasks/{done['id']}", json={"done": True})
    c.post(
        f"{API}/tasks",
        json={
            "project_id": audit["id"],
            "title": "Write up findings",
            "due_date": str(today + timedelta(days=3)),
        },
    )
    return {"client": acme, "project": audit, "beta": beta}


def make(c, client_id, **body):
    r = c.post(f"{API}/clients/{client_id}/report-links", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_report_shows_progress_not_internals(logged_in, client, acme):
    c = logged_in
    link = make(c, acme["client"]["id"])
    assert link["path"].startswith("/r/") and link["active"] and link["expires_at"]
    anon = client.__class__(client.app)  # no session, no token
    r = anon.get(link["path"], params={"period": "90d"})
    assert r.status_code == 200
    html = r.text
    assert "Acme Farms" in html and "Barnsley Tech" in html and "Network audit" in html
    assert "Phase 1" in html and "5 of 8 h" in html  # scope burn
    assert "Map the closet" in html and "Write up findings" in html
    # Not shown by default: money, notes, internal descriptions, other clients.
    for secret in ("$", "Switch inventory", "INTERNAL", "Beta Secret", "Beta internal", "125"):
        assert secret not in html, secret
    assert r.headers["x-robots-tag"].startswith("noindex")
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert c.get(f"{API}/clients/{acme['client']['id']}/report-links").json()[0]["view_count"] == 1


def test_amounts_and_notes_when_chosen(logged_in, client, acme):
    c = logged_in
    link = make(c, acme["client"]["id"], show_amounts=True, show_notes=True)
    html = client.__class__(client.app).get(link["path"], params={"period": "90d"}).text
    assert "$625" in html and "Switch inventory" in html and "Firewall review" in html


def test_pdf(logged_in, client, acme):
    link = make(logged_in, acme["client"]["id"])
    r = client.__class__(client.app).get(link["path"] + "/pdf", params={"period": "all"})
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_project_only_link(logged_in, client, acme):
    c = logged_in
    p2 = c.post(
        f"{API}/projects", json={"name": "Yield dashboard", "client_id": acme["client"]["id"]}
    ).json()
    link = make(c, acme["client"]["id"], project_id=acme["project"]["id"])
    html = client.__class__(client.app).get(link["path"]).text
    assert "Network audit" in html and "Yield dashboard" not in html
    r = c.post(f"{API}/clients/{acme['beta']['id']}/report-links", json={"project_id": p2["id"]})
    assert r.status_code == 422


def test_revoke_expire_and_bad_tokens(logged_in, client, acme):
    c = logged_in
    anon = client.__class__(client.app)
    link = make(c, acme["client"]["id"])
    assert c.delete(f"{API}/report-links/{link['id']}").status_code == 204
    assert anon.get(link["path"]).status_code == 404
    assert "isn't available" in anon.get(link["path"]).text
    assert anon.get("/r/not-a-real-token").status_code == 404

    old = make(c, acme["client"]["id"])
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    c.patch(f"{API}/report-links/{old['id']}", json={"expires_at": past})
    assert anon.get(old["path"]).status_code == 404
    listed = {
        x["id"]: x["active"]
        for x in c.get(f"{API}/clients/{acme['client']['id']}/report-links").json()
    }
    assert listed == {link["id"]: False, old["id"]: False}


def test_admin_only(logged_in, client, acme):
    c = logged_in
    tok = c.post(f"{API}/tokens", json={"name": "agent", "scopes": ["read", "write"]}).json()
    agent = client.__class__(client.app)
    agent.headers["Authorization"] = f"Bearer {tok['token']}"
    r = agent.post(f"{API}/clients/{acme['client']['id']}/report-links", json={})
    assert r.status_code == 403


def test_public_url_setting(logged_in, acme, monkeypatch):
    from tahgul.config import get_settings

    monkeypatch.setattr(
        get_settings(), "public_report_url", "https://tahgul.tail1234.ts.net:10000/"
    )
    link = make(logged_in, acme["client"]["id"])
    assert link["public_url"] == "https://tahgul.tail1234.ts.net:10000" + link["path"]


def test_public_app_serves_reports_and_nothing_else(logged_in, acme):
    from tahgul.public import create_app

    link = make(logged_in, acme["client"]["id"])
    pub = TestClient(create_app())
    assert pub.get(link["path"]).status_code == 200
    assert pub.get("/healthz").text == "ok"
    for path in (
        "/api/v1/auth/me",
        "/api/v1/clients",
        "/api/docs",
        "/openapi.json",
        "/",
        "/settings",
        "/api/v1/health",
    ):
        assert pub.get(path).status_code == 404, path
    assert pub.post("/api/v1/auth/login", json={}).status_code in (404, 405)
