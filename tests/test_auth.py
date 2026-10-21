import time

import pyotp

from conftest import CSRF, OWNER_EMAIL, OWNER_PASSWORD


def login(client, password=OWNER_PASSWORD, code=None):
    body = {"email": OWNER_EMAIL, "password": password}
    if code:
        body["totp_code"] = code
    return client.post("/api/v1/auth/login", json=body)


def test_health_needs_no_auth(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_docs_are_served(client):
    assert client.get("/api/docs").status_code == 200
    spec = client.get("/api/openapi.json").json()
    assert "/api/v1/auth/login" in spec["paths"]


def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_sets_secure_session_cookie(client, owner):
    r = login(client)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    me = client.get("/api/v1/auth/me").json()
    assert me["kind"] == "owner" and me["email"] == OWNER_EMAIL


def test_email_is_case_insensitive(client, owner):
    r = client.post(
        "/api/v1/auth/login", json={"email": OWNER_EMAIL.upper(), "password": OWNER_PASSWORD}
    )
    assert r.status_code == 200


def test_wrong_password_and_unknown_user(client, owner):
    assert login(client, password="nope nope nope").json()["detail"] == "invalid_credentials"
    r = client.post(
        "/api/v1/auth/login", json={"email": "x@example.com", "password": "whatever123"}
    )
    assert r.status_code == 401


def test_login_is_throttled(client, owner, monkeypatch):
    from tahgul.config import get_settings

    monkeypatch.setattr(get_settings(), "login_max_failures", 3)
    for _ in range(3):
        assert login(client, password="wrong password!").status_code == 401
    # Even the right password is refused while throttled.
    assert login(client).status_code == 429


def test_logout_ends_session(logged_in):
    assert logged_in.post("/api/v1/auth/logout").status_code == 204
    assert logged_in.get("/api/v1/auth/me").status_code == 401


def test_cookie_writes_need_csrf_header(client, owner):
    login(client)
    # No X-Requested-With header: refused even with a valid session.
    assert client.post("/api/v1/auth/logout").status_code == 403
    assert client.post("/api/v1/auth/logout", headers=CSRF).status_code == 204


def test_totp_flow(logged_in):
    setup = logged_in.post("/api/v1/auth/totp/setup").json()
    totp = pyotp.TOTP(setup["secret"])
    assert setup["otpauth_uri"].startswith("otpauth://totp/")

    assert logged_in.post("/api/v1/auth/totp/enable", json={"code": "000000"}).status_code == 400
    assert logged_in.post("/api/v1/auth/totp/enable", json={"code": totp.now()}).status_code == 204
    assert logged_in.get("/api/v1/auth/me").json()["totp_enabled"] is True

    fresh = logged_in.__class__(logged_in.app)
    # Right password alone now asks for the code.
    r = login(fresh)
    assert r.status_code == 401 and r.json()["detail"] == "totp_required"
    assert login(fresh, code="123456").json()["detail"] == "invalid_totp"
    # The code used to enable is spent; the next step's code works.
    next_code = totp.at(time.time() + 30)
    assert login(fresh, code=next_code).status_code == 200
    # Replaying the same code is refused.
    assert login(fresh, code=next_code).status_code == 401


def test_password_change_signs_out_other_sessions(client, owner):
    other = client.__class__(client.app)
    login(other)
    login(client)
    client.headers.update(CSRF)
    new = "a brand new passphrase"
    r = client.post(
        "/api/v1/auth/password", json={"current_password": OWNER_PASSWORD, "new_password": new}
    )
    assert r.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401
    assert login(other, password=new).status_code == 200


def test_spa_fallback_serves_index(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tahgul.config import get_settings
    from tahgul.main import create_app

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(get_settings(), "static_dir", tmp_path)
    with TestClient(create_app()) as c:
        assert "root" in c.get("/").text
        assert "root" in c.get("/settings/tokens").text
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/api/v1/nope").status_code == 404
        assert c.get("/api/docs").status_code == 200


def test_pwa_files(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tahgul.config import get_settings
    from tahgul.main import create_app

    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "sw.js").write_text("self.addEventListener('fetch', () => {})")
    (tmp_path / "manifest.webmanifest").write_text('{"name": "Tahgul"}')
    (tmp_path / "icon-192.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(get_settings(), "static_dir", tmp_path)
    with TestClient(create_app()) as c:
        sw = c.get("/sw.js")
        assert sw.headers["cache-control"] == "no-cache"
        assert "javascript" in sw.headers["content-type"]
        m = c.get("/manifest.webmanifest")
        assert m.headers["content-type"].startswith("application/manifest+json")
        assert c.get("/icon-192.png").headers["content-type"] == "image/png"
