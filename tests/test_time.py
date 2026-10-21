"""Timer, time entries, timesheet and reports (milestone 3)."""

from datetime import UTC, datetime, timedelta

import pytest

from tahgul.timekeeping import round_seconds

API = "/api/v1"


@pytest.fixture
def setup(logged_in):
    """Workspace at $100/h; Acme at $125/h with two projects; Beta at the default."""
    c = logged_in
    ws = c.get(f"{API}/workspaces").json()[0]
    c.patch(f"{API}/workspaces/{ws['id']}", json={"default_rate": "100"})
    acme = c.post(f"{API}/clients", json={"name": "Acme", "rate": "125"}).json()
    beta = c.post(f"{API}/clients", json={"name": "Beta"}).json()
    p = {
        "audit": c.post(
            f"{API}/projects", json={"name": "Audit", "client_id": acme["id"], "budget_hours": "20"}
        ).json(),
        "sensors": c.post(
            f"{API}/projects", json={"name": "Sensors", "client_id": acme["id"], "rate": "150"}
        ).json(),
        "dash": c.post(
            f"{API}/projects", json={"name": "Dashboard", "client_id": beta["id"]}
        ).json(),
        "idea": c.post(f"{API}/projects", json={"name": "Newsletter", "status": "idea"}).json(),
    }
    a = {
        "travel": c.post(f"{API}/activities", json={"name": "Travel", "rate": "60"}).json(),
        "admin": c.post(f"{API}/activities", json={"name": "Admin", "billable": False}).json(),
    }
    return {"ws": ws, "acme": acme, "beta": beta, "p": p, "a": a}


