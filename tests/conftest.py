from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def db_session():
    """A fresh in-memory SQLite DB per test. Real deployments use Postgres
    (for JSON column support + production parity), but none of the logic
    under test here depends on a Postgres-specific feature.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
