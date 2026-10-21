"""Tasks in Apple Reminders via CalDAV to-dos (later milestone), against a real Radicale."""

from datetime import UTC, date, datetime, timedelta

import icalendar
import pytest
from radicale_server import radicale_server

from tahgul import todosync
from tahgul.caldav import CalDAVClient

API = "/api/v1"


class Phone:
    """What the iPhone's Reminders app does, over CalDAV."""

    def __init__(self, base: str):
        self.c = CalDAVClient(base, "connor", "secret")
        self.url: str | None = None

    def todos(self) -> dict[str, dict]:
        """summary → {href, etag, todo}"""
        ch = self.c.changes(self.url, None)
        out = {}
        for href, (etag, ics) in self.c.fetch(self.url, list(ch.changed)).items():
            todo = icalendar.Calendar.from_ical(ics).walk("VTODO")[0]
            out[str(todo["summary"])] = {"href": href, "etag": etag, "todo": todo, "ics": ics}
        return out

    def edit(self, which: str, **changes):
        t = self.todos()[which]
        cal = icalendar.Calendar.from_ical(t["ics"])
        todo = cal.walk("VTODO")[0]
        for k, v in changes.items():
            if k in todo:
                del todo[k]
            if v is not None:
                todo.add(k, v)
        if "last-modified" in todo:
            del todo["last-modified"]
        todo.add("last-modified", datetime.now(UTC) + timedelta(seconds=5))
        self.c.put(t["href"], cal.to_ical().decode(), t["etag"])

    def tick(self, summary: str):
        self.edit(summary, status="COMPLETED", completed=datetime.now(UTC))

    def add(self, summary: str, due: date | None = None):
        cal = icalendar.Calendar()
        cal.add("prodid", "-//Apple Inc.//iOS//EN")
        cal.add("version", "2.0")
        t = icalendar.Todo()
        t.add("uid", f"phone-{summary}")
        t.add("summary", summary)
        t.add("dtstamp", datetime.now(UTC))
        if due:
            t.add("due", due)
        cal.add_component(t)
        self.c.put(
            self.url.rstrip("/") + f"/phone-{abs(hash(summary))}.ics", cal.to_ical().decode(), None
        )

    def delete(self, summary: str):
        t = self.todos()[summary]
        self.c.delete(t["href"], t["etag"])


@pytest.fixture
def rem(logged_in, tmp_path):
    with radicale_server(str(tmp_path / "radicale")) as base:
        c = logged_in
        account = c.post(
            f"{API}/calendar-accounts",
            json={
                "name": "Homelab",
                "kind": "radicale",
                "url": base,
                "username": "connor",
                "password": "secret",
            },
        ).json()
        client = c.post(f"{API}/clients", json={"name": "Acme"}).json()
        audit = c.post(f"{API}/projects", json={"name": "Audit", "client_id": client["id"]}).json()
        inbox = c.post(f"{API}/projects", json={"name": "Inbox"}).json()
        soon = (date.today() + timedelta(days=2)).isoformat()
        due = c.post(
            f"{API}/tasks",
            json={
                "project_id": audit["id"],
                "title": "Call Laura",
                "due_date": soon,
                "due_time": "14:00",
                "priority": "high",
            },
        ).json()
        nodue = c.post(
            f"{API}/tasks", json={"project_id": audit["id"], "title": "Someday tidy"}
        ).json()
        r = c.post(
            f"{API}/reminder-lists",
            json={"account_id": account["id"], "inbox_project_id": inbox["id"]},
        )
        assert r.status_code == 201, r.text
        lst = r.json()
        phone = Phone(base)
        phone.url = lst["url"]
        yield {
            "c": c,
            "phone": phone,
            "list": lst,
            "due": due,
            "nodue": nodue,
            "audit": audit,
            "inbox": inbox,
            "account": account,
            "soon": soon,
        }


def sync(c):
    r = c.post(f"{API}/reminder-lists/sync")
    assert r.status_code == 200, r.text
    out = r.json()
    assert not any("error" in v for v in out.values() if isinstance(v, dict)), out
    return out


def test_tasks_with_due_dates_show_on_phone(rem):
    todos = rem["phone"].todos()
    assert list(todos) == ["Call Laura"]  # no due date: not on a "due" list
    t = todos["Call Laura"]["todo"]
    assert str(t["status"]) == "NEEDS-ACTION" and int(t["priority"]) == 1
    assert str(t["uid"]) == f"task-{rem['due']['id']}@tahgul"
    assert t["due"].dt.astimezone(UTC).isoformat().startswith(rem["soon"])
    assert rem["list"]["task_count"] == 1 and rem["list"]["name"] == "Tahgul"


def test_all_open_tasks_option(rem):
    c = rem["c"]
    c.patch(f"{API}/reminder-lists/{rem['list']['id']}", json={"include": "open"})
    assert set(rem["phone"].todos()) == {"Call Laura", "Someday tidy"}


def test_finishing_in_tahgul_ticks_it_off(rem):
    c = rem["c"]
    c.patch(f"{API}/tasks/{rem['due']['id']}", json={"done": True})
    sync(c)
    t = rem["phone"].todos()["Call Laura"]["todo"]
    assert str(t["status"]) == "COMPLETED"


