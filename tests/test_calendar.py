"""Two-way calendar sync against a real Radicale server (milestone 6).

The "phone" in these tests is a separate CalDAV client talking straight to Radicale,
the way the iPhone's Calendar app does.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from radicale_server import radicale_server
from sqlalchemy import select

from tahgul.caldav import CalDAVClient
from tahgul.models import AuditLog, CalendarAccount, CalendarEvent

API = "/api/v1"


def ics(uid, title, start, end, extra="", modified=None):
    stamp = (modified or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Phone//EN\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:{stamp}\r\nLAST-MODIFIED:{stamp}\r\n"
        f"DTSTART:{start}\r\nDTEND:{end}\r\nSUMMARY:{title}\r\n{extra}"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


class Phone:
    """Plays the iPhone: edits Radicale directly."""

    def __init__(self, base):
        self.c = CalDAVClient(base, "connor", "secret")
        self.base = base

    def calendar(self, slug, name):
        return self.c.make_calendar(self.c.home(), slug, name)

    def add(self, cal_url, title, start, end, extra="", modified=None):
        uid = str(uuid.uuid4())
        href = cal_url + uid + ".ics"
        self.c.put(href, ics(uid, title, start, end, extra, modified), None)
        return href, uid

    def events(self, cal_url):
        ch = self.c.changes(cal_url, None)
        return {h: data for h, (etag, data) in self.c.fetch(cal_url, list(ch.changed)).items()}

    def edit(self, href, cal_url, old, new, modified=None):
        etag, data = self.c.fetch(cal_url, [href])[href]
        data = data.replace(old, new)
        if modified:
            stamp = modified.strftime("%Y%m%dT%H%M%SZ")
            import re

            data = re.sub(r"LAST-MODIFIED:\d+T\d+Z", f"LAST-MODIFIED:{stamp}", data)
        self.c.put(href, data, etag)

    def delete(self, href, cal_url):
        etag, _ = self.c.fetch(cal_url, [href])[href]
        self.c.delete(href, etag)


@pytest.fixture
def cal(logged_in, tmp_path):
    with radicale_server(str(tmp_path / "radicale")) as base:
        phone = Phone(base)
        work = phone.calendar("work", "Work")
        shared = phone.calendar("client-shared", "Client (shared)")
        c = logged_in
        ws = c.get(f"{API}/workspaces").json()[0]
        c.patch(f"{API}/workspaces/{ws['id']}", json={"default_rate": "100"})
        acme = c.post(
            f"{API}/clients", json={"name": "Acme Farms", "code": "ACME", "rate": "125"}
        ).json()
        proj = c.post(
            f"{API}/projects", json={"name": "Network audit", "client_id": acme["id"]}
        ).json()
        r = c.post(
            f"{API}/calendar-accounts",
            json={
                "name": "Homelab",
                "kind": "radicale",
                "url": base,
                "username": "connor",
                "password": "secret",
            },
        )
        assert r.status_code == 201, r.text
        account = r.json()
        cals = {x["name"]: x for x in account["calendars"]}
        assert set(cals) == {"Work", "Client (shared)"}
        # Discovered calendars start read-only; choose Work as the default.
        assert not any(x["writable"] for x in cals.values())
        c.patch(f"{API}/calendars/{cals['Work']['id']}", json={"is_default": True})
        yield {
            "c": c,
            "phone": phone,
            "work": work,
            "shared": shared,
            "base": base,
            "cals": cals,
            "project": proj,
            "account": account,
        }


def sync(c):
    r = c.post(f"{API}/calendars/sync")
    assert r.status_code == 200, r.text
    return r.json()


def events(c, day_from="2026-10-01", day_to="2026-11-30"):
    return c.get(f"{API}/events", params={"from": day_from, "to": day_to}).json()


# --- The milestone's done-when ----------------------------------------------------------


def test_phone_event_appears_in_tahgul(cal):
    c, phone = cal["c"], cal["phone"]
    phone.add(cal["work"], "Call with Laura", "20261022T150000Z", "20261022T160000Z")
    stats = sync(c)["Homelab"]
    assert stats["added"] == 1
    evs = events(c)
    assert [(e["title"], e["calendar_name"]) for e in evs] == [("Call with Laura", "Work")]
    assert evs[0]["start_at"].startswith("2026-10-22T15:00")


def test_agent_event_appears_on_phone(cal, client):
    c, phone = cal["c"], cal["phone"]
    token = c.post(f"{API}/tokens", json={"name": "agent", "scopes": ["read", "write"]}).json()
    agent = client.__class__(client.app)
    agent.headers["Authorization"] = f"Bearer {token['token']}"
    r = agent.post(
        f"{API}/events",
        json={
            "title": "Site visit",
            "project": "acme",
            "start_at": "2026-10-23T09:00:00",
            "duration_minutes": 90,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["sync_pending"] is False and r.json()["project_name"] == "Network audit"
    on_phone = list(phone.events(cal["work"]).values())
    assert len(on_phone) == 1 and "SUMMARY:Site visit" in on_phone[0]
    # 09:00 Chicago (CDT) = 14:00 UTC.
    assert "DTSTART:20261023T140000Z" in on_phone[0]


# --- Two-way edits, etags and conflicts ---------------------------------------------------


def test_edits_flow_both_ways(cal):
    c, phone = cal["c"], cal["phone"]
    href, _ = phone.add(
        cal["work"],
        "Standup",
        "20261026T140000Z",
        "20261026T143000Z",
        extra="X-PHONE-ONLY:keep me\r\n",
    )
    sync(c)
    ev = events(c)[0]
    # Tahgul moves it; the phone sees the move and keeps properties Tahgul doesn't know.
    r = c.patch(
        f"{API}/events/{ev['id']}",
        json={"start_at": "2026-10-26T10:00:00", "title": "Standup (moved)"},
    )
    assert r.status_code == 200 and r.json()["sync_pending"] is False
    data = phone.events(cal["work"])[href]
    assert "SUMMARY:Standup (moved)" in data and "DTSTART:20261026T150000Z" in data
    assert "X-PHONE-ONLY:keep me" in data
    assert "DTEND:20261026T153000Z" in data  # length kept
    # The phone renames it; Tahgul picks that up on the next sync.
    phone.edit(href, cal["work"], "Standup (moved)", "Standup with Acme")
    assert sync(c)["Homelab"]["updated"] == 1
    assert events(c)[0]["title"] == "Standup with Acme"


def test_conflict_newest_wins_and_loser_is_logged(cal, db):
    c, phone = cal["c"], cal["phone"]
    href, _ = phone.add(cal["work"], "Review", "20261027T140000Z", "20261027T150000Z")
    sync(c)
    ev_id = events(c)[0]["id"]

    # 1) Phone edits later than our pending (unpushed) change: the phone wins.
    row = db.get(CalendarEvent, uuid.UUID(ev_id))
    row.title, row.dirty = "Review (tahgul)", True
    row.modified_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    phone.edit(
        href, cal["work"], "SUMMARY:Review", "SUMMARY:Review (phone)", modified=datetime.now(UTC)
    )
    sync(c)
    assert events(c)[0]["title"] == "Review (phone)"

    # 2) Phone edits, then Tahgul edits more recently without having synced: If-Match
    #    fails, Tahgul's newer edit wins, and the phone's version is logged.
    phone.edit(
        href,
        cal["work"],
        "Review (phone)",
        "Review (phone 2)",
        modified=datetime.now(UTC) - timedelta(minutes=5),
    )
    r = c.patch(f"{API}/events/{ev_id}", json={"title": "Review (tahgul 2)"})
    assert r.status_code == 200 and r.json()["sync_pending"] is False
    assert "SUMMARY:Review (tahgul 2)" in phone.events(cal["work"])[href]

    db.expire_all()
    conflicts = db.scalars(select(AuditLog).where(AuditLog.action == "conflict")).all()
    winners = [a.changes["winner"] for a in conflicts]
    assert winners == ["remote", "tahgul"]
    assert all(a.actor_type == "sync" for a in conflicts)
    assert conflicts[0].changes["lost_local"]["title"] == "Review (tahgul)"


def test_deletes_flow_both_ways(cal):
    c, phone = cal["c"], cal["phone"]
    a, _ = phone.add(cal["work"], "Delete on phone", "20261028T140000Z", "20261028T150000Z")
    phone.add(cal["work"], "Delete in Tahgul", "20261028T160000Z", "20261028T170000Z")
    sync(c)
    phone.delete(a, cal["work"])
    assert sync(c)["Homelab"]["deleted"] == 1
    remaining = events(c)
    assert [e["title"] for e in remaining] == ["Delete in Tahgul"]
    assert c.delete(f"{API}/events/{remaining[0]['id']}").status_code == 204
    assert phone.events(cal["work"]) == {}
    assert events(c) == []


def test_read_only_calendars_are_shown_never_changed(cal):
    c, phone = cal["c"], cal["phone"]
    phone.add(cal["shared"], "Client all-hands", "20261029T150000Z", "20261029T160000Z")
    sync(c)
    ev = events(c)[0]
    assert ev["calendar_name"] == "Client (shared)" and ev["writable"] is False
    r = c.patch(f"{API}/events/{ev['id']}", json={"title": "hacked"})
    assert r.status_code == 409 and r.json()["detail"] == "calendar_read_only"
    r = c.post(
        f"{API}/events",
        json={
            "calendar_id": cal["cals"]["Client (shared)"]["id"],
            "title": "x",
            "start_at": "2026-10-29T09:00:00",
        },
    )
    assert r.status_code == 409
    # Linking a project is a Tahgul-only note, so it's allowed on a read-only calendar.
    r = c.patch(f"{API}/events/{ev['id']}", json={"project": "Network audit"})
    assert r.status_code == 200 and r.json()["project_name"] == "Network audit"
    assert "hacked" not in list(phone.events(cal["shared"]).values())[0]


def test_recurring_events_expand(cal):
    c, phone = cal["c"], cal["phone"]
    phone.add(
        cal["work"],
        "Weekly 1:1",
        "20261005T150000Z",
        "20261005T153000Z",
        extra="RRULE:FREQ=WEEKLY;COUNT=10\r\n",
    )
    sync(c)
    evs = events(c, "2026-10-01", "2026-10-31")
    assert [e["start_at"][:10] for e in evs] == [
        "2026-10-05",
        "2026-10-12",
        "2026-10-19",
        "2026-10-26",
    ]
    assert all(e["recurring"] and not e["writable"] for e in evs)
    r = c.patch(f"{API}/events/{evs[0]['id']}", json={"title": "x"})
    assert r.status_code == 409 and r.json()["detail"] == "recurring_event_edit_on_phone"


def test_all_day_events(cal):
    c, phone = cal["c"], cal["phone"]
    uid = str(uuid.uuid4())
    body = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//P//EN\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:20261001T000000Z\r\nDTSTART;VALUE=DATE:20261030\r\n"
        "DTEND;VALUE=DATE:20261031\r\nSUMMARY:Farm show\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    phone.c.put(cal["work"] + uid + ".ics", body, None)
    sync(c)
    ev = events(c, "2026-10-30", "2026-10-30")[0]
    assert ev["all_day"] is True
    # Midnight Chicago on the 30th.
    assert ev["start_at"].startswith("2026-10-30T05:00")
    made = c.post(
        f"{API}/events",
        json={"title": "Day off", "all_day": True, "start_at": "2026-11-02T00:00:00"},
    ).json()
    data = phone.events(cal["work"])
    assert any(
        "DTSTART;VALUE=DATE:20261102" in d and "DTEND;VALUE=DATE:20261103" in d
        for d in data.values()
    ), made


# --- Event → time entry and the "log this as time?" prompt --------------------------------


def test_event_to_time_entry(cal):
    c, phone = cal["c"], cal["phone"]
    phone.add(cal["work"], "Acme workshop", "20261012T140000Z", "20261012T163000Z")
    sync(c)
    ev = events(c)[0]
    c.patch(f"{API}/events/{ev['id']}", json={"project": "Network audit"})
    # Ended and linked, so it asks: log this as time?
    pending = c.get(f"{API}/events/pending-log").json()
    # (only events that ended in the last week are offered; this one is older)
    assert pending == []
    r = c.post(f"{API}/events/{ev['id']}/log-time").json()
    e = r["entry"]
    assert (e["hours"], e["amount"], e["project_name"], e["source"]) == (
        "2.50",
        "312.50",
        "Network audit",
        "calendar",
    )
    assert r["event"]["time_entry_id"] == e["id"]
    assert c.post(f"{API}/events/{ev['id']}/log-time").status_code == 409


def test_pending_log_prompt(cal):
    c, phone = cal["c"], cal["phone"]
    start = (datetime.now(UTC) - timedelta(hours=3)).strftime("%Y%m%dT%H0000Z")
    end = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y%m%dT%H0000Z")
    phone.add(cal["work"], "Just ended", start, end)
    sync(c)
    today = datetime.now(UTC).date()
    ev = events(c, str(today - timedelta(days=1)), str(today + timedelta(days=1)))[0]
    assert c.get(f"{API}/events/pending-log").json() == []  # not linked to a project yet
    c.patch(f"{API}/events/{ev['id']}", json={"project": "Network audit"})
    pending = c.get(f"{API}/events/pending-log").json()
    assert [p["title"] for p in pending] == ["Just ended"]
    c.post(f"{API}/events/{ev['id']}/dismiss-log")
    assert c.get(f"{API}/events/pending-log").json() == []


# --- Billable mirror and task time blocks -------------------------------------------------


def test_time_entries_mirror_to_billable_calendar(cal):
    c, phone = cal["c"], cal["phone"]
    acct = c.post(
        f"{API}/calendar-accounts/{cal['account']['id']}/new-calendar", params={"name": "Billable"}
    ).json()
    billable = next(x for x in acct["calendars"] if x["name"] == "Billable")
    assert billable["writable"] and billable["mirror_time_entries"]
    billable_url = cal["base"] + "connor/tahgul-billable/"

    e = c.post(
        f"{API}/time-entries",
        json={
            "project": "Network audit",
            "start_at": "2026-10-13T09:00:00",
            "duration_minutes": 120,
            "notes": "Scan",
        },
    ).json()
    data = list(phone.events(billable_url).values())
    assert len(data) == 1 and "SUMMARY:Network audit – Scan" in data[0]
    assert "250.00 USD" in data[0].replace("\r\n ", "")
    c.patch(f"{API}/time-entries/{e['id']}", json={"notes": "Scan + report"})
    assert "Scan + report" in list(phone.events(billable_url).values())[0]
    c.delete(f"{API}/time-entries/{e['id']}")
    assert phone.events(billable_url) == {}
    # The Work calendar never got any of it.
    assert phone.events(cal["work"]) == {}


def test_task_time_block(cal):
    c, phone = cal["c"], cal["phone"]
    t = c.post(
        f"{API}/tasks",
        json={
            "project": "Network audit",
            "title": "Firewall review",
            "due_date": "2026-10-29",
            "due_time": "13:00",
            "estimate_minutes": 90,
        },
    ).json()
    r = c.post(f"{API}/tasks/{t['id']}/block")
    assert r.status_code == 200, r.text
    assert c.get(f"{API}/tasks/{t['id']}").json()["calendar_block_id"] == r.json()["id"]
    data = list(phone.events(cal["work"]).values())[0]
    assert "SUMMARY:Firewall review (#1)" in data
    assert "DTSTART:20261029T180000Z" in data and "DTEND:20261029T193000Z" in data
    c.patch(f"{API}/tasks/{t['id']}", json={"due_time": "15:00"})
    assert "DTSTART:20261029T200000Z" in list(phone.events(cal["work"]).values())[0]
    assert c.post(f"{API}/tasks/{t['id']}/block").status_code == 409
    assert c.delete(f"{API}/tasks/{t['id']}/block").status_code == 204
    assert phone.events(cal["work"]) == {}
    no_time = c.post(f"{API}/tasks", json={"project": "Network audit", "title": "x"}).json()
    assert c.post(f"{API}/tasks/{no_time['id']}/block").status_code == 422


# --- Security and failure handling --------------------------------------------------------


def test_credentials_are_encrypted_and_owner_only(cal, client, db):
    row = db.scalars(select(CalendarAccount)).one()
    assert "secret" not in row.password_enc
    assert "password" not in cal["c"].get(f"{API}/calendar-accounts").text
    token = (
        cal["c"]
        .post(f"{API}/tokens", json={"name": "agent", "scopes": ["read", "write", "admin"]})
        .json()
    )
    agent = client.__class__(client.app)
    agent.headers["Authorization"] = f"Bearer {token['token']}"
    assert agent.get(f"{API}/calendar-accounts").status_code == 403
    assert (
        agent.post(
            f"{API}/calendar-accounts",
            json={"name": "x", "url": cal["base"], "username": "a", "password": "b"},
        ).status_code
        == 403
    )


def test_unreachable_server_keeps_changes_for_later(cal, db, monkeypatch):
    from tahgul.caldav import CalDAVClient, CalDAVError

    c, phone = cal["c"], cal["phone"]
    real = CalDAVClient._request

    def down(self, *a, **kw):
        raise CalDAVError("Can't reach the calendar server: connection refused")

    monkeypatch.setattr(CalDAVClient, "_request", down)
    r = c.post(f"{API}/events", json={"title": "Offline", "start_at": "2026-10-30T09:00:00"})
    assert r.status_code == 201 and r.json()["sync_pending"] is True
    assert "error" in sync(c)["Homelab"]
    db.expire_all()
    assert db.scalars(select(CalendarAccount)).one().last_error
    # Back online: the next sync pushes it.
    monkeypatch.setattr(CalDAVClient, "_request", real)
    assert sync(c)["Homelab"]["pushed"] == 1
    assert "SUMMARY:Offline" in list(phone.events(cal["work"]).values())[0]
    assert events(c)[0]["sync_pending"] is False


def test_bad_login_is_reported(logged_in, tmp_path):
    with radicale_server(str(tmp_path / "r")) as base:
        r = logged_in.post(
            f"{API}/calendar-accounts",
            json={
                "name": "Nowhere",
                "url": "http://127.0.0.1:9/",
                "username": "a",
                "password": "b",
            },
        )
        assert r.status_code == 502 and r.json()["detail"]["code"] == "calendar_server_error"
        assert base


def test_stack_radicale_is_connected_on_first_start(logged_in, tmp_path, db, monkeypatch):
    from tahgul import calsync
    from tahgul.config import get_settings

    with radicale_server(str(tmp_path / "stack")) as base:
        s = get_settings()
        monkeypatch.setattr(s, "radicale_url", base)
        monkeypatch.setattr(s, "radicale_user", "connor")
        monkeypatch.setattr(s, "radicale_password", "pw")
        account = calsync.ensure_stack_account(db)
        assert account is not None and account.name == "Homelab"
        cals = logged_in.get(f"{API}/calendars").json()
        assert [(c["name"], c["writable"], c["is_default"]) for c in cals] == [("Work", True, True)]
        # Second run does nothing.
        assert calsync.ensure_stack_account(db) is None
        # And events can be made straight away.
        r = logged_in.post(f"{API}/events", json={"title": "Hi", "start_at": "2026-10-30T09:00:00"})
        assert r.status_code == 201 and r.json()["sync_pending"] is False
