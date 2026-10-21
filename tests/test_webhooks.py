"""Webhooks (later milestone): outbox, signing, retries, scheduled events."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tahgul import webhooks as wh

API = "/api/v1"


@pytest.fixture
def receiver(monkeypatch):
    """A fake agent endpoint. Records what arrives; ``state["fail"]`` makes it return 503."""
    state = {"calls": [], "fail": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        state["calls"].append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": json.loads(body),
                "raw": body,
            }
        )
        if state["fail"]:
            state["fail"] -= 1
            return httpx.Response(503, text="busy")
        return httpx.Response(204)

    monkeypatch.setattr(wh, "_transport", httpx.MockTransport(handler))
    return state


def hook(c, events, url="http://agent.lan:8787/tahgul"):
    r = c.post(f"{API}/webhooks", json={"name": "agent", "url": url, "events": events})
    assert r.status_code == 201, r.text
    return r.json()


def events_of(state):
    return [x["body"]["event"] for x in state["calls"]]


@pytest.fixture
def project(logged_in):
    c = logged_in
    client = c.post(f"{API}/clients", json={"name": "Acme", "rate": "100"}).json()
    return c.post(f"{API}/projects", json={"name": "Audit", "client_id": client["id"]}).json()


def test_timer_events_signed(logged_in, project, receiver):
    c = logged_in
    h = hook(c, ["timer.started", "timer.stopped"])
    assert h["secret"].startswith("whsec_")
    c.post(f"{API}/timer/start", json={"project_id": project["id"], "notes": "kickoff"})
    c.post(f"{API}/timer/stop")
    assert wh.deliver_pending() == 2
    assert events_of(receiver) == ["timer.started", "timer.stopped"]

    call = receiver["calls"][1]
    data = call["body"]["data"]
    assert data["project_name"] == "Audit" and data["client_name"] == "Acme"
    assert data["notes"] == "kickoff" and data["end_at"] is not None
    hd = call["headers"]
    assert hd["x-tahgul-event"] == "timer.stopped" and hd["x-tahgul-delivery"] == call["body"]["id"]
    expected = hmac.new(
        h["secret"].encode(), f"{hd['x-tahgul-timestamp']}.".encode() + call["raw"], hashlib.sha256
    ).hexdigest()
    assert hd["x-tahgul-signature"] == f"sha256={expected}"
    assert wh.deliver_pending() == 0  # nothing sent twice


def test_only_subscribed_events_and_wildcard(logged_in, project, receiver):
    c = logged_in
    hook(c, ["task.completed"])
    hook(c, ["*"], url="http://logger.lan/all")
    t = c.post(f"{API}/tasks", json={"project_id": project["id"], "title": "Patch firewall"}).json()
    c.patch(f"{API}/tasks/{t['id']}", json={"done": True})
    c.patch(f"{API}/tasks/{t['id']}", json={"title": "Patch the firewall"})  # not an event
    wh.deliver_pending()
    by_url = {}
    for x in receiver["calls"]:
        by_url.setdefault(x["url"], []).append(x["body"]["event"])
    assert by_url["http://agent.lan:8787/tahgul"] == ["task.completed"]
    assert by_url["http://logger.lan/all"] == ["task.created", "task.completed"]
    done = next(x for x in receiver["calls"] if x["body"]["event"] == "task.completed")
    assert done["body"]["data"]["status"] == "Done" and done["body"]["data"]["ref"] == t["ref"]


def test_rolled_back_work_sends_nothing(logged_in, project, receiver, db):
    from tahgul.models import Project

    hook(logged_in, ["idea.created"])
    db.add(Project(workspace_id=uuid.UUID(project["workspace_id"]), name="Never", status="idea"))
    db.flush()  # the delivery is queued in the same transaction...
    db.rollback()  # ...and goes away with it
    assert wh.deliver_pending() == 0 and receiver["calls"] == []


def test_retries_then_gives_up(logged_in, project, receiver):
    c = logged_in
    h = hook(c, ["idea.created"])
    receiver["fail"] = 1
    c.post(f"{API}/ideas", json={"title": "Drone surveys"})
    assert wh.deliver_pending() == 0
    d = c.get(f"{API}/webhooks/{h['id']}/deliveries").json()[0]
    assert d["status"] == "pending" and d["attempts"] == 1 and d["response_code"] == 503
    assert wh.deliver_pending() == 0  # backoff: not due yet
    later = datetime.now(UTC) + timedelta(minutes=2)
    assert wh.deliver_pending(now=later) == 1
    d = c.get(f"{API}/webhooks/{h['id']}/deliveries").json()[0]
    assert d["status"] == "sent" and d["attempts"] == 2
    ids = {x["body"]["id"] for x in receiver["calls"]}
    assert len(ids) == 1  # same delivery id on the retry

    receiver["fail"] = 99
    c.post(f"{API}/ideas", json={"title": "Another"})
    t = datetime.now(UTC)
    for _ in range(len(wh.BACKOFF) + 1):
        wh.deliver_pending(now=t)
        t += timedelta(hours=7)
    d = c.get(f"{API}/webhooks/{h['id']}/deliveries").json()[0]
    assert d["status"] == "failed" and d["attempts"] == len(wh.BACKOFF) + 1
    assert c.get(f"{API}/webhooks").json()[0]["last_status"] == "failed"

    receiver["fail"] = 0
    r = c.post(f"{API}/webhook-deliveries/{d['id']}/retry")
    assert r.json()["status"] == "sent"


def test_invoice_and_idea_lifecycle(logged_in, project, receiver):
    c = logged_in
    hook(c, ["invoice.issued", "invoice.paid", "idea.promoted"])
    c.post(
        f"{API}/time-entries",
        json={
            "project_id": project["id"],
            "start_at": "2026-09-02T09:00:00",
            "duration_minutes": 60,
        },
    )
    inv = c.post(
        f"{API}/invoices",
        json={
            "client_id": project["client_id"],
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
            "issue_date": "2026-10-01",
        },
    ).json()
    c.post(f"{API}/invoices/{inv['id']}/issue")
    c.post(f"{API}/invoices/{inv['id']}/paid", json={"paid_on": "2026-10-05"})
    idea = c.post(f"{API}/ideas", json={"title": "Drones"}).json()
    c.post(f"{API}/ideas/{idea['id']}/promote", json={})
    wh.deliver_pending()
    assert events_of(receiver) == ["invoice.issued", "invoice.paid", "idea.promoted"]
    issued = receiver["calls"][0]["body"]["data"]
    assert issued["number"] == "2026-001" and issued["total"] == "100.00"


def test_due_soon_fires_once(logged_in, project, receiver, db):
    from tahgul.models import Workspace
    from tahgul.timekeeping import tz

    c = logged_in
    hook(c, ["task.due_soon"])
    zone = tz(db.query(Workspace).first())
    soon = datetime.now(zone) + timedelta(minutes=30)
    t = c.post(
        f"{API}/tasks",
        json={
            "project_id": project["id"],
            "title": "Call Laura",
            "due_date": soon.date().isoformat(),
            "due_time": soon.strftime("%H:%M"),
        },
    ).json()
    later = datetime.now(zone) + timedelta(days=3)
    c.post(
        f"{API}/tasks",
        json={
            "project_id": project["id"],
            "title": "Later",
            "due_date": later.date().isoformat(),
            "due_time": "10:00",
        },
    )
    assert wh.scan(db) == 1
    assert wh.scan(db) == 0  # once only
    wh.deliver_pending()
    body = receiver["calls"][0]["body"]
    assert body["event"] == "task.due_soon" and body["data"]["id"] == t["id"]
    assert body["data"]["due_at"]


def test_event_ended(logged_in, project, receiver, db):
    from tahgul.models import Calendar, CalendarAccount, CalendarEvent, Workspace

    c = logged_in
    hook(c, ["calendar.event_ended"])
    ws = db.query(Workspace).first()
    acct = CalendarAccount(name="t", kind="caldav", url="http://x/", username="u", password_enc="x")
    db.add(acct)
    db.flush()
    cal = Calendar(
        account_id=acct.id, workspace_id=ws.id, url="http://x/c/", name="Work", enabled=True
    )
    db.add(cal)
    db.flush()
    now = datetime.now(UTC)
    ev = CalendarEvent(
        calendar_id=cal.id,
        href="/c/1.ics",
        uid="1",
        ics="",
        title="Acme sync",
        start_at=now - timedelta(minutes=50),
        end_at=now - timedelta(minutes=5),
        modified_at=now,
        project_id=uuid.UUID(project["id"]),
    )
    db.add(ev)
    db.commit()
    assert wh.scan(db) == 1 and wh.scan(db) == 0
    wh.deliver_pending()
    data = receiver["calls"][0]["body"]["data"]
    assert data["title"] == "Acme sync" and data["project_name"] == "Audit"
    assert data["log_url"].endswith("/log-time")


def test_test_ping_and_admin_only(logged_in, client, receiver):
    c = logged_in
    h = hook(c, ["*"])
    r = c.post(f"{API}/webhooks/{h['id']}/test")
    assert r.json()["status"] == "sent" and receiver["calls"][0]["body"]["event"] == "ping"
    tok = c.post(f"{API}/tokens", json={"name": "agent", "scopes": ["read", "write"]}).json()
    agent = client.__class__(client.app)
    agent.headers["Authorization"] = f"Bearer {tok['token']}"
    assert agent.get(f"{API}/webhooks").status_code == 403
    assert agent.get(f"{API}/webhooks/events").status_code == 200
    r = c.post(f"{API}/webhooks", json={"name": "x", "url": "ftp://nope", "events": ["*"]})
    assert r.status_code == 422
    r = c.post(f"{API}/webhooks", json={"name": "x", "url": "http://a", "events": ["nope"]})
    assert r.status_code == 422
    # Turned off: nothing queued.
    c.patch(f"{API}/webhooks/{h['id']}", json={"active": False})
    c.post(f"{API}/ideas", json={"title": "Quiet"})
    assert wh.deliver_pending() == 0
    # Rotating the secret returns a new one.
    assert c.post(f"{API}/webhooks/{h['id']}/rotate-secret").json()["secret"] != h["secret"]
    assert c.delete(f"{API}/webhooks/{h['id']}").status_code == 204
    assert c.get(f"{API}/webhooks").json() == []
