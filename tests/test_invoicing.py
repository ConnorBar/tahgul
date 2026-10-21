"""Scopes, budgets and invoicing (milestone 7)."""

import csv
import io
from pathlib import Path

import pytest

from tahgul.config import get_settings

API = "/api/v1"


@pytest.fixture
def setup(logged_in):
    """Acme at $125/h with a Sensors project; Beta at the $100 default."""
    c = logged_in
    ws = c.get(f"{API}/workspaces").json()[0]
    c.patch(
        f"{API}/workspaces/{ws['id']}",
        json={
            "default_rate": "100",
            "business_name": "Barnsley Consulting",
            "business_address": "1 Main St\nChicago, IL 60601",
            "payment_instructions": "ACH to account 1234",
        },
    )
    acme = c.post(
        f"{API}/clients",
        json={"name": "Acme", "rate": "125", "code": "ACME", "billing_address": "9 Elm Rd"},
    ).json()
    beta = c.post(f"{API}/clients", json={"name": "Beta"}).json()
    sensors = c.post(f"{API}/projects", json={"name": "Sensors", "client_id": acme["id"]}).json()
    audit = c.post(f"{API}/projects", json={"name": "Audit", "client_id": acme["id"]}).json()
    dash = c.post(f"{API}/projects", json={"name": "Dashboard", "client_id": beta["id"]}).json()
    return {"ws": ws, "acme": acme, "beta": beta, "sensors": sensors, "audit": audit, "dash": dash}


