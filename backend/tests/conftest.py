"""Shared test fixtures. Keep DB-related fixtures out of this file — a later agent
owns those; this only helps tests reach the FIT fixture files."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401  (registers every mapped class on Base.metadata)
from app.config import settings
from app.db import Base, get_db
from app.main import app

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def fixture_path(name: str) -> Path:
    """Absolute path to one of the real FIT fixtures in tests/fixtures/."""
    path = FIXTURES_DIR / name
    assert path.exists(), f"missing fixture: {path}"
    return path


# --- DB-backed test fixtures --------------------------------------------------
#
# Tests run against a real Postgres, never the dev database: `_TEST_DB_NAME` is a
# separate database on the same server as `settings.database_url`, created fresh
# for the test session and dropped afterwards. Each test then gets its own
# connection wrapped in an outer transaction + SAVEPOINT (`join_transaction_mode=
# "create_savepoint"`), rolled back at teardown — so tests can freely call
# `db.commit()` (as the app's request-scoped sessions do) without leaking state
# between tests or ever touching another test's rows.

_TEST_DB_NAME = "race_test"


def _server_url(db_name: str) -> str:
    """`settings.database_url` with its trailing database name swapped out."""
    root, _, _ = settings.database_url.rpartition("/")
    return f"{root}/{db_name}"


@pytest.fixture(scope="session")
def _test_database() -> Iterator[str]:
    """Creates `_TEST_DB_NAME` fresh (dropping any leftover copy) and the full
    schema in it via `Base.metadata.create_all` — no Alembic dependency, so tests
    aren't coupled to migration history being up to date. Drops it again at the
    end of the session."""
    admin_engine = create_engine(_server_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{_TEST_DB_NAME}"'))
    admin_engine.dispose()

    test_db_url = _server_url(_TEST_DB_NAME)
    schema_engine = create_engine(test_db_url)
    Base.metadata.create_all(schema_engine)
    schema_engine.dispose()

    yield test_db_url

    admin_engine = create_engine(_server_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}" WITH (FORCE)'))
    admin_engine.dispose()


@pytest.fixture(scope="session")
def _engine(_test_database: str):
    engine = create_engine(_test_database, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db(_engine) -> Iterator[Session]:
    """A session bound to a connection with an open outer transaction. Any
    `db.commit()` inside the test (or inside app code sharing this session via the
    `client` fixture) only closes a nested SAVEPOINT — the outer transaction stays
    open until the fixture rolls it back after the test."""
    connection = _engine.connect()
    outer_txn = connection.begin()
    session_factory = sessionmaker(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    session = session_factory()

    yield session

    session.close()
    outer_txn.rollback()
    connection.close()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    """A `TestClient` whose requests share `db` — the fixture's transactional
    rollback, not deletion, is what isolates tests from each other."""

    def _get_db_override() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db, None)
