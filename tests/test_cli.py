from tahgul.cli import main


def test_create_owner_once(capsys):
    assert main(["create-owner", "--email", "Me@Example.com", "--password", "long enough pw"]) == 0
    assert main(["create-owner", "--email", "two@example.com", "--password", "long enough pw"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_create_owner_rejects_short_password():
    assert main(["create-owner", "--email", "me@example.com", "--password", "short"]) == 1


def test_reset_totp(db, owner):
    owner.totp_enabled = True
    owner.totp_secret = "JBSWY3DPEHPK3PXP"
    db.commit()
    assert main(["reset-totp", "--email", owner.email]) == 0
    db.refresh(owner)
    assert owner.totp_enabled is False and owner.totp_secret is None


def test_seed_demo(owner, logged_in, capsys):
    from datetime import date

    from tahgul import demo
    from tahgul.cli import main

    made = demo.seed(today=date(2026, 10, 21))
    assert made["clients"] == 3 and made["time_entries"] > 20 and made["invoice"] == "2026-001"
    c = logged_in
    summary = c.get("/api/v1/reports/summary", params={"from": "2026-09-01", "to": "2026-10-31"})
    assert float(summary.json()["totals"]["amount"]) > 1000
    assert len(c.get("/api/v1/ideas").json()) == 2
    assert c.get("/api/v1/tokens").json() == []  # the seed token is gone
    assert main(["seed-demo"]) == 1  # refuses a second time
    assert "--force" in capsys.readouterr().err
