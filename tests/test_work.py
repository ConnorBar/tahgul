"""Workspaces, clients, projects, activities over the API, including rate resolution end to end."""

import pytest
from sqlalchemy import select

from tahgul.models import AuditLog

API = "/api/v1"


@pytest.fixture
def ws(logged_in):
    """The workspace created with the owner."""
    spaces = logged_in.get(f"{API}/workspaces").json()
    assert [s["name"] for s in spaces] == ["Consulting"]
    return spaces[0]


def token(client, *scopes, name="agent"):
    r = client.post(f"{API}/tokens", json={"name": name, "scopes": list(scopes)})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def bare(client):
    return client.__class__(client.app)


# --- Workspaces ---------------------------------------------------------------


def test_default_workspace_settings(ws):
    assert ws["currency"] == "USD"
    assert ws["tax_rate"] is None  # tax off by default
    assert ws["default_rate"] is None
    assert ws["payment_terms_days"] == 30
    assert ws["invoice_number_pattern"] == "{Y}-{seq:3}"
    assert ws["timezone"] == "America/Chicago"


def test_update_workspace(logged_in, ws):
    r = logged_in.patch(
        f"{API}/workspaces/{ws['id']}",
        json={
            "default_rate": "120",
            "tax_rate": "6.25",
            "currency": "eur",
            "invoice_number_pattern": "INV-{seq:4}",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default_rate"] == "120.00"
    assert body["tax_rate"] == "6.25"
    assert body["currency"] == "EUR"
    # Clearing tax with an explicit null.
    r = logged_in.patch(f"{API}/workspaces/{ws['id']}", json={"tax_rate": None})
    assert r.json()["tax_rate"] is None
    # Required fields can't be nulled.
    r = logged_in.patch(f"{API}/workspaces/{ws['id']}", json={"currency": None})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency", "DOLLARS"),
        ("timezone", "Mars/Olympus"),
        ("invoice_number_pattern", "INV"),
        ("default_rate", "-5"),
        ("tax_rate", "101"),
        ("default_rate", "12.345"),
    ],
)
def test_workspace_validation(logged_in, ws, field, value):
    r = logged_in.patch(f"{API}/workspaces/{ws['id']}", json={field: value})
    assert r.status_code == 422, r.text


def test_workspace_delete_refuses_when_not_empty(logged_in, ws):
    logged_in.post(f"{API}/clients", json={"name": "Acme"})
    assert logged_in.delete(f"{API}/workspaces/{ws['id']}").status_code == 409
    other = logged_in.post(f"{API}/workspaces", json={"name": "Personal"}).json()
    assert logged_in.delete(f"{API}/workspaces/{other['id']}").status_code == 204
    assert len(logged_in.get(f"{API}/workspaces").json()) == 1


def test_invoice_number_presets_and_preview(logged_in):
    presets = logged_in.get(f"{API}/invoice-numbers/presets").json()
    assert [p["pattern"] for p in presets][0] == "{Y}-{seq:3}"
    assert all(len(p["examples"]) == 3 for p in presets)
    ok = logged_in.get(f"{API}/invoice-numbers/preview", params={"pattern": "INV-{seq:4}"}).json()
    assert ok["valid"] and ok["examples"][0] == "INV-0001"
    bad = logged_in.get(f"{API}/invoice-numbers/preview", params={"pattern": "INV"}).json()
    assert not bad["valid"] and bad["errors"]


# --- Clients --------------------------------------------------------------------


