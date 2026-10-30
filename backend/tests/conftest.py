import os
import sys
from pathlib import Path

# Tests run on in-memory SQLite, never on whatever database the shell points at.
os.environ["DATABASE_URL"] = "sqlite://"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db import models  # noqa: E402, F401 - registers the tables
from app.db.database import Base  # noqa: E402


@pytest.fixture(autouse=True)
def _unprompted_work_off(monkeypatch):
    """Tests opt in to the work a sync does on its own.

    Otherwise every test that ingests a report would also start investigations
    and raise alerts, which changes investigation numbering and spends the
    model budget in tests about something else. test_auto_investigate.py and
    test_alerts.py switch them back on.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "auto_investigate", False)
    monkeypatch.setattr(settings, "alerts_enabled", False)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
