"""Tests run against a real PostgreSQL database, `race_test`, migrated with Alembic.

The app commits inside requests, so isolation is by truncation between tests rather
than by rolled-back transactions.
"""

import os
import tempfile
from pathlib import Path

_BASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://race:race@localhost:5432/race")
TEST_DB_URL = _BASE_URL.rsplit("/", 1)[0] + "/race_test"
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="race-uploads-")
os.environ["SITE_MODE"] = "private"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app import access  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import _session_factory  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Owner  # noqa: E402
from app.services import weather  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
PRIVATE = FIXTURES / "private"
PASSWORD = "correct horse battery"
# Seeded by the migration; everything else is truncated between tests.
_KEEP = {"race_category", "alembic_version", "site_settings"}


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(scope="session", autouse=True)
def _database():
    admin = create_engine(_BASE_URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS race_test WITH (FORCE)"))
        conn.execute(text("CREATE DATABASE race_test"))
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    yield
    admin.dispose()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    with _session_factory()() as db:
        tables = db.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).scalars()
        names = ", ".join(f'"{t}"' for t in tables if t not in _KEEP)
        db.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        db.execute(
            text(
                "UPDATE site_settings SET site_title='Race history', athlete_name=NULL, about=NULL"
            )
        )
        db.commit()
    access._login_attempts.clear()
    monkeypatch.setattr(get_settings(), "site_mode", "private")

    def no_network(url):
        raise OSError("network disabled in tests")

    monkeypatch.setattr(weather, "fetch_json", no_network)


@pytest.fixture
def db():
    with _session_factory()() as session:
        yield session


@pytest.fixture
def public_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "site_mode", "public")


@pytest.fixture
def anon():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def owner():
    """A logged-in owner. Sends X-Requested-With like the frontend's api.js."""
    with _session_factory()() as db:
        db.add(Owner(username="owner", password_hash=access.hash_password(PASSWORD)))
        db.commit()
    client = TestClient(app, base_url="http://localhost", headers={"X-Requested-With": "fetch"})
    response = client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert response.status_code == 204
    return client


def upload(client, *names: str):
    files = [
        ("files", (n, fixture_bytes(n) if not n.startswith("/") else Path(n).read_bytes()))
        for n in names
    ]
    response = client.post("/api/uploads", files=files)
    assert response.status_code == 200, response.text
    return response.json()
