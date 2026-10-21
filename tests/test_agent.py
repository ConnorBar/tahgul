"""Agent API: idempotency keys, name lookups, /quick and ideas (milestone 5)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

API = "/api/v1"
CHI = ZoneInfo("America/Chicago")


@pytest.fixture
def agent(logged_in, client):
    """A Siri-style token client plus some data to talk about."""
    c = logged_in
    ws = c.get(f"{API}/workspaces").json()[0]
    c.patch(f"{API}/workspaces/{ws['id']}", json={"default_rate": "100"})
    acme = c.post(
        f"{API}/clients", json={"name": "Acme Farms", "code": "ACME", "rate": "125"}
    ).json()
    prairie = c.post(f"{API}/clients", json={"name": "Prairie Co-op"}).json()
    audit = c.post(
        f"{API}/projects", json={"name": "Network audit", "client_id": acme["id"]}
    ).json()
    c.post(f"{API}/projects", json={"name": "Yield dashboard", "client_id": prairie["id"]})
    c.post(f"{API}/projects", json={"name": "Grain bin sensors", "client_id": prairie["id"]})
    c.post(f"{API}/activities", json={"name": "Travel", "rate": "60"})
    token = c.post(f"{API}/tokens", json={"name": "Siri", "scopes": ["read", "write"]}).json()
    bot = client.__class__(client.app)
    bot.headers["Authorization"] = f"Bearer {token['token']}"
    return {"bot": bot, "owner": c, "audit": audit}


def quick(bot, text, **kw):
    r = bot.post(f"{API}/quick", json={"text": text, **kw})
    assert r.status_code == 200, r.text
    return r.json()


# --- Idempotency -------------------------------------------------------------------------


def test_retried_voice_command_creates_one_task(agent):
    bot = agent["bot"]
    h = {"Idempotency-Key": "siri-2026-10-21T09:00:00-123"}
    body = {"project": "Network audit", "title": "Check VPN"}
    first = bot.post(f"{API}/tasks", json=body, headers=h)
    again = bot.post(f"{API}/tasks", json=body, headers=h)
    assert first.status_code == again.status_code == 201
    assert again.headers.get("idempotent-replayed") == "true"
    assert again.json() == first.json()
    assert len(bot.get(f"{API}/tasks").json()) == 1


def test_same_key_different_request_is_refused(agent):
    bot = agent["bot"]
    h = {"Idempotency-Key": "k1"}
    bot.post(f"{API}/tasks", json={"project": "Network audit", "title": "A"}, headers=h)
    r = bot.post(f"{API}/tasks", json={"project": "Network audit", "title": "B"}, headers=h)
    assert r.status_code == 422 and r.json()["detail"] == "idempotency_key_reused"


def test_keys_are_per_caller(agent):
    """The owner and the agent can use the same key string without colliding."""
    h = {"Idempotency-Key": "shared"}
    body = {"project": "Network audit", "title": "Same"}
    agent["bot"].post(f"{API}/tasks", json=body, headers=h)
    r = agent["owner"].post(f"{API}/tasks", json=body, headers=h)
    assert r.status_code == 201 and "idempotent-replayed" not in r.headers
    assert len(agent["owner"].get(f"{API}/tasks").json()) == 2


def test_errors_are_replayed_too_but_not_server_errors(agent):
    bot = agent["bot"]
    h = {"Idempotency-Key": "bad"}
    r1 = bot.post(f"{API}/tasks", json={"project": "Nope nothing", "title": "x"}, headers=h)
    r2 = bot.post(f"{API}/tasks", json={"project": "Nope nothing", "title": "x"}, headers=h)
    assert r1.status_code == r2.status_code == 404
    assert r2.headers.get("idempotent-replayed") == "true"


def test_timer_start_with_key_starts_once(agent):
    bot = agent["bot"]
    h = {"Idempotency-Key": "start-1"}
    a = bot.post(f"{API}/timer/start", json={"project": "Network audit"}, headers=h).json()
    b = bot.post(f"{API}/timer/start", json={"project": "Network audit"}, headers=h).json()
    assert a["running"]["id"] == b["running"]["id"] and b["stopped"] is None
    assert len(bot.get(f"{API}/time-entries").json()) == 1


# --- Name lookups ----------------------------------------------------------------------------


def test_project_by_name_and_client_name(agent):
    bot = agent["bot"]
    assert (
        bot.post(f"{API}/tasks", json={"project": "network AUDIT", "title": "a"}).status_code == 201
    )
    # A client with one open project stands for it.
    t = bot.post(f"{API}/tasks", json={"project": "acme", "title": "b"}).json()
    assert t["project_name"] == "Network audit"


def test_ambiguous_names_need_confirmation(agent):
    bot = agent["bot"]
    r = bot.post(f"{API}/tasks", json={"project": "Prairie Co-op", "title": "x"})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "needs_confirmation" and d["field"] == "project"
    assert {c["name"] for c in d["candidates"]} == {"Yield dashboard", "Grain bin sensors"}
    # A near miss is never guessed, even with one candidate.
    r = bot.post(f"{API}/tasks", json={"project": "netwrk audit", "title": "x"})
    assert r.status_code == 409 and r.json()["detail"]["candidates"][0]["name"] == "Network audit"
    r = bot.post(f"{API}/tasks", json={"project": "zzz", "title": "x"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "project_not_found"


def test_names_in_timer_entries_and_reports(agent):
    bot = agent["bot"]
    e = bot.post(
        f"{API}/time-entries",
        json={
            "project": "audit",
            "activity": "travel",
            "start_at": "2026-10-12T08:00:00",
            "duration_minutes": 60,
        },
    )
    assert e.status_code == 409  # "audit" is only a partial match
    e = bot.post(
        f"{API}/time-entries",
        json={
            "project": "Network audit",
            "activity": "travel",
            "start_at": "2026-10-12T08:00:00",
            "duration_minutes": 60,
        },
    ).json()
    assert (e["project_name"], e["activity_name"], e["amount"]) == (
        "Network audit",
        "Travel",
        "60.00",
    )
    s = bot.get(
        f"{API}/reports/summary",
        params={"client": "ACME", "from": "2026-10-12", "to": "2026-10-18"},
    ).json()
    assert s["totals"]["hours"] == "1.00"
    tasks = bot.get(f"{API}/tasks", params={"project": "Network audit"})
    assert tasks.status_code == 200


# --- /quick ---------------------------------------------------------------------------------


def test_quick_spec_example_task(agent):
    r = quick(agent["bot"], "Call Laura Thursday 2pm #acme")
    assert r["action"] == "task_created"
    assert r["task"]["title"] == "Call Laura" and r["task"]["project_name"] == "Network audit"
    assert r["task"]["due_time"] == "14:00:00"
    assert r["speech"].startswith("Added “Call Laura” to Network audit, due ")
    assert r["speech"].endswith("at 2 PM.")


def test_quick_new_task_needs_a_project(agent):
    r = quick(agent["bot"], "Buy printer paper")
    assert r["action"] == "needs_confirmation" and "Which project" in r["speech"]
    r = quick(agent["bot"], "Buy printer paper", project_id=agent["audit"]["id"])
    assert r["action"] == "task_created"


def test_quick_timer_flow(agent):
    bot = agent["bot"]
    r = quick(bot, "start timer for acme")
    assert r["action"] == "timer_started" and r["speech"] == "Timer started for Network audit."
    r = quick(bot, "what's running")
    assert r["action"] == "status" and "Network audit" in r["speech"]
    r = quick(bot, "start working on yield dashboard")
    assert r["speech"] == "Timer started for Yield dashboard. Stopped Network audit first."
    r = quick(bot, "Stop the timer")
    assert r["action"] == "timer_stopped" and r["speech"].endswith("on Yield dashboard.")
    assert quick(bot, "stop")["action"] == "nothing"


def test_quick_timer_ambiguous_asks(agent):
    r = quick(agent["bot"], "start timer for Prairie Co-op")
    assert r["action"] == "needs_confirmation" and r["needs_confirmation"]
    assert "Which project" in r["speech"] and len(r["candidates"]) == 2


def test_quick_log_time_spec_example(agent):
    bot = agent["bot"]
    r = quick(bot, "2 hours on Acme yesterday afternoon")
    assert r["action"] == "time_logged", r
    e = r["entry"]
    start = datetime.fromisoformat(e["start_at"]).astimezone(CHI)
    yesterday = datetime.now(CHI).date() - timedelta(days=1)
    assert (start.date(), start.hour) == (yesterday, 13)
    assert e["duration_seconds"] == 7200 and e["amount"] == "250.00"
    assert r["speech"] == "Logged 2 hours on Network audit yesterday. That's $250."


@pytest.mark.parametrize(
    "text,minutes",
    [
        ("log 45m on network audit", 45),
        ("log an hour and 15 minutes on acme", 75),
        ("spent half an hour on acme", 30),
        ("log 1.5h on acme", 90),
    ],
)
def test_quick_log_durations(agent, text, minutes):
    r = quick(agent["bot"], text)
    assert r["action"] == "time_logged" and r["entry"]["duration_seconds"] == minutes * 60


def test_quick_idea_and_summary(agent):
    bot = agent["bot"]
    r = quick(bot, "idea: farm data newsletter")
    assert r["action"] == "idea_captured" and r["idea"]["status"] == "idea"
    assert r["speech"] == "Saved the idea “farm data newsletter”."
    again = quick(bot, "idea: farm data newsletter")
    assert again["idea"]["name"] == "farm data newsletter (2)"
    quick(bot, "log 2h on acme")
    r = quick(bot, "how many hours this week")
    assert r["action"] == "summary" and r["speech"].startswith("This week: 2 hours, $250 billable")


def test_quick_dry_run_changes_nothing(agent):
    bot = agent["bot"]
    for text in ["Call Laura tomorrow #acme", "start timer for acme", "log 1h on acme", "idea: x"]:
        assert quick(bot, text, dry_run=True)["speech"].startswith("I'd ")
    assert bot.get(f"{API}/tasks").json() == []
    assert bot.get(f"{API}/time-entries").json() == []
    assert bot.get(f"{API}/ideas").json() == []


def test_quick_writes_are_audited_as_the_token(agent, db):
    from sqlalchemy import select

    from tahgul.models import AuditLog

    quick(agent["bot"], "Call Laura Thursday 2pm #acme")
    row = db.scalars(select(AuditLog).where(AuditLog.entity == "task")).first()
    assert (row.actor_type, row.actor_name) == ("token", "Siri")


# --- Ideas -----------------------------------------------------------------------------------


def test_promote_idea(agent):
    c = agent["owner"]
    idea = c.post(f"{API}/ideas", json={"title": "Drone surveys"}).json()
    assert [i["name"] for i in c.get(f"{API}/ideas").json()] == ["Drone surveys"]
    clients = c.get(f"{API}/clients").json()
    p = c.post(f"{API}/ideas/{idea['id']}/promote", json={"client_id": clients[0]["id"]}).json()
    assert p["status"] == "active" and p["client_id"] == clients[0]["id"]
    assert c.get(f"{API}/ideas").json() == []
    assert c.post(f"{API}/ideas/{idea['id']}/promote", json={}).status_code == 409
