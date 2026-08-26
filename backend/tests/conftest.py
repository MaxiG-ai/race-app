"""Database fixtures.

Tests run against a real Postgres because the schema uses JSONB and a partial unique
index; SQLite would not exercise either.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, RaceCategory, User
from app.services.categories import CATEGORY_SEEDS

DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://race@/race?host=/tmp&port=5432")


@pytest.fixture(scope="session")
def engine():
    engine = create_engine(DB_URL, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no test database available: {exc}")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine) -> Session:
    """A session on its own transaction, rolled back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def categories(db: Session) -> list[RaceCategory]:
    rows = [
        RaceCategory(
            key=seed.key,
            label=seed.label,
            kind=seed.kind,
            sort_order=seed.sort_order,
            swim_min_m=seed.swim[0],
            swim_max_m=seed.swim[1],
            bike_min_m=seed.bike[0],
            bike_max_m=seed.bike[1],
            run_min_m=seed.run[0],
            run_max_m=seed.run[1],
            tolerance_pct=10.0,
            is_active=True,
        )
        for seed in CATEGORY_SEEDS
    ]
    db.add_all(rows)
    db.flush()
    return rows


@pytest.fixture
def user(db: Session) -> User:
    account = User(
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="not-a-real-hash",
        display_name="Test",
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def other_user(db: Session) -> User:
    account = User(email=f"other-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    db.add(account)
    db.flush()
    return account
