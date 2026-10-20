from sqlalchemy import select

from ledger.models import ApiToken, AuditLog


def create(client, name="Siri", scopes=("read", "write")):
    r = client.post("/api/v1/tokens", json={"name": name, "scopes": list(scopes)})
    assert r.status_code == 201, r.text
    return r.json()


def test_token_is_shown_once_and_stored_hashed(logged_in, db):
    t = create(logged_in)
    assert t["token"].startswith(f"ldg_{t['prefix']}_")
    row = db.scalar(select(ApiToken).where(ApiToken.prefix == t["prefix"]))
    assert row.token_hash != t["token"] and t["token"] not in row.token_hash
    listed = logged_in.get("/api/v1/tokens").json()
    assert listed[0]["name"] == "Siri" and "token" not in listed[0]


def test_bearer_token_authenticates_and_tracks_last_use(logged_in, client):
    t = create(logged_in, scopes=["read"])
    bare = client.__class__(client.app)
    me = bare.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t['token']}"}).json()
    assert me == {
        "kind": "token",
        "name": "Siri",
        "email": None,
        "scopes": ["read"],
        "totp_enabled": None,
    }
    assert logged_in.get("/api/v1/tokens").json()[0]["last_used_at"] is not None


def test_tokens_cannot_manage_tokens(logged_in, client):
    t = create(logged_in, scopes=["read", "write", "invoice"])
    bare = client.__class__(client.app)
    h = {"Authorization": f"Bearer {t['token']}"}
    assert bare.get("/api/v1/tokens", headers=h).status_code == 403
    assert bare.post("/api/v1/tokens", json={"name": "x"}, headers=h).status_code == 403


def test_revoked_token_stops_working(logged_in, client):
    t = create(logged_in)
    h = {"Authorization": f"Bearer {t['token']}"}
    bare = client.__class__(client.app)
    assert bare.get("/api/v1/auth/me", headers=h).status_code == 200
    r = logged_in.delete(f"/api/v1/tokens/{t['id']}")
    assert r.status_code == 200 and r.json()["revoked_at"]
    assert bare.get("/api/v1/auth/me", headers=h).status_code == 401


def test_bad_tokens_are_rejected(client):
    for raw in ["nope", "ldg_12345678_wrong", "ldg_short_x"]:
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 401


def test_token_with_right_prefix_but_wrong_secret(logged_in, client):
    t = create(logged_in)
    forged = f"ldg_{t['prefix']}_not-the-secret"
    bare = client.__class__(client.app)
    assert (
        bare.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code
        == 401
    )


def test_scope_validation(logged_in):
    r = logged_in.post("/api/v1/tokens", json={"name": "x", "scopes": ["admin"]})
    assert r.status_code == 422
    r = logged_in.post("/api/v1/tokens", json={"name": "  ", "scopes": ["read"]})
    assert r.status_code == 422


def test_require_scope(logged_in):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from ledger.deps import require_scope

    app = FastAPI()

    @app.post("/needs-invoice")
    def needs_invoice(p=Depends(require_scope("invoice"))):
        return {"ok": True}

    read_only = create(logged_in, name="agent", scopes=["read", "write"])["token"]
    invoicer = create(logged_in, name="books", scopes=["invoice"])["token"]
    with TestClient(app) as c:
        assert (
            c.post("/needs-invoice", headers={"Authorization": f"Bearer {read_only}"}).status_code
            == 403
        )
        assert (
            c.post("/needs-invoice", headers={"Authorization": f"Bearer {invoicer}"}).status_code
            == 200
        )


def test_writes_are_audited(logged_in, db):
    t = create(logged_in, name="agent")
    logged_in.delete(f"/api/v1/tokens/{t['id']}")
    actions = [(a.action, a.entity, a.actor_type) for a in db.scalars(select(AuditLog)).all()]
    assert ("create", "api_token", "owner") in actions
    assert ("revoke", "api_token", "owner") in actions
