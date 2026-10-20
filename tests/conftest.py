"""Shared fixtures.

Tests run against LEDGER_TEST_DATABASE_URL when set (CI uses Postgres), otherwise a
throwaway SQLite file, so `pytest` works on a laptop with no database running.
"""

import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="ledger-test-"))
os.environ["LEDGER_DATABASE_URL"] = os.environ.get(
    "LEDGER_TEST_DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}"
)
os.environ["LEDGER_COOKIE_SECURE"] = "false"  # TestClient talks plain HTTP
os.environ["LEDGER_STATIC_DIR"] = str(_tmp / "no-web-build")
os.environ.pop("LEDGER_OWNER_EMAIL", None)
os.environ.pop("LEDGER_OWNER_PASSWORD", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ledger.bootstrap import create_owner  # noqa: E402
from ledger.db import get_engine, get_sessionmaker  # noqa: E402
from ledger.main import create_app  # noqa: E402
from ledger.models import Base  # noqa: E402

OWNER_EMAIL = "connor@example.com"
OWNER_PASSWORD = "correct horse battery"
CSRF = {"X-Requested-With": "ledger"}


@pytest.fixture(autouse=True)
def _fresh_db():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture
def db():
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture
def owner(db):
    return create_owner(db, OWNER_EMAIL, OWNER_PASSWORD)


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def logged_in(client, owner):
    r = client.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert r.status_code == 200, r.text
    client.headers.update(CSRF)
    return client