def test_client_crud(logged_in, ws):
    r = logged_in.post(
        f"{API}/clients",
        json={
            "name": " Acme Farms ",
            "code": "acme",
            "contact_email": "ap@acme.example",
            "rate": "125",
            "payment_terms_days": 15,
        },
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["name"] == "Acme Farms" and c["code"] == "ACME" and c["workspace_id"] == ws["id"]
    assert c["rate"] == "125.00" and c["project_count"] == 0

    assert logged_in.patch(f"{API}/clients/{c['id']}", json={"notes": "Net 15"}).status_code == 200
    assert logged_in.get(f"{API}/clients", params={"q": "acme"}).json()[0]["notes"] == "Net 15"
    assert logged_in.delete(f"{API}/clients/{c['id']}").status_code == 204
    assert logged_in.get(f"{API}/clients/{c['id']}").status_code == 404
    assert logged_in.get(f"{API}/clients").json() == []


def test_client_names_unique_per_workspace(logged_in, ws):
    assert logged_in.post(f"{API}/clients", json={"name": "Acme"}).status_code == 201
    assert logged_in.post(f"{API}/clients", json={"name": "ACME"}).status_code == 409
    other = logged_in.post(f"{API}/workspaces", json={"name": "Personal"}).json()
    r = logged_in.post(f"{API}/clients", json={"name": "Acme", "workspace_id": other["id"]})
    assert r.status_code == 201
    # With two workspaces, which one must be said.
    r = logged_in.post(f"{API}/clients", json={"name": "Beta"})
    assert r.status_code == 422 and r.json()["detail"] == "workspace_required"


def test_client_with_projects_cannot_be_deleted(logged_in, ws):
    c = logged_in.post(f"{API}/clients", json={"name": "Acme"}).json()
    logged_in.post(f"{API}/projects", json={"name": "Audit", "client_id": c["id"]})
    assert logged_in.delete(f"{API}/clients/{c['id']}").status_code == 409
    assert logged_in.get(f"{API}/clients/{c['id']}").json()["project_count"] == 1


def test_client_validation(logged_in, ws):
    for body in [
        {"name": ""},
        {"name": "x", "contact_email": "nope"},
        {"name": "x", "code": "a b"},
        {"name": "x", "rate": "abc"},
    ]:
        assert logged_in.post(f"{API}/clients", json=body).status_code == 422, body


# --- Projects -------------------------------------------------------------------


def test_project_crud_and_filters(logged_in, ws):
    c = logged_in.post(f"{API}/clients", json={"name": "Acme"}).json()
    p = logged_in.post(
        f"{API}/projects",
        json={
            "name": "Network audit",
            "client_id": c["id"],
            "budget_hours": "40",
            "colour": "#AA3300",
        },
    ).json()
    assert p["client_name"] == "Acme" and p["status"] == "active" and p["colour"] == "#aa3300"
    idea = logged_in.post(f"{API}/projects", json={"name": "Farm sensor app", "status": "idea"})
    assert idea.status_code == 201 and idea.json()["client_id"] is None

    assert len(logged_in.get(f"{API}/projects").json()) == 2
    assert [
        x["name"] for x in logged_in.get(f"{API}/projects", params={"status": "idea"}).json()
    ] == ["Farm sensor app"]
    assert len(logged_in.get(f"{API}/projects", params={"client_id": c["id"]}).json()) == 1

    r = logged_in.patch(f"{API}/projects/{p['id']}", json={"status": "done", "client_id": None})
    assert r.json()["status"] == "done" and r.json()["client_id"] is None
    assert logged_in.patch(f"{API}/projects/{p['id']}", json={"status": "nope"}).status_code == 422
    assert logged_in.patch(f"{API}/projects/{p['id']}", json={"colour": "red"}).status_code == 422
    assert logged_in.delete(f"{API}/projects/{p['id']}").status_code == 204
    assert logged_in.get(f"{API}/projects/{p['id']}").status_code == 404


def test_project_client_must_be_in_same_workspace(logged_in, ws):
    other = logged_in.post(f"{API}/workspaces", json={"name": "Personal"}).json()
    c = logged_in.post(f"{API}/clients", json={"name": "Acme", "workspace_id": other["id"]}).json()
    r = logged_in.post(
        f"{API}/projects", json={"name": "X", "workspace_id": ws["id"], "client_id": c["id"]}
    )
    assert r.status_code == 422 and r.json()["detail"] == "client_in_other_workspace"
    # Given only a client, the project lands in the client's workspace.
    r = logged_in.post(f"{API}/projects", json={"name": "Y", "client_id": c["id"]})
    assert r.status_code == 201 and r.json()["workspace_id"] == other["id"]


# --- Effective rate through the API ------------------------------------------------


def rate(client, project_id, activity_id=None):
    params = {"activity_id": activity_id} if activity_id else {}
    r = client.get(f"{API}/projects/{project_id}/rate", params=params)
    assert r.status_code == 200, r.text
    b = r.json()
    return b["rate"], b["source"]


def test_effective_rate_every_override_case(logged_in, ws):
    """Walks the chain from nothing set to every level set, and back down."""
    c = logged_in.post(f"{API}/clients", json={"name": "Acme"}).json()
    p = logged_in.post(f"{API}/projects", json={"name": "Audit", "client_id": c["id"]}).json()
    a = logged_in.post(f"{API}/activities", json={"name": "Travel"}).json()
    pid, aid = p["id"], a["id"]

    assert rate(logged_in, pid) == (None, None)

    logged_in.patch(f"{API}/workspaces/{ws['id']}", json={"default_rate": "100"})
    assert rate(logged_in, pid) == ("100.00", "workspace")
    assert rate(logged_in, pid, aid) == ("100.00", "workspace")  # activity without a rate

    logged_in.patch(f"{API}/clients/{c['id']}", json={"rate": "125"})
    assert rate(logged_in, pid) == ("125.00", "client")

    logged_in.patch(f"{API}/projects/{pid}", json={"rate": "150"})
    assert rate(logged_in, pid) == ("150.00", "project")

    logged_in.patch(f"{API}/activities/{aid}", json={"rate": "60"})
    assert rate(logged_in, pid, aid) == ("60.00", "activity")
    assert rate(logged_in, pid) == ("150.00", "project")  # other work unaffected

    # Clearing levels falls back down the chain.
    logged_in.patch(f"{API}/projects/{pid}", json={"rate": None})
    assert rate(logged_in, pid) == ("125.00", "client")
    logged_in.patch(f"{API}/clients/{c['id']}", json={"rate": None})
    assert rate(logged_in, pid) == ("100.00", "workspace")

    # A project with no client skips straight from project to workspace.
    idea = logged_in.post(f"{API}/projects", json={"name": "Idea", "status": "idea"}).json()
    assert rate(logged_in, idea["id"]) == ("100.00", "workspace")

    # The list view carries the same answer.
    listed = {x["name"]: x for x in logged_in.get(f"{API}/projects").json()}
    assert (listed["Audit"]["effective_rate"], listed["Audit"]["rate_source"]) == (
        "100.00",
        "workspace",
    )

    # Levels are reported for the UI, most specific first.
    levels = logged_in.get(f"{API}/projects/{pid}/rate", params={"activity_id": aid}).json()
    assert [lv["level"] for lv in levels["levels"]] == [
        "activity",
        "project",
        "client",
        "workspace",
    ]
    assert levels["currency"] == "USD"


def test_activity_from_other_workspace_rejected(logged_in, ws):
    other = logged_in.post(f"{API}/workspaces", json={"name": "Personal"}).json()
    a = logged_in.post(f"{API}/activities", json={"name": "X", "workspace_id": other["id"]}).json()
    p = logged_in.post(f"{API}/projects", json={"name": "P", "workspace_id": ws["id"]}).json()
    r = logged_in.get(f"{API}/projects/{p['id']}/rate", params={"activity_id": a["id"]})
    assert r.status_code == 422


# --- Agent limits -------------------------------------------------------------------


def test_write_token_can_create_but_not_set_rates_or_delete(logged_in, client, ws):
    h = token(logged_in, "read", "write")
    agent = bare(client)
    r = agent.post(f"{API}/clients", json={"name": "Acme"}, headers=h)
    assert r.status_code == 201
    cid = r.json()["id"]
    assert agent.patch(f"{API}/clients/{cid}", json={"notes": "hi"}, headers=h).status_code == 200

    for method, url, body in [
        ("post", f"{API}/clients", {"name": "Beta", "rate": "90"}),
        ("patch", f"{API}/clients/{cid}", {"rate": "90"}),
        ("patch", f"{API}/clients/{cid}", {"rate": None}),
        ("patch", f"{API}/workspaces/{ws['id']}", {"default_rate": "90"}),
        ("post", f"{API}/projects", {"name": "P", "rate": "90"}),
        ("post", f"{API}/activities", {"name": "A", "rate": "90"}),
        ("delete", f"{API}/clients/{cid}", None),
    ]:
        kwargs = {"headers": h} | ({"json": body} if body is not None else {})
        r = getattr(agent, method)(url, **kwargs)
        assert r.status_code == 403, (method, url, body)
        assert r.json()["detail"].startswith("scope_required:admin")


def test_admin_token_can_set_rates_and_delete(logged_in, client, ws):
    h = token(logged_in, "write", "admin", name="books")
    agent = bare(client)
    r = agent.post(f"{API}/clients", json={"name": "Acme", "rate": "90"}, headers=h)
    assert r.status_code == 201
    assert agent.delete(f"{API}/clients/{r.json()['id']}", headers=h).status_code == 204


def test_read_token_cannot_write(logged_in, client, ws):
    h = token(logged_in, "read")
    agent = bare(client)
    assert agent.get(f"{API}/clients", headers=h).status_code == 200
    assert agent.post(f"{API}/clients", json={"name": "A"}, headers=h).status_code == 403


def test_writes_are_audited_with_token_name(logged_in, client, db, ws):
    h = token(logged_in, "write", name="Siri")
    c = bare(client).post(f"{API}/clients", json={"name": "Acme"}, headers=h).json()
    logged_in.patch(f"{API}/clients/{c['id']}", json={"name": "Acme Farms"})
    rows = db.scalars(select(AuditLog).where(AuditLog.entity == "client")).all()
    assert [(r.action, r.actor_type, r.actor_name) for r in rows] == [
        ("create", "token", "Siri"),
        ("update", "owner", "owner"),
    ]
    assert rows[1].changes == {"name": ["Acme", "Acme Farms"]}
