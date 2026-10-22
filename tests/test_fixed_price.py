"""Fixed-price work: billing a fee in parts, and invoices sent from elsewhere."""

import pytest
from test_invoicing import API, draft, log, scope
from test_invoicing import setup as setup  # noqa: F401  (fixture)

from tahgul.config import get_settings


@pytest.fixture
def eckerts(logged_in, setup):  # noqa: F811
    """A fixed-price job: $6,000 for the whole project."""
    c = logged_in
    client = c.post(
        f"{API}/clients", json={"name": "Eckerts", "billing_address": "Belleville, IL"}
    ).json()
    project = c.post(
        f"{API}/projects", json={"name": "Orchard Wi-Fi", "client_id": client["id"]}
    ).json()
    fx = scope(c, project, title="Orchard Wi-Fi", type="fixed", fee="6000")
    return {**setup, "c": c, "client": client, "project": project, "fx": fx}


def billing(c, fx):
    return c.get(f"{API}/scopes/{fx['id']}").json()["billing"]


def test_new_fixed_scope_has_nothing_billed(eckerts):
    b = eckerts["fx"]["billing"]
    assert b["fee"] == "6000.00" and b["to_invoice"] == "6000.00"
    assert b["invoiced"] == b["paid"] == b["outstanding"] == "0.00" and b["invoices"] == []
    # Hourly scopes have no fee billing.
    other = scope(eckerts["c"], eckerts["project"], title="Extras")
    assert other["billing"] is None