def log(c, project, day, minutes, hour=15, **extra):
    """Log time on a September 2026 day (15:00 UTC = 10am in Chicago)."""
    body = {
        "project_id": project["id"],
        "start_at": f"2026-09-{day:02d}T{hour:02d}:00:00Z",
        "duration_minutes": minutes,
        **extra,
    }
    r = c.post(f"{API}/time-entries", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def scope(c, project, **body):
    r = c.post(f"{API}/projects/{project['id']}/scopes", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def draft(c, client, start="2026-09-01", end="2026-09-30", **extra):
    r = c.post(
        f"{API}/invoices",
        json={"client_id": client["id"], "period_start": start, "period_end": end, **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()


def token(c, *scopes):
    r = c.post(f"{API}/tokens", json={"name": "agent", "scopes": list(scopes)})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- Scopes ------------------------------------------------------------------------------


def test_scope_burn_and_creep(logged_in, setup):
    c, p = logged_in, setup["sensors"]
    s = scope(c, p, title="Phase 1", budget_hours="10", rate="150")
    assert s["type"] == "hourly" and s["burn"]["warning"] == "ok"

    e = log(c, p, 2, 480, scope_id=s["id"])
    assert e["rate"] == "150.00" and e["rate_source"] == "scope"  # scope beats client
    assert e["scope_title"] == "Phase 1"
    report = c.get(f"{API}/projects/{p['id']}/scopes").json()
    burn = report["scopes"][0]["burn"]
    assert burn["hours"] == "8.00" and burn["hours_pct"] == "80.00" and burn["warning"] == "near"
    assert report["creep"] is False

    log(c, p, 3, 180, scope_id=s["id"])
    log(c, p, 3, 60, hour=19)  # outside every scope
    report = c.get(f"{API}/projects/{p['id']}/scopes").json()
    assert report["scopes"][0]["burn"]["warning"] == "over"
    assert report["outside_scope_hours"] == "1.00" and report["creep"] is True


def test_scope_rules(logged_in, setup):
    c = logged_in
    r = c.post(
        f"{API}/projects/{setup['sensors']['id']}/scopes", json={"title": "X", "type": "fixed"}
    )
    assert r.status_code == 422  # a fixed scope needs a fee
    s = scope(c, setup["sensors"], title="Build", type="fixed", fee="5000")
    r = c.post(
        f"{API}/time-entries",
        json={
            "project_id": setup["audit"]["id"],
            "scope_id": s["id"],
            "start_at": "2026-09-02T15:00:00Z",
            "duration_minutes": 30,
        },
    )
    assert r.status_code == 422 and r.json()["detail"] == "scope_in_other_project"


def test_task_scope_flows_to_time(logged_in, setup):
    c, p = logged_in, setup["sensors"]
    s = scope(c, p, title="Phase 1")
    t = c.post(
        f"{API}/tasks", json={"project_id": p["id"], "title": "Wire up", "scope_id": s["id"]}
    )
    assert t.status_code == 201, t.text
    assert t.json()["scope_id"] == s["id"]
    e = log(c, p, 4, 30, task_id=t.json()["id"])
    assert e["scope_id"] == s["id"]


# --- Drafts ------------------------------------------------------------------------------


def test_draft_from_time(logged_in, setup):
    c, s = logged_in, setup
    ws = s["ws"]
    c.patch(f"{API}/workspaces/{ws['id']}", json={"rounding_minutes": 15, "rounding_mode": "up"})
    e1 = log(c, s["sensors"], 2, 50)  # rounds to 1.00 h
    log(c, s["sensors"], 3, 70)  # rounds to 1.25 h
    log(c, s["audit"], 4, 120)
    log(c, s["audit"], 5, 60, billable=False)  # not billed
    log(c, s["dash"], 2, 60)  # other client
    c.post(
        f"{API}/time-entries",  # outside the period
        json={
            "project_id": s["sensors"]["id"],
            "start_at": "2026-10-02T15:00:00Z",
            "duration_minutes": 60,
        },
    )

    inv = draft(c, s["acme"])
    assert inv["status"] == "draft" and inv["number"] is None
    lines = [
        (ln["description"], ln["quantity"], ln["unit_price"], ln["amount"]) for ln in inv["lines"]
    ]
    assert lines == [("Sensors", "2.25", "125.00", "281.25"), ("Audit", "2.00", "125.00", "250.00")]
    assert inv["total"] == "531.25" and inv["tax_amount"] == "0.00" and inv["tax_rate"] is None
    assert inv["due_date"] == _plus_days(inv["issue_date"], 30)
    assert inv["lines"][0]["entry_count"] == 2
    assert "Barnsley Consulting" in inv["bill_from"] and "9 Elm Rd" in inv["bill_to"]

    # Time on a draft is locked, and isn't offered to a second draft.
    r = c.patch(f"{API}/time-entries/{e1['id']}", json={"notes": "edit"})
    assert r.status_code == 409 and r.json()["detail"] == "entry_invoiced"
    assert draft(c, s["acme"])["lines"] == []

    # Grouping by entry gives one line per entry; time added later comes in on rebuild.
    inv = c.patch(f"{API}/invoices/{inv['id']}", json={"group_by": "entry"}).json()
    assert len(inv["lines"]) == 3
    log(c, s["audit"], 20, 30)
    inv = c.post(f"{API}/invoices/{inv['id']}/rebuild").json()
    assert len(inv["lines"]) == 4

    # Deleting the draft frees the time.
    assert c.delete(f"{API}/invoices/{inv['id']}").status_code == 204
    assert c.patch(f"{API}/time-entries/{e1['id']}", json={"notes": "edit"}).status_code == 200
    unbilled = c.get(f"{API}/invoices/unbilled").json()
    assert {u["client_name"] for u in unbilled} == {"Acme", "Beta"}


def _plus_days(d, n):
    from datetime import date, timedelta

    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def test_default_period_and_tax(logged_in, setup):
    c, s = logged_in, setup
    c.patch(f"{API}/clients/{s['acme']['id']}", json={"tax_rate": "6.25", "payment_terms_days": 15})
    log(c, s["sensors"], 7, 60)
    r = c.post(f"{API}/invoices", json={"client_id": s["acme"]["id"], "issue_date": "2026-10-01"})
    inv = r.json()
    assert inv["period_start"] == "2026-09-07" and inv["due_date"] == "2026-10-16"
    assert inv["subtotal"] == "125.00" and inv["tax_amount"] == "7.81" and inv["total"] == "132.81"
    inv = c.patch(f"{API}/invoices/{inv['id']}", json={"tax_rate": None}).json()
    assert inv["total"] == "125.00"


def test_manual_lines(logged_in, setup):
    c, s = logged_in, setup
    log(c, s["sensors"], 2, 60)
    inv = draft(c, s["acme"])
    inv = c.post(
        f"{API}/invoices/{inv['id']}/lines",
        json={"description": "Hardware: Raspberry Pi kit", "quantity": "2", "unit_price": "85.50"},
    ).json()
    inv = c.post(
        f"{API}/invoices/{inv['id']}/lines",
        json={"description": "Loyalty discount", "unit_price": "-25"},
    ).json()
    assert inv["total"] == "271.00"  # 125 + 171 - 25
    time_line, kit, _ = inv["lines"]
    r = c.patch(f"{API}/invoices/{inv['id']}/lines/{time_line['id']}", json={"quantity": "3"})
    assert r.status_code == 409 and r.json()["detail"] == "line_from_time"
    r = c.patch(
        f"{API}/invoices/{inv['id']}/lines/{time_line['id']}", json={"description": "Sensor work"}
    )
    assert r.json()["lines"][0]["description"] == "Sensor work"
    inv = c.patch(f"{API}/invoices/{inv['id']}/lines/{kit['id']}", json={"quantity": "1"}).json()
    assert inv["total"] == "185.50"
    # Manual lines survive a rebuild; removing a time line frees its time.
    inv = c.post(f"{API}/invoices/{inv['id']}/rebuild").json()
    assert [ln["kind"] for ln in inv["lines"]] == ["time", "manual", "manual"]
    inv = c.delete(f"{API}/invoices/{inv['id']}/lines/{inv['lines'][0]['id']}").json()
    assert inv["total"] == "60.50"
    assert c.get(f"{API}/time-entries", params={"invoiced": "false"}).json()


def test_fixed_fee_scope(logged_in, setup):
    c, s = logged_in, setup
    fx = scope(c, s["sensors"], title="Prototype", type="fixed", fee="4000")
    log(c, s["sensors"], 2, 600, scope_id=fx["id"])
    log(c, s["sensors"], 3, 60)

    inv = draft(c, s["acme"])
    assert [ln["kind"] for ln in inv["lines"]] == ["time"]  # not billed until done
    assert any("Prototype" in w for w in inv["warnings"])
    c.delete(f"{API}/invoices/{inv['id']}")

    c.patch(f"{API}/scopes/{fx['id']}", json={"status": "done"})
    inv = draft(c, s["acme"])
    fixed = next(ln for ln in inv["lines"] if ln["kind"] == "fixed")
    assert fixed["amount"] == "4000.00" and fixed["entry_count"] == 1
    assert inv["total"] == "4125.00"
    inv = c.post(f"{API}/invoices/{inv['id']}/issue").json()
    assert c.get(f"{API}/scopes/{fx['id']}").json()["invoiced_at"] is not None
    # Billed once only.
    assert draft(c, s["acme"], start="2026-09-01", end="2026-12-31")["lines"] == []
    r = c.patch(f"{API}/scopes/{fx['id']}", json={"fee": "5000"})
    assert r.status_code == 409


def test_retainer(logged_in, setup):
    c, s = logged_in, setup
    rt = scope(
        c,
        s["audit"],
        title="Support",
        type="retainer",
        fee="2000",
        included_hours="10",
        rate="140",
        start_date="2026-09-01",
    )
    log(c, s["audit"], 2, 600, scope_id=rt["id"])
    log(c, s["audit"], 9, 150, scope_id=rt["id"])
    inv = draft(c, s["acme"])
    kinds = [(ln["kind"], ln["quantity"], ln["amount"]) for ln in inv["lines"]]
    assert kinds == [("retainer", "1.00", "2000.00"), ("overage", "2.50", "350.00")]
    assert inv["lines"][0]["description"] == "Support retainer, September 2026"
    c.post(f"{API}/invoices/{inv['id']}/issue")

    # September is billed; October's fee comes on the next invoice even with no time.
    nxt = draft(c, s["acme"], start="2026-09-01", end="2026-10-31")
    assert [ln["description"] for ln in nxt["lines"]] == ["Support retainer, October 2026"]


# --- Issuing -----------------------------------------------------------------------------


def test_issue_numbers_pdf_and_lifecycle(logged_in, setup):
    c, s = logged_in, setup
    log(c, s["sensors"], 2, 60)
    log(c, s["dash"], 2, 90)
    a = draft(c, s["acme"], issue_date="2026-10-01")
    b = draft(c, s["beta"], issue_date="2026-10-02")
    empty = draft(c, s["acme"])
    assert c.post(f"{API}/invoices/{empty['id']}/issue").status_code == 422

    # Draft previews render (PDF and HTML) without using a number.
    r = c.get(f"{API}/invoices/{a['id']}/pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert "DRAFT" in c.get(f"{API}/invoices/{a['id']}/html").text

    b = c.post(f"{API}/invoices/{b['id']}/issue").json()
    a = c.post(f"{API}/invoices/{a['id']}/issue").json()
    assert (b["number"], a["number"]) == ("2026-001", "2026-002")  # issue order, no gaps
    assert a["status"] == "sent" and a["sent_at"]
    assert c.patch(f"{API}/invoices/{a['id']}", json={"notes": "x"}).status_code == 409
    assert c.delete(f"{API}/invoices/{a['id']}").status_code == 409

    pdf = c.get(f"{API}/invoices/{a['id']}/pdf", params={"download": True})
    assert pdf.content[:4] == b"%PDF" and "2026-002" in pdf.headers["content-disposition"]
    stored = Path(get_settings().data_dir) / "invoices" / "2026" / "2026-002.pdf"
    assert stored.exists()
    html = c.get(f"{API}/invoices/{a['id']}/html").text
    assert "2026-002" in html and "ACH to account 1234" in html and "$125.00" in html

    # A later address change doesn't touch the issued invoice.
    c.patch(f"{API}/clients/{s['acme']['id']}", json={"billing_address": "New Place"})
    assert "9 Elm Rd" in c.get(f"{API}/invoices/{a['id']}").json()["bill_to"]

    a = c.post(f"{API}/invoices/{a['id']}/paid", json={"paid_on": "2026-10-10"}).json()
    assert a["status"] == "paid" and a["paid_on"] == "2026-10-10"
    a = c.post(f"{API}/invoices/{a['id']}/unpaid").json()
    assert a["status"] == "sent" and a["paid_on"] is None
    totals = c.get(f"{API}/invoices/totals").json()
    assert totals["outstanding"] == "275.00" and totals["drafts"] == 1

    # Voiding keeps the number and frees the time for a fresh invoice.
    v = c.post(f"{API}/invoices/{a['id']}/void").json()
    assert v["status"] == "void" and v["number"] == "2026-002"
    again = draft(c, s["acme"], issue_date="2026-10-03")
    assert again["total"] == "125.00"
    again = c.post(f"{API}/invoices/{again['id']}/issue").json()
    assert again["number"] == "2026-003"

    listed = c.get(f"{API}/invoices", params={"status": "void"}).json()
    assert [i["number"] for i in listed] == ["2026-002"]


def test_client_code_pattern(logged_in, setup):
    c, s = logged_in, setup
    c.patch(
        f"{API}/workspaces/{s['ws']['id']}", json={"invoice_number_pattern": "{CLIENT}-{Y}-{seq:3}"}
    )
    log(c, s["sensors"], 2, 60)
    log(c, s["dash"], 2, 60)
    a = c.post(f"{API}/invoices/{draft(c, s['acme'], issue_date='2026-10-01')['id']}/issue").json()
    assert a["number"] == "ACME-2026-001"
    b = draft(c, s["beta"], issue_date="2026-10-01")
    r = c.post(f"{API}/invoices/{b['id']}/issue")
    assert r.status_code == 409 and r.json()["detail"] == "client_needs_code"
    c.patch(f"{API}/clients/{s['beta']['id']}", json={"code": "beta"})
    assert c.post(f"{API}/invoices/{b['id']}/issue").json()["number"] == "BETA-2026-001"


def test_invoice_scope_for_tokens(logged_in, setup):
    c, s = logged_in, setup
    log(c, s["sensors"], 2, 60)
    rw = token(c, "read", "write")
    assert c.get(f"{API}/invoices", headers=rw).status_code == 403
    assert (
        c.post(f"{API}/invoices", headers=rw, json={"client_id": s["acme"]["id"]}).status_code
        == 403
    )
    inv_tok = token(c, "read", "invoice")
    r = c.post(f"{API}/invoices", headers=inv_tok, json={"client_id": s["acme"]["id"]})
    assert r.status_code == 201
    inv = c.post(f"{API}/invoices/{r.json()['id']}/issue", headers=inv_tok).json()
    assert inv["number"]
    r = c.post(f"{API}/invoices/{inv['id']}/void", headers=inv_tok)
    assert r.status_code == 403  # voiding needs admin


def test_exports(logged_in, setup):
    c, s = logged_in, setup
    sc = scope(c, s["sensors"], title="Phase 1")
    log(c, s["sensors"], 2, 90, notes='Wiring, "rev B"', scope_id=sc["id"])
    log(c, s["dash"], 3, 60)
    inv = draft(c, s["acme"], issue_date="2026-10-01")
    c.post(f"{API}/invoices/{inv['id']}/issue")

    r = c.get(f"{API}/exports/time-entries.csv", params={"from": "2026-09-01", "to": "2026-09-30"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    assert len(rows) == 2
    acme = next(x for x in rows if x["client"] == "Acme")
    assert acme["date"] == "2026-09-02" and acme["start"] == "10:00" and acme["hours"] == "1.50"
    assert acme["notes"] == 'Wiring, "rev B"' and acme["scope"] == "Phase 1"
    assert acme["invoice"] == "2026-001" and acme["amount"] == "187.50"

    r = c.get(f"{API}/exports/invoices.csv", params={"year": 2026})
    rows = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    assert [(x["number"], x["total"], x["status"]) for x in rows] == [
        ("2026-001", "187.50", "sent")
    ]
