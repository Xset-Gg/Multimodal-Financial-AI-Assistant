"""Seed a workspace with the fictional DEMO statement, processed by the real pipeline.

Usage (from backend/):  python -m scripts.seed_demo
Prints a session token; set it as the `spendlens_session` cookie on http://localhost:3020
to open the seeded workspace. (In the UI, "Load demo statement" does the same thing.)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.security import create_session  # noqa: E402
from app.db.session import db_session  # noqa: E402
from app.jobs.worker import run_pending_jobs  # noqa: E402
from app.models import Statement  # noqa: E402
from app.services.statements import create_statement  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "app" / "demo" / "demo_statement.pdf"


def main() -> None:
    with db_session() as db:
        sess, token = create_session(db)
        with DEMO.open("rb") as fh:
            stmt = create_statement(db, sess, fh, "SpendLens Demo Bank - September 2025.pdf", is_demo=True)
        statement_id = stmt.id
    print("Processing demo statement with Docling (first run downloads models)…")
    run_pending_jobs()
    with db_session() as db:
        stmt = db.get(Statement, statement_id)
        assert stmt is not None
        print(f"Statement {statement_id}: {stmt.status}" + (f" ({stmt.error_message})" if stmt.error_message else ""))
    print(f"Session token (cookie 'spendlens_session'): {token}")


if __name__ == "__main__":
    main()
