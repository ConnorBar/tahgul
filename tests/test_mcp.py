"""tahgul-mcp tools, run against the real app through its test client."""

import json

import anyio
import pytest

pytest.importorskip("mcp")

from tahgul_mcp.server import TahgulAPI, build_server  # noqa: E402

API = "/api/v1"


@pytest.fixture
def mcp(logged_in, client):
    c = logged_in
    ws = c.get(f"{API}/workspaces").json()[0]
    c.patch(f"{API}/workspaces/{ws['id']}", json={"default_rate": "100"})
    acme = c.post(
        f"{API}/clients", json={"name": "Acme Farms", "code": "ACME", "rate": "125"}
    ).json()
    prairie = c.post(f"{API}/clients", json={"name": "Prairie Co-op"}).json()
    c.post(f"{API}/projects", json={"name": "Network audit", "client_id": acme["id"]})
    c.post(f"{API}/projects", json={"name": "Yield dashboard", "client_id": prairie["id"]})
    c.post(f"{API}/projects", json={"name": "Grain bin sensors", "client_id": prairie["id"]})
    token = c.post(
        f"{API}/tokens", json={"name": "local agent", "scopes": ["read", "write"]}
    ).json()["token"]
    http = client.__class__(client.app)
    http.headers["Authorization"] = f"Bearer {token}"
    return build_server(TahgulAPI(http))


def call(server, name, **args):
    async def run():
        return await server.call_tool(name, args)

    result = anyio.run(run)
    if getattr(result, "structuredContent", None):
        data = result.structuredContent
        return data.get("result", data)
    return json.loads(result.content[0].text)


def test_tools_are_listed(mcp):
    names = {t.name for t in anyio.run(mcp.list_tools)}
    assert names == {
        "quick",
        "create_task",
        "list_tasks",
        "update_task",
        "start_timer",
        "stop_timer",
        "timer_status",
        "log_time",
        "summary",
        "capture_idea",
        "unbilled_work",
        "draft_invoice",
        "create_event",
        "list_events",
        "events_to_log",
        "log_event_as_time",
    }
    tools = {t.name: t for t in anyio.run(mcp.list_tools)}
    assert tools["list_tasks"].annotations.read_only_hint is True
    assert tools["create_task"].annotations.destructive_hint is False


def test_task_round_trip(mcp):
    t = call(
        mcp,
        "create_task",
        title="Check VPN",
        project="acme",
        priority="high",
        due_date="2026-10-30",
    )
    assert t["ref"] == "#1" and t["project_name"] == "Network audit"
    listed = call(mcp, "list_tasks", project="Network audit")
    assert listed["count"] == 1 and listed["tasks"][0]["title"] == "Check VPN"
    done = call(mcp, "update_task", task="#1", done=True, comment="Fixed the MTU")
    assert done["done"] is True and done["status"] == "Done"
    assert call(mcp, "list_tasks")["count"] == 0


def test_ambiguous_project_asks(mcp):
    r = call(mcp, "create_task", title="x", project="Prairie Co-op")
    assert r["needs_confirmation"] is True
    assert set(r["candidates"]) == {"Yield dashboard", "Grain bin sensors"}


def test_timer_and_log_and_summary(mcp):
    r = call(mcp, "start_timer", project="Network audit", notes="router config")
    assert r["running"]["project_name"] == "Network audit"
    assert call(mcp, "timer_status")["running"]["notes"] == "router config"
    stopped = call(mcp, "stop_timer")
    assert stopped["running"] is False
    logged = call(
        mcp, "log_time", project="Yield dashboard", minutes=90, day="2026-10-13", start_time="10:00"
    )
    assert logged["hours"] == "1.50" and logged["amount"] == "150.00"
    s = call(mcp, "summary", date_from="2026-10-12", date_to="2026-10-18")
    assert s["totals"]["hours"] == "1.50" and s["by_client"][0]["name"] == "Prairie Co-op"


def test_quick_and_idea(mcp):
    r = call(mcp, "quick", text="Call Laura Thursday 2pm #acme")
    assert r["action"] == "task_created" and r["speech"].startswith("Added")
    r = call(mcp, "quick", text="Buy paper", project="Yield dashboard")
    assert r["task"]["project_name"] == "Yield dashboard"
    idea = call(mcp, "capture_idea", title="Drone surveys")
    assert idea["status"] == "idea"


def test_agent_cannot_delete_or_change_rates(mcp):
    # No delete or rate tools exist; and the token itself lacks admin.
    names = {t.name for t in anyio.run(mcp.list_tools)}
    assert not any("delete" in n or "rate" in n for n in names)


def test_invoice_tools_need_invoice_scope(mcp, logged_in, client):
    c = logged_in
    p = c.get(f"{API}/projects", params={"q": "Network audit"}).json()[0]
    c.post(
        f"{API}/time-entries",
        json={"project_id": p["id"], "start_at": "2026-10-12T09:00:00", "duration_minutes": 120},
    )
    assert call(mcp, "draft_invoice", client="acme")["status"] == 403
    token = c.post(f"{API}/tokens", json={"name": "billing", "scopes": ["read", "invoice"]}).json()[
        "token"
    ]
    http = client.__class__(client.app)
    http.headers["Authorization"] = f"Bearer {token}"
    billing = build_server(TahgulAPI(http))
    u = call(billing, "unbilled_work")
    assert u["clients"][0]["client_name"] == "Acme Farms" and u["clients"][0]["hours"] == "2.00"
    d = call(billing, "draft_invoice", client="acme", period_start="2026-10-01")
    assert d["total"] == "250.00" and d["speech"].startswith("Drafted")
