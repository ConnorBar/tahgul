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
