"""Tasks and the kanban board (milestone 4)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

API = "/api/v1"


@pytest.fixture
def board(logged_in):
    c = logged_in
    ws = c.get(f"{API}/workspaces").json()[0]
    acme = c.post(f"{API}/clients", json={"name": "Acme Farms", "code": "ACME"}).json()
    proj = c.post(f"{API}/projects", json={"name": "Network audit", "client_id": acme["id"]}).json()
    other = c.post(f"{API}/projects", json={"name": "Side project"}).json()
    cols = c.get(f"{API}/projects/{proj['id']}/columns").json()
    return {
        "ws": ws,
        "acme": acme,
        "p": proj,
        "other": other,
        "cols": {col["name"]: col for col in cols},
    }


def task(c, project, title, **kw):
    r = c.post(f"{API}/tasks", json={"project_id": project["id"], "title": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def column_titles(c, project, status):
    return [
        t["title"]
        for t in c.get(f"{API}/tasks", params={"project": project["id"], "status": status}).json()
    ]


# --- Columns ----------------------------------------------------------------------------


def test_new_projects_get_default_columns(board):
    assert list(board["cols"]) == ["To do", "In progress", "Done"]
    assert [c["is_done"] for c in board["cols"].values()] == [False, False, True]


def test_custom_columns(logged_in, board):
    c, pid = logged_in, board["p"]["id"]
    cols = c.post(f"{API}/projects/{pid}/columns", json={"name": "Review", "position": 2}).json()
    assert [x["name"] for x in cols] == ["To do", "In progress", "Review", "Done"]
    assert c.post(f"{API}/projects/{pid}/columns", json={"name": "review"}).status_code == 409
    review = next(x for x in cols if x["name"] == "Review")
    cols = c.patch(f"{API}/columns/{review['id']}", json={"name": "QA", "position": 0}).json()
    assert [x["name"] for x in cols] == ["QA", "To do", "In progress", "Done"]


def test_deleting_a_column_moves_its_tasks(logged_in, board):
    c = logged_in
    t = task(c, board["p"], "Scan ports", status="In progress")
    doing, todo = board["cols"]["In progress"], board["cols"]["To do"]
    r = c.delete(f"{API}/columns/{doing['id']}")
    assert r.status_code == 409 and r.json()["detail"] == "column_has_tasks"
    r = c.delete(f"{API}/columns/{doing['id']}", params={"move_to": todo["id"]})
    assert [x["name"] for x in r.json()] == ["To do", "Done"]
    assert c.get(f"{API}/tasks/{t['id']}").json()["status"] == "To do"


def test_marking_a_column_done_completes_its_tasks(logged_in, board):
    c = logged_in
    t = task(c, board["p"], "Ship it", status="In progress")
    c.patch(f"{API}/columns/{board['cols']['In progress']['id']}", json={"is_done": True})
    assert c.get(f"{API}/tasks/{t['id']}").json()["done"] is True


# --- Tasks ------------------------------------------------------------------------------


def test_create_with_all_fields(logged_in, board):
    t = task(
        logged_in,
        board["p"],
        "Fix VPN",
        description="**Tunnel** drops",
        type="bug",
        priority="high",
        labels=["#Network", "network", "vpn"],
        due_date="2026-10-30",
        due_time="14:00",
        estimate_minutes=90,
    )
    assert t["ref"] == "#1" and t["status"] == "To do" and t["done"] is False
    assert t["labels"] == ["network", "vpn"]
    assert (t["type"], t["priority"], t["due_time"]) == ("bug", "high", "14:00:00")
    assert t["client_name"] == "Acme Farms"
    assert task(logged_in, board["other"], "Second")["number"] == 2  # per workspace


def test_status_by_name_and_done_tracking(logged_in, board):
    c = logged_in
    t = task(c, board["p"], "Write report")
    r = c.patch(f"{API}/tasks/{t['id']}", json={"status": "done"}).json()
    assert r["status"] == "Done" and r["done"] and r["completed_at"]
    r = c.patch(f"{API}/tasks/{t['id']}", json={"status": "To do"}).json()
    assert r["done"] is False and r["completed_at"] is None
    # done=true/false works whatever the columns are called.
    assert c.patch(f"{API}/tasks/{t['id']}", json={"done": True}).json()["status"] == "Done"
    assert c.patch(f"{API}/tasks/{t['id']}", json={"done": False}).json()["status"] == "To do"
    bad = c.patch(f"{API}/tasks/{t['id']}", json={"status": "Nope"})
    assert bad.status_code == 422 and bad.json()["detail"]["choices"] == [
        "To do",
        "In progress",
        "Done",
    ]


def test_drag_and_drop_ordering(logged_in, board):
    c = logged_in
    a, b, d = (task(c, board["p"], x) for x in ("A", "B", "C"))
    todo, doing = board["cols"]["To do"], board["cols"]["In progress"]
    assert column_titles(c, board["p"], "To do") == ["A", "B", "C"]
    c.post(f"{API}/tasks/{d['id']}/move", json={"column_id": todo["id"], "index": 0})
    assert column_titles(c, board["p"], "To do") == ["C", "A", "B"]
    c.post(f"{API}/tasks/{a['id']}/move", json={"column_id": doing["id"], "index": 0})
    c.post(f"{API}/tasks/{b['id']}/move", json={"column_id": doing["id"], "index": 0})
    assert column_titles(c, board["p"], "In progress") == ["B", "A"]
    assert column_titles(c, board["p"], "To do") == ["C"]
    other_cols = c.get(f"{API}/projects/{board['other']['id']}/columns").json()
    r = c.post(f"{API}/tasks/{a['id']}/move", json={"column_id": other_cols[0]["id"], "index": 0})
    assert r.status_code == 422


def test_subtasks(logged_in, board):
    c = logged_in
    parent = task(c, board["p"], "Audit")
    s1 = task(c, board["p"], "Scan", parent_id=parent["id"])
    task(c, board["p"], "Report", parent_id=parent["id"])
    c.patch(f"{API}/tasks/{s1['id']}", json={"status": "Done"})
    d = c.get(f"{API}/tasks/{parent['id']}").json()
    assert (d["subtask_count"], d["subtasks_done"]) == (2, 1)
    assert [s["title"] for s in d["subtasks"]] == ["Scan", "Report"]
    # No cycles, no cross-project parents.
    assert c.patch(f"{API}/tasks/{parent['id']}", json={"parent_id": s1["id"]}).status_code == 422
    elsewhere = task(c, board["other"], "Elsewhere")
    assert (
        c.patch(f"{API}/tasks/{elsewhere['id']}", json={"parent_id": parent["id"]}).status_code
        == 422
    )
    # Top-level listing hides subtasks.
    top = c.get(f"{API}/tasks", params={"project": board["p"]["id"], "top_level": True}).json()
    assert [t["title"] for t in top] == ["Audit"]


def test_blocked_by(logged_in, board):
    c = logged_in
    scan = task(c, board["p"], "Scan network")
    report = task(c, board["p"], "Write report")
    d = c.post(f"{API}/tasks/{report['id']}/blocked-by", json={"task_id": scan["id"]}).json()
    assert d["blocked"] is True and [b["title"] for b in d["blockers"]] == ["Scan network"]
    assert c.get(f"{API}/tasks/{scan['id']}").json()["blocks"] == [report["id"]]
    # A cycle is refused.
    r = c.post(f"{API}/tasks/{scan['id']}/blocked-by", json={"task_id": report["id"]})
    assert r.status_code == 422 and r.json()["detail"] == "link_cycle"
    # Finishing the blocker unblocks.
    c.patch(f"{API}/tasks/{scan['id']}", json={"status": "Done"})
    assert c.get(f"{API}/tasks/{report['id']}").json()["blocked"] is False
    d = c.delete(f"{API}/tasks/{report['id']}/blocked-by/{scan['id']}").json()
    assert d["blocked_by"] == []


def test_move_to_another_project(logged_in, board):
    c = logged_in
    t = task(c, board["p"], "Wander", status="Done")
    r = c.patch(f"{API}/tasks/{t['id']}", json={"project_id": board["other"]["id"]}).json()
    assert r["project_name"] == "Side project" and r["status"] == "Done"


def test_filters_and_sorting(logged_in, board):
    c = logged_in
    today = date.today()
    task(c, board["p"], "Overdue", due_date=str(today - timedelta(days=2)), priority="low")
    task(
        c,
        board["p"],
        "Soon",
        due_date=str(today + timedelta(days=3)),
        priority="urgent",
        labels=["ops"],
    )
    task(c, board["other"], "Later", due_date=str(today + timedelta(days=30)))
    task(c, board["p"], "Done thing", status="Done", due_date=str(today))
    task(c, board["p"], "Bug", type="bug")

    def titles(**params):
        return [t["title"] for t in c.get(f"{API}/tasks", params=params).json()]

    week = str(today + timedelta(days=7))
    assert titles(open=True, due_before=week, sort="due") == ["Overdue", "Soon"]
    assert titles(overdue=True) == ["Overdue"]
    assert titles(label="ops") == ["Soon"]
    assert titles(priority=["urgent", "low"], sort="priority") == ["Soon", "Overdue"]
    assert titles(type="bug") == ["Bug"]
    assert titles(q="soo") == ["Soon"]
    assert titles(client=board["acme"]["id"], open=False) == ["Done thing"]
    assert c.get(f"{API}/tasks", params={"q": "Overdue"}).json()[0]["overdue"] is True


def test_by_number(logged_in, board):
    t = task(logged_in, board["p"], "Find me")
    r = logged_in.get(f"{API}/tasks/by-number/{t['number']}").json()
    assert r["id"] == t["id"]


def test_delete_takes_subtasks_and_needs_admin(logged_in, client, board):
    c = logged_in
    parent = task(c, board["p"], "Parent")
    task(c, board["p"], "Child", parent_id=parent["id"])
    h = {
        "Authorization": "Bearer "
        + c.post(f"{API}/tokens", json={"name": "agent", "scopes": ["read", "write"]}).json()[
            "token"
        ]
    }
    agent = client.__class__(client.app)
    assert agent.delete(f"{API}/tasks/{parent['id']}", headers=h).status_code == 403
    assert c.delete(f"{API}/tasks/{parent['id']}").status_code == 204
    assert c.get(f"{API}/tasks").json() == []


# --- Comments and history ---------------------------------------------------------------


def test_comments_and_history(logged_in, client, board):
    c = logged_in
    t = task(c, board["p"], "Fix VPN")
    c.patch(f"{API}/tasks/{t['id']}", json={"priority": "high", "status": "In progress"})
    c.post(f"{API}/tasks/{t['id']}/comments", json={"body": "Tunnel drops at 2am"})
    h = {
        "Authorization": "Bearer "
        + c.post(f"{API}/tokens", json={"name": "agent", "scopes": ["write"]}).json()["token"]
    }
    bare = client.__class__(client.app)
    bare.post(f"{API}/tasks/{t['id']}/comments", json={"body": "Logged 1h"}, headers=h)
    bare.patch(f"{API}/tasks/{t['id']}", json={"status": "Done"}, headers=h)

    hist = c.get(f"{API}/tasks/{t['id']}/history").json()
    summary = [(x["kind"], x["actor_name"], x.get("action")) for x in hist]
    assert summary == [
        ("change", "owner", "create"),
        ("change", "owner", "update"),
        ("comment", "owner", None),
        ("comment", "agent", None),
        ("change", "agent", "update"),
    ]
    assert hist[1]["changes"]["status"] == ["To do", "In progress"]
    assert c.get(f"{API}/tasks/{t['id']}").json()["comment_count"] == 2

    agent_comment = next(x for x in hist if x["actor_name"] == "agent" and x["kind"] == "comment")
    owner_comment = next(x for x in hist if x["actor_name"] == "owner" and x["kind"] == "comment")
    # The agent can delete its own comment but not the owner's.
    assert (
        bare.delete(f"{API}/comments/{owner_comment['comment_id']}", headers=h).status_code == 403
    )
    assert (
        bare.delete(f"{API}/comments/{agent_comment['comment_id']}", headers=h).status_code == 204
    )


# --- Time on tasks ----------------------------------------------------------------------


def test_timer_from_a_task(logged_in, board):
    c = logged_in
    t = task(c, board["p"], "Configure firewall")
    r = c.post(f"{API}/timer/start", json={"task_id": t["id"]}).json()["running"]
    assert r["project_name"] == "Network audit" and r["task_ref"] == t["ref"]
    assert r["notes"] == "Configure firewall"
    c.post(f"{API}/timer/stop")
    log = c.post(
        f"{API}/time-entries",
        json={"task_id": t["id"], "start_at": "2026-10-12T09:00:00", "duration_minutes": 45},
    ).json()
    assert log["task_title"] == "Configure firewall"
    assert c.get(f"{API}/tasks/{t['id']}").json()["tracked_seconds"] >= 45 * 60
    wrong = c.post(
        f"{API}/time-entries",
        json={
            "task_id": t["id"],
            "project_id": board["other"]["id"],
            "start_at": "2026-10-12T11:00:00",
            "duration_minutes": 5,
        },
    )
    assert wrong.status_code == 422


# --- Quick add and saved views ------------------------------------------------------------


def test_quick_add(logged_in, board):
    c = logged_in
    r = c.post(f"{API}/tasks/quick", json={"text": "Fix VPN for Acme tomorrow !high #network"})
    body = r.json()
    assert body["task"]["title"] == "Fix VPN" and body["project_name"] == "Network audit"
    assert body["task"]["priority"] == "high" and body["task"]["labels"] == ["network"]
    chicago_tomorrow = datetime.now(ZoneInfo("America/Chicago")).date() + timedelta(days=1)
    assert body["task"]["due_date"] == str(chicago_tomorrow)

    preview = c.post(f"{API}/tasks/quick", json={"text": "Plan Q1 !low", "dry_run": True}).json()
    assert preview["task"] is None and preview["needs_confirmation"] is True
    with_default = c.post(
        f"{API}/tasks/quick", json={"text": "Plan Q1 !low", "project_id": board["other"]["id"]}
    ).json()
    assert with_default["task"]["project_name"] == "Side project"


def test_saved_views(logged_in, board):
    c = logged_in
    v = c.post(
        f"{API}/views",
        json={
            "name": "Due this week",
            "filters": {"open": True, "due_within_days": 7, "sort": "due"},
        },
    ).json()
    assert v["position"] == 0
    assert c.post(f"{API}/views", json={"name": "x", "filters": {"bogus": 1}}).status_code == 422
    c.patch(f"{API}/views/{v['id']}", json={"name": "This week"})
    assert [x["name"] for x in c.get(f"{API}/views").json()] == ["This week"]
    assert c.delete(f"{API}/views/{v['id']}").status_code == 204


# --- Done-when: a real project run from the board for a week ------------------------------


def test_a_week_on_the_board(logged_in, board):
    """Plan → work → review → done over a week, with time, comments and a blocker."""
    c, p, cols = logged_in, board["p"], board["cols"]
    review = next(
        x
        for x in c.post(
            f"{API}/projects/{p['id']}/columns", json={"name": "Review", "position": 2}
        ).json()
        if x["name"] == "Review"
    )
    plan = [
        task(c, p, t, priority=pr)
        for t, pr in [
            ("Inventory devices", "high"),
            ("Scan network", "high"),
            ("Patch firmware", "medium"),
            ("Write report", "medium"),
        ]
    ]
    inventory, scan, patch_fw, report = plan
    c.post(f"{API}/tasks/{report['id']}/blocked-by", json={"task_id": scan["id"]})
    for day, t in enumerate([inventory, scan, patch_fw]):
        c.post(
            f"{API}/tasks/{t['id']}/move", json={"column_id": cols["In progress"]["id"], "index": 0}
        )
        c.post(
            f"{API}/time-entries",
            json={
                "task_id": t["id"],
                "start_at": f"2026-10-{12 + day}T09:00:00",
                "duration_minutes": 180,
            },
        )
        c.post(f"{API}/tasks/{t['id']}/move", json={"column_id": review["id"], "index": 0})
    assert c.get(f"{API}/tasks/{report['id']}").json()["blocked"] is True
    for t in (inventory, scan):
        c.post(f"{API}/tasks/{t['id']}/move", json={"column_id": cols["Done"]["id"], "index": 0})
    assert c.get(f"{API}/tasks/{report['id']}").json()["blocked"] is False
    c.post(f"{API}/tasks/{report['id']}/comments", json={"body": "Draft in shared folder"})
    c.patch(f"{API}/tasks/{report['id']}", json={"status": "Review"})

    board_now = {
        name: column_titles(c, p, name) for name in ["To do", "In progress", "Review", "Done"]
    }
    assert board_now == {
        "To do": [],
        "In progress": [],
        "Review": ["Patch firmware", "Write report"],
        "Done": ["Scan network", "Inventory devices"],
    }
    s = c.get(f"{API}/reports/summary", params={"from": "2026-10-12", "to": "2026-10-18"}).json()
    assert s["totals"]["hours"] == "9.00"
    cols_now = {
        x["name"]: x["task_count"] for x in c.get(f"{API}/projects/{p['id']}/columns").json()
    }
    assert cols_now == {"To do": 0, "In progress": 0, "Review": 2, "Done": 2}
