"""Test configuration.

Tests run against a real PostgreSQL database (``TEST_DATABASE_URL``, default: the
``spendlens_test`` database created by docker compose) so JSONB, row locking and
migrations are exercised exactly as in production. The Groq API is never called:
vision/LLM behaviour is tested with fakes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_STORAGE = Path(tempfile.mkdtemp(prefix="spendlens-test-"))
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://spendlens:spendlens@localhost:5446/spendlens_test"
)
os.environ["EMBEDDED_WORKER"] = "false"
os.environ["STORAGE_DIR"] = str(_STORAGE)
os.environ["GROQ_API_KEY"] = ""
os.environ["ENVIRONMENT"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"

from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import get_engine, reset_engine  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
DEMO_PDF = BACKEND / "app" / "demo" / "demo_statement.pdf"


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    get_settings.cache_clear()
    reset_engine()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["database_url"] = os.environ["DATABASE_URL"]
    command.upgrade(cfg, "head")
    yield
    reset_engine()
    shutil.rmtree(_STORAGE, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    with get_engine().begin() as conn:
        conn.execute(text(
            "TRUNCATE chat_messages, chat_conversations, transactions, extracted_pages, processing_events, "
            "analysis_jobs, documents, statements, audit_events, user_sessions RESTART IDENTITY CASCADE"
        ))
    for f in _STORAGE.glob("*"):
        f.unlink()


@pytest.fixture
def storage_dir() -> Path:
    return _STORAGE


def _client() -> TestClient:
    from app.main import app

    c = TestClient(app)
    c.headers["x-spendlens-client"] = "1"
    r = c.post("/api/session")
    assert r.status_code == 200
    return c


@pytest.fixture
def client() -> Iterator[TestClient]:
    with _client() as c:
        yield c


@pytest.fixture
def other_client() -> Iterator[TestClient]:
    """A second, independent browser session."""
    with _client() as c:
        yield c


@pytest.fixture
def demo_pdf() -> Path:
    return DEMO_PDF