def test_record_invoice_sent_elsewhere_then_paid(eckerts):
    c, fx = eckerts["c"], eckerts["fx"]
    before = c.get(f"{API}/invoices/totals").json()
    r = c.post(
        f"{API}/invoices/external",
        json={"scope_id": fx["id"], "number": "1001", "issue_date": "2026-08-15", "amount": "6000"},
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["external"] and inv["number"] == "1001" and inv["status"] == "sent"
    assert inv["due_date"] == "2026-09-14"  # the workspace's 30-day terms
    assert inv["total"] == "6000.00" and inv["lines"][0]["kind"] == "fee"
    assert inv["has_pdf"] is False

    b = billing(c, fx)
    assert (b["invoiced"], b["outstanding"], b["to_invoice"]) == ("6000.00", "6000.00", "0.00")
    assert b["overdue"] == "6000.00" and b["invoices"][0]["overdue"]
    assert c.get(f"{API}/scopes/{fx['id']}").json()["invoiced_at"] is not None

    after = c.get(f"{API}/invoices/totals").json()
    assert float(after["outstanding"]) - float(before["outstanding"]) == 6000
    # Tahgul's own numbering is untouched: the next issued invoice still starts at 1.
    log(c, eckerts["dash"], 2, 60)
    own = draft(c, eckerts["beta"])
    own = c.post(f"{API}/invoices/{own['id']}/issue").json()
    assert own["number"].endswith("001") and not own["external"]

    paid = c.post(f"{API}/invoices/{inv['id']}/paid", json={"paid_on": "2026-10-20"}).json()
    assert paid["status"] == "paid"
    b = billing(c, fx)
    assert (b["paid"], b["outstanding"], b["overdue"]) == ("6000.00", "0.00", "0.00")


def test_external_without_scope_and_validation(eckerts):
    c = eckerts["c"]
    r = c.post(
        f"{API}/invoices/external",
        json={"number": "X-1", "issue_date": "2026-09-01", "amount": "100"},
    )
    assert r.status_code == 422  # needs a client or a scope
    r = c.post(
        f"{API}/invoices/external",
        json={
            "client_id": eckerts["client"]["id"],
            "number": "X-1",
            "issue_date": "2026-09-01",
            "amount": "250",
            "status": "paid",
            "paid_on": "2026-09-10",
        },
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["lines"][0]["kind"] == "manual" and rec["paid_on"] == "2026-09-10"
    # Recorded invoices have no PDF until you attach one.
    assert c.get(f"{API}/invoices/{rec['id']}/pdf").status_code == 404
    # More than the fee is refused.
    r = c.post(
        f"{API}/invoices/external",
        json={
            "scope_id": eckerts["fx"]["id"],
            "number": "X-2",
            "issue_date": "2026-09-01",
            "amount": "6000.01",
        },
    )
    assert r.status_code == 422 and r.json()["detail"] == "amount_over_fee"


def test_deposit_then_balance_when_done(eckerts):
    c, fx = eckerts["c"], eckerts["fx"]
    # 50% deposit up front, as a Tahgul invoice.
    r = c.post(f"{API}/scopes/{fx['id']}/invoice", json={"amount": "3000"})
    assert r.status_code == 201, r.text
    dep = r.json()
    assert [(ln["kind"], ln["amount"], ln["description"]) for ln in dep["lines"]] == [
        ("fee", "3000.00", "Orchard Wi-Fi: 50% of fixed fee $6,000.00")
    ]
    assert billing(c, fx)["in_draft"] == "3000.00"
    # Can't put more than what's left on another invoice.
    r = c.post(f"{API}/scopes/{fx['id']}/invoice", json={"amount": "3000.01"})
    assert r.status_code == 422
    dep = c.post(f"{API}/invoices/{dep['id']}/issue").json()
    assert dep["status"] == "sent"
    b = billing(c, fx)
    assert (b["invoiced"], b["to_invoice"]) == ("3000.00", "3000.00")
    assert c.get(f"{API}/scopes/{fx['id']}").json()["invoiced_at"] is None

    # Time on the fixed scope isn't "ready to bill" by the hour.
    log(c, eckerts["project"], 2, 300, scope_id=fx["id"])
    ready = {u["client_name"]: u for u in c.get(f"{API}/invoices/unbilled").json()}
    assert "Eckerts" not in ready
    # Once done, the balance is ready to bill and the next draft carries it.
    c.patch(f"{API}/scopes/{fx['id']}", json={"status": "done"})
    ready = {u["client_name"]: u for u in c.get(f"{API}/invoices/unbilled").json()}
    assert ready["Eckerts"]["fees"] == "3000.00" and ready["Eckerts"]["amount"] == "3000.00"
    bal = draft(c, eckerts["client"])
    assert [
        (ln["kind"], ln["amount"], ln["description"], ln["entry_count"]) for ln in bal["lines"]
    ] == [("fixed", "3000.00", "Orchard Wi-Fi (fixed fee, balance)", 1)]
    c.post(f"{API}/invoices/{bal['id']}/issue")
    b = billing(c, fx)
    assert (b["invoiced"], b["to_invoice"], len(b["invoices"])) == ("6000.00", "0.00", 2)
    assert c.get(f"{API}/scopes/{fx['id']}").json()["invoiced_at"] is not None
    assert "Eckerts" not in {u["client_name"] for u in c.get(f"{API}/invoices/unbilled").json()}


def test_rebuild_keeps_fee_line(eckerts):
    c, fx = eckerts["c"], eckerts["fx"]
    dep = c.post(f"{API}/scopes/{fx['id']}/invoice", json={"amount": "1000"}).json()
    c.patch(f"{API}/scopes/{fx['id']}", json={"status": "done"})
    rebuilt = c.post(f"{API}/invoices/{dep['id']}/rebuild").json()
    kinds = sorted((ln["kind"], ln["amount"]) for ln in rebuilt["lines"])
    # The deposit stays, and the rest of the done fee joins it: never more than the fee.
    assert kinds == [("fee", "1000.00"), ("fixed", "5000.00")]
    assert rebuilt["total"] == "6000.00"


def test_void_and_delete_free_the_fee(eckerts):
    c, fx = eckerts["c"], eckerts["fx"]
    ext = c.post(
        f"{API}/invoices/external",
        json={"scope_id": fx["id"], "number": "77", "issue_date": "2026-09-01", "amount": "6000"},
    ).json()
    assert billing(c, fx)["to_invoice"] == "0.00"
    assert c.post(f"{API}/scopes/{fx['id']}/invoice", json={}).status_code == 422
    # A mistaken record can simply be deleted (it never took a Tahgul number).
    assert c.delete(f"{API}/invoices/{ext['id']}").status_code == 204
    b = billing(c, fx)
    assert b["to_invoice"] == "6000.00" and b["invoices"] == []
    assert c.get(f"{API}/scopes/{fx['id']}").json()["invoiced_at"] is None

    ext = c.post(
        f"{API}/invoices/external",
        json={"scope_id": fx["id"], "number": "78", "issue_date": "2026-09-01", "amount": "6000"},
    ).json()
    c.post(f"{API}/invoices/{ext['id']}/void")
    assert billing(c, fx)["to_invoice"] == "6000.00"
    assert c.get(f"{API}/scopes/{fx['id']}").json()["invoiced_at"] is None
    # Issued Tahgul invoices still can't be deleted.
    own = c.post(f"{API}/scopes/{fx['id']}/invoice", json={}).json()
    c.post(f"{API}/invoices/{own['id']}/issue")
    assert c.delete(f"{API}/invoices/{own['id']}").status_code == 409
    # And a billed fixed scope can't be deleted or have its fee cut.
    assert c.delete(f"{API}/scopes/{fx['id']}").status_code == 409


def test_edit_external_and_attach_pdf(eckerts):
    c, fx = eckerts["c"], eckerts["fx"]
    ext = c.post(
        f"{API}/invoices/external",
        json={"scope_id": fx["id"], "number": "1001", "issue_date": "2026-09-01", "amount": "2000"},
    ).json()
    r = c.patch(
        f"{API}/invoices/{ext['id']}/external",
        json={"number": "1002", "amount": "6000", "due_date": "2026-10-01"},
    )
    assert r.status_code == 200, r.text
    e = r.json()
    assert (e["number"], e["total"], e["due_date"]) == ("1002", "6000.00", "2026-10-01")
    assert (
        c.patch(f"{API}/invoices/{ext['id']}/external", json={"amount": "6000.01"}).status_code
        == 422
    )
    # Only recorded invoices take these.
    small = scope(c, eckerts["project"], title="B", type="fixed", fee="10")
    own = c.post(f"{API}/scopes/{small['id']}/invoice", json={}).json()
    assert c.patch(f"{API}/invoices/{own['id']}/external", json={"number": "9"}).status_code == 409

    bad = c.post(
        f"{API}/invoices/{ext['id']}/file", files={"file": ("x.pdf", b"hello", "application/pdf")}
    )
    assert bad.status_code == 415
    pdf = b"%PDF-1.4\n%fake but fine\n"
    r = c.post(
        f"{API}/invoices/{ext['id']}/file", files={"file": ("sent.pdf", pdf, "application/pdf")}
    )
    assert r.status_code == 200 and r.json()["has_pdf"]
    got = c.get(f"{API}/invoices/{ext['id']}/pdf")
    assert got.status_code == 200 and got.content == pdf
    assert 'filename="invoice-1002.pdf"' in got.headers["content-disposition"]
    assert (get_settings().data_dir / "invoices" / "external" / f"{ext['id']}.pdf").exists()
    assert not c.delete(f"{API}/invoices/{ext['id']}/file").json()["has_pdf"]
    assert c.get(f"{API}/invoices/{ext['id']}/pdf").status_code == 404


def test_csv_marks_recorded_invoices(eckerts):
    c = eckerts["c"]
    c.post(
        f"{API}/invoices/external",
        json={
            "scope_id": eckerts["fx"]["id"],
            "number": "1001",
            "issue_date": "2026-08-15",
            "amount": "6000",
        },
    )
    text = c.get(f"{API}/exports/invoices.csv").text
    assert "1001,2026-08-15" in text and text.strip().endswith("yes")


def test_fixed_fees_overview(eckerts):
    c, fx = eckerts["c"], eckerts["fx"]
    rows = c.get(f"{API}/fixed-fees", params={"open": True}).json()
    assert [(r["title"], r["client_name"], r["billing"]["to_invoice"]) for r in rows] == [
        ("Orchard Wi-Fi", "Eckerts", "6000.00")
    ]
    ext = c.post(
        f"{API}/invoices/external",
        json={"scope_id": fx["id"], "number": "1", "issue_date": "2026-09-01", "amount": "6000"},
    ).json()
    assert len(c.get(f"{API}/fixed-fees", params={"open": True}).json()) == 1  # awaiting payment
    c.post(f"{API}/invoices/{ext['id']}/paid", json={})
    assert c.get(f"{API}/fixed-fees", params={"open": True}).json() == []
    assert len(c.get(f"{API}/fixed-fees").json()) == 1