def log(c, project, start, minutes, **extra):
    body = {"start_at": start, "duration_minutes": minutes, **extra}
    if project:
        body["project_id"] = project["id"]
    r = c.post(f"{API}/time-entries", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def token(c, *scopes, name="agent"):
    r = c.post(f"{API}/tokens", json={"name": name, "scopes": list(scopes)})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def ago(**kw):
    return (datetime.now(UTC) - timedelta(**kw)).isoformat()


# --- Timer ---------------------------------------------------------------------------


def test_timer_start_stop(logged_in, setup):
    c = logged_in
    assert c.get(f"{API}/timer").json()["running"] is None
    r = c.post(
        f"{API}/timer/start",
        json={
            "project_id": setup["p"]["audit"]["id"],
            "notes": "kickoff",
            "start_at": ago(minutes=90),
        },
    )
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["running"]["project_name"] == "Audit" and t["running"]["running"] is True
    assert t["running"]["source"] == "timer"
    assert 5390 <= t["elapsed_seconds"] <= 5410 and t["long_running"] is False

    e = c.post(f"{API}/timer/stop").json()
    assert e["running"] is False and e["rate"] == "125.00" and e["rate_source"] == "client"
    assert e["amount"] == "187.50"  # 1.5 h at 125
    assert c.get(f"{API}/timer").json()["running"] is None
    assert c.post(f"{API}/timer/stop").status_code == 404


def test_starting_a_timer_stops_the_running_one(logged_in, setup):
    c = logged_in
    c.post(
        f"{API}/timer/start",
        json={"project_id": setup["p"]["audit"]["id"], "start_at": ago(minutes=30)},
    )
    r = c.post(f"{API}/timer/start", json={"project_id": setup["p"]["dash"]["id"]}).json()
    assert r["stopped"]["project_name"] == "Audit" and r["stopped"]["running"] is False
    assert r["running"]["project_name"] == "Dashboard"
    running = [e for e in c.get(f"{API}/time-entries").json() if e["running"]]
    assert len(running) == 1


def test_timer_can_start_blank_and_be_assigned_later(logged_in, setup):
    c = logged_in
    t = c.post(f"{API}/timer/start", json={}).json()["running"]
    assert t["project_id"] is None and t["rate"] == "100.00"  # workspace default
    r = c.patch(f"{API}/timer", json={"project_id": setup["p"]["sensors"]["id"]}).json()
    assert r["running"]["project_name"] == "Sensors" and r["running"]["rate"] == "150.00"


def test_long_running_timer_and_trim(logged_in, setup):
    c = logged_in
    c.post(
        f"{API}/timer/start",
        json={"project_id": setup["p"]["audit"]["id"], "start_at": ago(hours=9)},
    )
    t = c.get(f"{API}/timer").json()
    assert t["long_running"] is True and t["warning_after_minutes"] == 240
    start = datetime.fromisoformat(t["running"]["start_at"])
    e = c.post(f"{API}/timer/stop", json={"end_at": (start + timedelta(hours=2)).isoformat()})
    assert e.json()["duration_seconds"] == 7200


def test_discard_and_future_start(logged_in, setup):
    c = logged_in
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert c.post(f"{API}/timer/start", json={"start_at": future}).status_code == 422
    c.post(f"{API}/timer/start", json={})
    assert c.delete(f"{API}/timer").status_code == 204
    assert c.get(f"{API}/timer").json()["running"] is None
    assert c.get(f"{API}/time-entries").json() == []


# --- Entries and frozen rates ----------------------------------------------------------


def test_manual_entry_in_local_time(logged_in, setup):
    # 09:00 in Chicago on 12 Oct 2026 is 14:00 UTC (CDT).
    e = log(logged_in, setup["p"]["audit"], "2026-10-12T09:00:00", 120)
    assert datetime.fromisoformat(e["start_at"]).astimezone(UTC).hour == 14
    assert e["hours"] == "2.00" and e["amount"] == "250.00" and e["source"] == "manual"


def test_entry_validation(logged_in, setup):
    c = logged_in
    base = {"project_id": setup["p"]["audit"]["id"], "start_at": "2026-10-12T09:00:00"}
    assert c.post(f"{API}/time-entries", json=base).status_code == 422  # no end or duration
    r = c.post(f"{API}/time-entries", json={**base, "end_at": "2026-10-12T08:00:00"})
    assert r.json()["detail"] == "end_before_start"
    r = c.post(
        f"{API}/time-entries",
        json={**base, "end_at": "2026-10-12T10:00:00", "duration_minutes": 60},
    )
    assert r.status_code == 422


def test_rate_is_frozen_until_repriced(logged_in, setup):
    c = logged_in
    old = log(c, setup["p"]["audit"], "2026-10-12T09:00:00", 60)
    c.patch(f"{API}/clients/{setup['acme']['id']}", json={"rate": "140"})
    new = log(c, setup["p"]["audit"], "2026-10-13T09:00:00", 60)
    assert (old["amount"], new["amount"]) == ("125.00", "140.00")
    # Editing notes doesn't re-price; it's still the old rate.
    assert (
        c.patch(f"{API}/time-entries/{old['id']}", json={"notes": "x"}).json()["rate"] == "125.00"
    )
    # Changing what the entry is for does.
    moved = c.patch(
        f"{API}/time-entries/{old['id']}", json={"project_id": setup["p"]["sensors"]["id"]}
    ).json()
    assert moved["rate"] == "150.00" and moved["amount"] == "150.00"
    c.patch(f"{API}/time-entries/{old['id']}", json={"project_id": setup["p"]["audit"]["id"]})
    c.patch(f"{API}/clients/{setup['acme']['id']}", json={"rate": "160"})
    r = c.post(f"{API}/time-entries/reprice", json={"client_id": setup["acme"]["id"]}).json()
    assert r == {"checked": 2, "changed": 2}
    assert {e["amount"] for e in c.get(f"{API}/time-entries").json()} == {"160.00"}


def test_activity_rate_and_billable_default(logged_in, setup):
    c = logged_in
    travel = log(
        c, setup["p"]["audit"], "2026-10-12T07:00:00", 90, activity_id=setup["a"]["travel"]["id"]
    )
    assert (travel["rate"], travel["rate_source"], travel["amount"]) == (
        "60.00",
        "activity",
        "90.00",
    )
    adm = log(
        c, setup["p"]["audit"], "2026-10-12T16:00:00", 30, activity_id=setup["a"]["admin"]["id"]
    )
    assert adm["billable"] is False
    override = log(c, setup["p"]["audit"], "2026-10-12T17:00:00", 60, rate_override="0")
    assert (override["rate"], override["rate_source"], override["amount"]) == (
        "0.00",
        "entry",
        "0.00",
    )


def test_overlaps_are_reported(logged_in, setup):
    c = logged_in
    a = log(c, setup["p"]["audit"], "2026-10-12T09:00:00", 60)
    b = log(c, setup["p"]["dash"], "2026-10-12T09:30:00", 60)
    assert b["overlaps"] == [a["id"]]
    clean = log(c, setup["p"]["dash"], "2026-10-12T10:30:00", 30)
    assert clean["overlaps"] == []


def test_invoiced_entries_are_locked(logged_in, setup, db):
    import uuid

    from tahgul.models import TimeEntry

    e = log(logged_in, setup["p"]["audit"], "2026-10-12T09:00:00", 60)
    row = db.get(TimeEntry, uuid.UUID(e["id"]))
    row.invoice_id = uuid.uuid4()
    db.commit()
    r = logged_in.patch(f"{API}/time-entries/{e['id']}", json={"notes": "x"})
    assert r.status_code == 409 and r.json()["detail"] == "entry_invoiced"
    assert logged_in.delete(f"{API}/time-entries/{e['id']}").status_code == 409


def test_agent_limits_on_time(logged_in, client, setup):
    h = token(logged_in, "read", "write")
    agent = client.__class__(client.app)
    body = {
        "project_id": setup["p"]["audit"]["id"],
        "start_at": "2026-10-12T09:00:00",
        "duration_minutes": 30,
    }
    e = agent.post(f"{API}/time-entries", json=body, headers=h).json()
    assert e["source"] == "agent"
    r = agent.post(f"{API}/time-entries", json={**body, "rate_override": "10"}, headers=h)
    assert r.status_code == 403
    assert agent.delete(f"{API}/time-entries/{e['id']}", headers=h).status_code == 403
    assert agent.post(f"{API}/time-entries/reprice", json={}, headers=h).status_code == 403
    # Siri-style: start and stop by voice.
    assert (
        agent.post(
            f"{API}/timer/start", json={"project_id": setup["p"]["dash"]["id"]}, headers=h
        ).status_code
        == 200
    )
    assert agent.post(f"{API}/timer/stop", headers=h).status_code == 200


def test_list_filters(logged_in, setup):
    c = logged_in
    log(c, setup["p"]["audit"], "2026-10-12T09:00:00", 60)
    log(c, setup["p"]["dash"], "2026-10-13T09:00:00", 60)
    log(c, setup["p"]["dash"], "2026-10-20T09:00:00", 60)
    week = c.get(f"{API}/time-entries", params={"from": "2026-10-12", "to": "2026-10-18"}).json()
    assert len(week) == 2
    beta = c.get(f"{API}/time-entries", params={"client_id": setup["beta"]["id"]}).json()
    assert len(beta) == 2


# --- Timesheet ----------------------------------------------------------------------------


def cell(c, project, day, hours, activity=None):
    body = {"project_id": project["id"], "date": day, "hours": hours}
    if activity:
        body["activity_id"] = activity["id"]
    return c.put(f"{API}/timesheet/cell", json=body)


def row_for(week, name):
    return next(r for r in week["rows"] if r["project_name"] == name)


def test_timesheet_grid(logged_in, setup):
    c = logged_in
    audit = setup["p"]["audit"]
    w = cell(c, audit, "2026-10-12", "3").json()
    assert w["week_start"] == "2026-10-12"
    assert row_for(w, "Audit")["hours"][0] == "3.00"
    w = cell(c, audit, "2026-10-12", "5").json()  # adds 2 h
    assert row_for(w, "Audit")["hours"][0] == "5.00"
    assert len(c.get(f"{API}/time-entries").json()) == 2
    w = cell(c, audit, "2026-10-12", "1.5").json()  # shrinks newest first
    assert row_for(w, "Audit")["hours"][0] == "1.50"
    w = cell(c, audit, "2026-10-12", "0").json()
    assert w["rows"] == [] and c.get(f"{API}/time-entries").json() == []
    # Any day of the week finds the same week.
    cell(c, audit, "2026-10-15", "2")
    w = c.get(f"{API}/timesheet", params={"week": "2026-10-18"}).json()
    assert row_for(w, "Audit")["hours"] == ["0.00"] * 3 + ["2.00"] + ["0.00"] * 3
    assert w["day_totals"][3] == "2.00" and w["total"] == "2.00"


def test_timesheet_never_shortens_timer_entries(logged_in, setup, db):
    c = logged_in
    e = log(c, setup["p"]["audit"], "2026-10-12T09:00:00", 120)
    from uuid import UUID

    from tahgul.models import TimeEntry

    db.get(TimeEntry, UUID(e["id"])).source = "timer"
    db.commit()
    r = cell(c, setup["p"]["audit"], "2026-10-12", "1")
    assert r.status_code == 409 and r.json()["detail"] == "cannot_shrink_tracked_time"


def test_timesheet_days_follow_workspace_time_zone(logged_in, setup):
    # 23:30 Monday in Chicago is 04:30 Tuesday UTC; it belongs to Monday.
    log(logged_in, setup["p"]["audit"], "2026-10-12T23:30:00", 20)
    w = logged_in.get(f"{API}/timesheet", params={"week": "2026-10-12"}).json()
    assert row_for(w, "Audit")["hours"][:2] == ["0.33", "0.00"]


# --- Reports: the milestone's done-when -------------------------------------------------


def test_a_week_of_work_summed_by_client(logged_in, setup):
    """Done when: a week of real work can be logged and summed by client."""
    c, p, a = logged_in, setup["p"], setup["a"]
    week = [
        # Mon: Acme audit with travel, Beta dashboard
        (p["audit"], "2026-10-12T08:00:00", 60, {"activity_id": a["travel"]["id"]}),  # 60
        (p["audit"], "2026-10-12T09:00:00", 180, {}),  # 3 h × 125 = 375
        (p["dash"], "2026-10-12T13:00:00", 240, {}),  # 4 h × 100 = 400
        # Tue: sensors at the project rate, non-billable admin
        (p["sensors"], "2026-10-13T09:00:00", 300, {}),  # 5 h × 150 = 750
        (p["audit"], "2026-10-13T15:00:00", 30, {"activity_id": a["admin"]["id"]}),  # unbilled
        # Wed–Fri
        (p["dash"], "2026-10-14T09:00:00", 420, {}),  # 7 h × 100 = 700
        (p["audit"], "2026-10-15T09:00:00", 360, {}),  # 6 h × 125 = 750
        (p["idea"], "2026-10-16T09:00:00", 90, {}),  # 1.5 h × 100 = 150, no client
        # Next week (outside the range)
        (p["audit"], "2026-10-19T09:00:00", 60, {}),
    ]
    for proj, start, mins, extra in week:
        log(c, proj, start, mins, **extra)

    s = c.get(f"{API}/reports/summary", params={"from": "2026-10-12", "to": "2026-10-18"}).json()
    assert s["entry_count"] == 8 and s["currency"] == "USD"
    assert s["totals"]["hours"] == "28.00"
    assert s["totals"]["billable_hours"] == "27.50"
    assert s["totals"]["amount"] == "3185.00"
    assert s["totals"]["unbilled_amount"] == "3185.00"
    assert s["totals"]["effective_rate"] == "115.82"

    by_client = {x["name"]: x for x in s["by_client"]}
    assert set(by_client) == {"Acme", "Beta", "No client"}
    assert (by_client["Acme"]["hours"], by_client["Acme"]["amount"]) == ("15.50", "1935.00")
    assert (by_client["Beta"]["hours"], by_client["Beta"]["amount"]) == ("11.00", "1100.00")
    assert (by_client["No client"]["hours"], by_client["No client"]["amount"]) == ("1.50", "150.00")
    assert sum(float(x["amount"]) for x in s["by_client"]) == 3185.0

    audit = next(x for x in s["by_project"] if x["name"] == "Audit")
    assert audit["hours"] == "10.50"
    # Budget counts all work ever, including next week's hour: 20 − 11.5.
    assert audit["used_hours_all_time"] == "11.50" and audit["budget_hours_left"] == "8.50"

    assert [w["week_start"] for w in s["by_week"]] == ["2026-10-12"]

    # Filter to one client.
    acme = c.get(
        f"{API}/reports/summary",
        params={"from": "2026-10-12", "to": "2026-10-18", "client": setup["acme"]["id"]},
    ).json()
    assert acme["totals"]["amount"] == "1935.00"
    assert [x["name"] for x in acme["by_client"]] == ["Acme"]

    # The timesheet for the same week agrees.
    w = c.get(f"{API}/timesheet", params={"week": "2026-10-12"}).json()
    assert w["total"] == "28.00"
    assert w["day_totals"] == ["8.00", "5.50", "7.00", "6.00", "1.50", "0.00", "0.00"]


def test_running_timer_not_in_reports(logged_in, setup):
    logged_in.post(
        f"{API}/timer/start",
        json={"project_id": setup["p"]["audit"]["id"], "start_at": ago(minutes=30)},
    )
    s = logged_in.get(f"{API}/reports/summary").json()
    assert s["entry_count"] == 0


# --- Rounding (applied at invoice time) ---------------------------------------------------


@pytest.mark.parametrize(
    "secs,minutes,mode,expected",
    [
        (61, 6, "up", 360),
        (360, 6, "up", 360),
        (361, 6, "up", 720),
        (7 * 60, 15, "nearest", 0),
        (8 * 60, 15, "nearest", 900),
        (22 * 60, 15, "down", 900),
        (1234, 0, "up", 1234),
        (0, 15, "up", 0),
    ],
)
def test_rounding(secs, minutes, mode, expected):
    assert round_seconds(secs, minutes, mode) == expected


def test_workspace_rounding_settings(logged_in, setup):
    ws = setup["ws"]
    r = logged_in.patch(
        f"{API}/workspaces/{ws['id']}",
        json={"rounding_minutes": 6, "rounding_mode": "nearest", "timer_warning_minutes": 300},
    )
    assert r.status_code == 200
    assert (r.json()["rounding_minutes"], r.json()["timer_warning_minutes"]) == (6, 300)
    assert (
        logged_in.patch(f"{API}/workspaces/{ws['id']}", json={"rounding_minutes": 7}).status_code
        == 422
    )
