"""Demo data: `tahgul seed-demo` fills an empty install with a believable few weeks.

It goes through the real API (in process, with a short-lived admin token), so rates,
boards, scopes and the audit log behave exactly as they do for real data.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from tahgul.db import get_sessionmaker
from tahgul.models import ApiToken, Client, Workspace
from tahgul.security import new_api_token, sha256


class DemoError(RuntimeError):
    pass


def _token() -> tuple[str, object]:
    raw, prefix = new_api_token()
    with get_sessionmaker()() as db:
        t = ApiToken(
            name="demo seed",
            prefix=prefix,
            token_hash=sha256(raw),
            scopes=["read", "write", "invoice", "admin"],
        )
        db.add(t)
        db.commit()
        return raw, t.id


def _drop_token(token_id) -> None:
    with get_sessionmaker()() as db:
        t = db.get(ApiToken, token_id)
        if t is not None:
            db.delete(t)
            db.commit()


def seed(force: bool = False, today: date | None = None) -> dict:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient

    from tahgul.main import create_app

    with get_sessionmaker()() as db:
        ws = db.scalar(select(Workspace).where(Workspace.deleted_at.is_(None)))
        if ws is None:
            raise DemoError("No workspace yet: run the migrations first.")
        if not force and db.scalar(select(Client.id).where(Client.deleted_at.is_(None))):
            raise DemoError("There are clients already; use --force to add demo data anyway.")
        ws_id, tz = ws.id, ZoneInfo(ws.timezone)

    raw, token_id = _token()
    try:
        # No `with`: the app's lifespan (scheduler, bootstrap) isn't needed here.
        http = TestClient(create_app(), base_url="http://demo")
        http.headers["Authorization"] = f"Bearer {raw}"
        return _fill(http, ws_id, today or datetime.now(tz).date())
    finally:
        _drop_token(token_id)


def _fill(http, ws_id, today: date) -> dict:
    def call(method: str, path: str, **kw):
        r = http.request(method, f"/api/v1{path}", **kw)
        if r.status_code >= 400:
            raise DemoError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    rnd = random.Random(42)
    call(
        "PATCH",
        f"/workspaces/{ws_id}",
        json={
            "default_rate": "110",
            "rounding_minutes": 15,
            "rounding_mode": "up",
            "business_name": "Barnsley Tech Consulting",
            "business_address": "200 W Madison St, Suite 400\nChicago, IL 60606",
            "business_email": "billing@example.com",
            "payment_instructions": "ACH or Zelle to billing@example.com. Checks payable to "
            "Barnsley Tech Consulting.",
            "invoice_footer": "Thank you for your business.",
        },
    )

    clients = {
        "acme": call(
            "POST",
            "/clients",
            json={
                "name": "Acme Farms",
                "code": "ACME",
                "rate": "125",
                "contact_email": "laura@acmefarms.example",
                "billing_address": "Acme Farms LLC\n4100 County Rd 9\nDeKalb, IL 60115",
            },
        ),
        "prairie": call(
            "POST",
            "/clients",
            json={
                "name": "Prairie Co-op",
                "code": "PRAIRIE",
                "billing_address": "Prairie Grain Cooperative\n12 Elevator St\nSycamore, IL 60178",
                "payment_terms_days": 15,
            },
        ),
        "lake": call(
            "POST",
            "/clients",
            json={
                "name": "Lakeside Dental",
                "code": "LAKE",
                "rate": "140",
                "billing_address": "Lakeside Dental Group\n88 Shore Dr\nEvanston, IL 60201",
            },
        ),
    }
    acts = {
        "travel": call("POST", "/activities", json={"name": "Travel", "rate": "60"}),
        "admin": call("POST", "/activities", json={"name": "Admin", "billable": False}),
        "onsite": call("POST", "/activities", json={"name": "On-site"}),
    }

    def project(name, client, **kw):
        return call("POST", "/projects", json={"name": name, "client_id": client["id"], **kw})

    p = {
        "audit": project("Network audit", clients["acme"], budget_hours="30", colour="#2f6db5"),
        "sensors": project("Grain bin sensors", clients["prairie"], colour="#b5762f"),
        "dash": project("Yield dashboard", clients["prairie"], rate="115", colour="#6b8e23"),
        "support": project("Office IT support", clients["lake"], colour="#8a3ea6"),
    }

    def scope(proj, **body):
        return call("POST", f"/projects/{p[proj]['id']}/scopes", json=body)

    s = {
        "phase1": scope("audit", title="Phase 1: assessment", budget_hours="16"),
        "phase2": scope("audit", title="Phase 2: remediation", budget_hours="24", status="draft"),
        "proto": scope(
            "sensors", title="Prototype (5 bins)", type="fixed", fee="4000", budget_hours="35"
        ),
        "retainer": scope(
            "support",
            title="Monthly support",
            type="retainer",
            fee="1200",
            included_hours="8",
            rate="140",
            start_date=(today.replace(day=1) - timedelta(days=62)).isoformat(),
        ),
    }

    def task(proj, title, **kw):
        return call("POST", "/tasks", json={"project_id": p[proj]["id"], "title": title, **kw})

    soon = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    tasks = [
        task(
            "audit",
            "Inventory switches and APs",
            scope_id=s["phase1"]["id"],
            status="Done",
            labels=["network"],
            estimate_minutes=240,
        ),
        task(
            "audit",
            "Firewall rule review",
            scope_id=s["phase1"]["id"],
            status="In progress",
            priority="high",
            labels=["security"],
            due_date=soon(2),
            estimate_minutes=180,
            description="Export the rule base, flag **any/any** rules and unused objects.",
        ),
        task(
            "audit",
            "Write up findings for Laura",
            scope_id=s["phase1"]["id"],
            priority="medium",
            due_date=soon(6),
            due_time="14:00",
        ),
        task("audit", "Replace closet switch", scope_id=s["phase2"]["id"], type="request"),
        task("sensors", "Order LoRa gateways", scope_id=s["proto"]["id"], status="Done"),
        task(
            "sensors",
            "Mount sensors in bins 1-5",
            scope_id=s["proto"]["id"],
            status="In progress",
            priority="high",
            due_date=soon(1),
        ),
        task("sensors", "Moisture alert thresholds", scope_id=s["proto"]["id"], type="idea"),
        task(
            "dash",
            "Fix yield chart axis",
            type="bug",
            priority="urgent",
            due_date=soon(0),
            labels=["frontend"],
        ),
        task("dash", "Field map layer", priority="low"),
        task(
            "support",
            "Printer keeps dropping off Wi-Fi",
            type="bug",
            priority="high",
            scope_id=s["retainer"]["id"],
        ),
        task(
            "support",
            "Quarterly backup restore test",
            scope_id=s["retainer"]["id"],
            due_date=soon(9),
        ),
    ]

    # About three weeks of weekdays.
    plan = [
        ("audit", "phase1", "Site survey and switch inventory", None),
        ("audit", "phase1", "Firewall rule export and review", None),
        ("sensors", "proto", "Gateway setup and bin wiring", "onsite"),
        ("dash", None, "Chart fixes and data import", None),
        ("support", "retainer", "Help desk tickets", None),
        ("audit", None, "Extra: guest Wi-Fi password reset", None),
    ]
    entries = 0
    start = today - timedelta(days=24)
    d = start
    while d < today:
        if d.weekday() < 5:
            hour = 8 + rnd.randint(0, 1)
            for proj, sc, notes, act in rnd.sample(plan, k=rnd.choice([2, 2, 3])):
                mins = rnd.choice([45, 60, 90, 120, 150, 180])
                body = {
                    "project_id": p[proj]["id"],
                    "start_at": f"{d.isoformat()}T{hour:02d}:{rnd.choice([0, 15, 30]):02d}:00",
                    "duration_minutes": mins,
                    "notes": notes,
                }
                if sc:
                    body["scope_id"] = s[sc]["id"]
                if act:
                    body["activity_id"] = acts[act]["id"]
                call("POST", "/time-entries", json=body)
                entries += 1
                hour += mins // 60 + 1
            if rnd.random() < 0.3:
                call(
                    "POST",
                    "/time-entries",
                    json={
                        "project_id": p["sensors"]["id"],
                        "activity_id": acts["travel"]["id"],
                        "start_at": f"{d.isoformat()}T07:15:00",
                        "duration_minutes": 45,
                        "notes": "Drive to Sycamore",
                    },
                )
                entries += 1
        d += timedelta(days=1)

    call(
        "POST",
        "/ideas",
        json={
            "title": "Drone surveys for grain farms",
            "description": "Multispectral flights for co-op members, per acre. Part 107 first.",
        },
    )
    call("POST", "/ideas", json={"title": "Small-office security checkup package"})

    # One issued invoice for last month's Lakeside support, so Invoices isn't empty.
    first = today.replace(day=1)
    prev_end = first - timedelta(days=1)
    inv = call(
        "POST",
        "/invoices",
        json={
            "client_id": clients["lake"]["id"],
            "period_start": prev_end.replace(day=1).isoformat(),
            "period_end": prev_end.isoformat(),
            "issue_date": first.isoformat(),
        },
    )
    issued = None
    if inv["lines"]:
        issued = call("POST", f"/invoices/{inv['id']}/issue")["number"]
    else:
        call("DELETE", f"/invoices/{inv['id']}")

    return {
        "clients": len(clients),
        "projects": len(p),
        "scopes": len(s),
        "tasks": len(tasks),
        "time_entries": entries,
        "ideas": 2,
        "invoice": issued,
    }