def test_ticking_off_on_phone_finishes_the_task(rem):
    c, phone = rem["c"], rem["phone"]
    phone.tick("Call Laura")
    assert sync(c)["Tahgul"]["pulled"] == 1
    task = c.get(f"{API}/tasks/{rem['due']['id']}").json()
    assert task["done"] and task["status"] == "Done"
    history = c.get(f"{API}/tasks/{rem['due']['id']}/history").json()
    assert any("Reminders" in str(h.get("changes")) for h in history)
    # And unticking reopens it.
    phone.edit("Call Laura", status="NEEDS-ACTION", completed=None)
    sync(c)
    assert not c.get(f"{API}/tasks/{rem['due']['id']}").json()["done"]


def test_phone_edits_title_and_due(rem):
    c, phone = rem["c"], rem["phone"]
    new_due = date.today() + timedelta(days=5)
    phone.edit("Call Laura", summary="Call Laura re: VLANs", due=new_due)
    sync(c)
    task = c.get(f"{API}/tasks/{rem['due']['id']}").json()
    assert task["title"] == "Call Laura re: VLANs" and task["due_date"] == new_due.isoformat()
    assert task["due_time"] is None
    sync(c)  # nothing bounces back
    assert set(phone.todos()) == {"Call Laura re: VLANs"}


def test_new_reminder_becomes_inbox_task(rem):
    c, phone = rem["c"], rem["phone"]
    phone.add("Buy patch cables", due=date.today() + timedelta(days=1))
    assert sync(c)["Tahgul"]["created"] == 1
    tasks = c.get(f"{API}/tasks", params={"project": rem["inbox"]["id"]}).json()
    assert [t["title"] for t in tasks] == ["Buy patch cables"]
    sync(c)
    assert len(c.get(f"{API}/tasks", params={"project": rem["inbox"]["id"]}).json()) == 1
    # Without an inbox, new reminders are left alone.
    c.patch(f"{API}/reminder-lists/{rem['list']['id']}", json={"inbox_project_id": None})
    phone.add("Personal errand")
    sync(c)
    assert "Personal errand" in phone.todos()
    assert all(t["title"] != "Personal errand" for t in c.get(f"{API}/tasks").json())


def test_deleted_on_phone_stays_off_until_task_changes(rem):
    c, phone = rem["c"], rem["phone"]
    phone.delete("Call Laura")
    sync(c)
    sync(c)
    assert phone.todos() == {}
    assert c.get(f"{API}/tasks/{rem['due']['id']}").status_code == 200  # task stays
    c.patch(f"{API}/tasks/{rem['due']['id']}", json={"title": "Call Laura (moved)"})
    sync(c)
    assert set(phone.todos()) == {"Call Laura (moved)"}


def test_removed_when_task_deleted_or_due_cleared(rem):
    c, phone = rem["c"], rem["phone"]
    second = c.post(
        f"{API}/tasks",
        json={"project_id": rem["audit"]["id"], "title": "Renew SSL", "due_date": rem["soon"]},
    ).json()
    sync(c)
    assert set(phone.todos()) == {"Call Laura", "Renew SSL"}
    c.patch(f"{API}/tasks/{second['id']}", json={"due_date": None})
    c.delete(f"{API}/tasks/{rem['due']['id']}")
    sync(c)
    assert phone.todos() == {}


def test_conflict_newest_wins(rem, db):
    c, phone = rem["c"], rem["phone"]
    c.patch(f"{API}/tasks/{rem['due']['id']}", json={"title": "Call Laura (Tahgul)"})
    phone.edit("Call Laura", summary="Call Laura (phone)")  # later: last-modified +5 s
    sync(c)
    assert c.get(f"{API}/tasks/{rem['due']['id']}").json()["title"] == "Call Laura (phone)"
    history = c.get(f"{API}/tasks/{rem['due']['id']}/history").json()
    assert any(h.get("action") == "conflict" for h in history)


def test_delete_list_removes_todos(rem):
    c, phone = rem["c"], rem["phone"]
    assert c.delete(f"{API}/reminder-lists/{rem['list']['id']}").status_code == 204
    assert phone.todos() == {}
    assert c.get(f"{API}/reminder-lists").json() == []


def test_existing_lists_offered_and_owner_only(rem, client):
    c = rem["c"]
    remote = c.get(f"{API}/calendar-accounts/{rem['account']['id']}/todo-lists").json()
    assert any(x["url"] == rem["list"]["url"] and x["in_use"] for x in remote)
    r = c.post(
        f"{API}/reminder-lists",
        json={"account_id": rem["account"]["id"], "url": rem["list"]["url"]},
    )
    assert r.status_code == 409
    tok = c.post(f"{API}/tokens", json={"name": "a", "scopes": ["read", "write", "admin"]}).json()
    agent = client.__class__(client.app)
    agent.headers["Authorization"] = f"Bearer {tok['token']}"
    assert agent.get(f"{API}/reminder-lists").status_code == 403


def test_task_changes_wake_the_pusher(rem):
    todosync._wake.clear()
    rem["c"].patch(f"{API}/tasks/{rem['nodue']['id']}", json={"priority": "low"})
    assert todosync._wake.is_set()
