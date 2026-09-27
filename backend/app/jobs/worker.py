"""Database-backed background worker.

Jobs live in ``analysis_jobs`` and are claimed with ``FOR UPDATE SKIP LOCKED`` so
several workers (embedded thread or a separate ``python -m app.jobs.worker``
process) can run safely. Stale jobs from crashed workers are re-queued. The
worker also enforces the retention policy by purging expired statements.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import db_session
from app.models import AnalysisJob, JobStatus, Statement, StatementStatus, utcnow
from app.processing.pipeline import Pipeline, PipelineError
from app.services.events import emit
from app.services.statements import purge_expired

log = logging.getLogger(__name__)


def enqueue(db, statement_id: uuid.UUID) -> AnalysisJob:  # type: ignore[no-untyped-def]
    job = AnalysisJob(statement_id=statement_id, status=JobStatus.queued.value)
    db.add(job)
    db.flush()
    return job


def claim_next_job() -> uuid.UUID | None:
    with db_session() as db:
        job = db.scalar(
            select(AnalysisJob)
            .where(AnalysisJob.status == JobStatus.queued.value)
            .order_by(AnalysisJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status = JobStatus.running.value
        job.attempts += 1
        job.locked_at = utcnow()
        job.started_at = job.started_at or utcnow()
        return job.id


def requeue_stale_jobs() -> int:
    settings = get_settings()
    cutoff = utcnow() - timedelta(seconds=settings.job_stale_after_seconds)
    with db_session() as db:
        stale = db.scalars(
            select(AnalysisJob).where(AnalysisJob.status == JobStatus.running.value, AnalysisJob.locked_at < cutoff)
        ).all()
        for job in stale:
            if job.attempts >= settings.job_max_attempts:
                job.status = JobStatus.failed.value
                job.error = "Processing timed out repeatedly."
                job.finished_at = utcnow()
                stmt = db.get(Statement, job.statement_id)
                if stmt:
                    stmt.status = StatementStatus.failed.value
                    stmt.error_message = job.error
            else:
                job.status = JobStatus.queued.value
        return len(stale)


def run_job(job_id: uuid.UUID, vision_factory=None) -> bool:  # type: ignore[no-untyped-def]
    """Run one claimed job to completion. Returns True on success."""
    with db_session() as db:
        job = db.get(AnalysisJob, job_id)
        if job is None:
            return False
        statement_id = job.statement_id
        try:
            Pipeline(db, statement_id, job_id, vision_factory=vision_factory).run()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            safe = str(exc) if isinstance(exc, PipelineError) else "An internal error occurred while processing."
            if not isinstance(exc, PipelineError):
                log.exception("job %s failed", job_id)
            else:
                log.info("job %s failed: %s", job_id, type(exc).__name__)
            job = db.get(AnalysisJob, job_id)
            stmt = db.get(Statement, statement_id)
            if job:
                job.status = JobStatus.failed.value
                job.error = safe
                job.finished_at = utcnow()
            if stmt:
                stmt.status = StatementStatus.failed.value
                stmt.error_message = safe
                emit(db, statement_id, "failed", safe, level="error", job_id=job_id, commit=False)
            db.commit()
            return False
        job = db.get(AnalysisJob, job_id)
        if job:
            job.status = JobStatus.succeeded.value
            job.finished_at = utcnow()
        db.commit()
        return True


def run_pending_jobs(vision_factory=None, max_jobs: int = 100) -> int:  # type: ignore[no-untyped-def]
    """Process queued jobs synchronously (used by tests and the seed script)."""
    done = 0
    while done < max_jobs:
        job_id = claim_next_job()
        if job_id is None:
            break
        run_job(job_id, vision_factory=vision_factory)
        done += 1
    return done


class Worker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start_in_thread(self) -> None:
        self._thread = threading.Thread(target=self.loop, name="spendlens-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def loop(self) -> None:
        settings = get_settings()
        last_sweep = 0.0
        log.info("worker started")
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                if now - last_sweep > settings.retention_sweep_seconds:
                    last_sweep = now
                    requeue_stale_jobs()
                    with db_session() as db:
                        purged = purge_expired(db)
                    if purged:
                        log.info("retention: purged %d expired statement(s)", purged)
                job_id = claim_next_job()
                if job_id is None:
                    self._stop.wait(settings.worker_poll_seconds)
                    continue
                log.info("processing job %s", job_id)
                run_job(job_id)
            except Exception:  # noqa: BLE001 - keep the worker alive
                log.exception("worker loop error")
                self._stop.wait(5)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    Worker().loop()


if __name__ == "__main__":
    main()
