"""A job queue on a plain Postgres table.

claim:   FOR UPDATE SKIP LOCKED -> concurrent workers never get the same job
heartbeat: running jobs bump locked_at; a stale locked_at means the worker died
reaper:  requeues stale jobs, so a crash or redeploy never loses work
wake:    in-process event so the embedded worker reacts instantly to new jobs
"""
import asyncio
import logging
import uuid

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import Job, JobKind, JobStatus

log = logging.getLogger("audio-notes.queue")

wake = asyncio.Event()

CLAIM_SQL = text("""
    UPDATE jobs
    SET status = 'running', locked_by = :worker, locked_at = now(),
        attempts = attempts + 1, updated_at = now()
    WHERE id = (
        SELECT id FROM jobs
        WHERE status = 'queued' AND run_after <= now()
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, recording_id, kind, attempts, max_attempts
""")

REQUEUE_STALE_SQL = text("""
    UPDATE jobs
    SET status = 'queued', locked_by = NULL, locked_at = NULL, updated_at = now()
    WHERE status = 'running' AND locked_at < now() - make_interval(mins => :mins)
    RETURNING id
""")


async def enqueue(session: AsyncSession, recording_id: uuid.UUID, kind: JobKind) -> None:
    """Add a job in the caller's transaction. Duplicate active jobs are ignored (partial unique index)."""
    session.add(Job(recording_id=recording_id, kind=kind.value))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        log.info("job %s for %s already active, skipping", kind.value, recording_id)
    wake.set()


async def claim_job(worker_id: str):
    async with SessionLocal() as s:
        row = (await s.execute(CLAIM_SQL, {"worker": worker_id})).first()
        await s.commit()
        return row  # None when the queue is empty


async def heartbeat(job_id: uuid.UUID) -> None:
    async with SessionLocal() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(locked_at=text("now()")))
        await s.commit()


async def finish_job(job_id: uuid.UUID) -> None:
    async with SessionLocal() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.DONE.value, locked_by=None))
        await s.commit()


async def fail_job(job_id: uuid.UUID, error: str, attempts: int, max_attempts: int) -> bool:
    """Requeue with backoff if attempts remain. Returns True if it gave up for good."""
    give_up = attempts >= max_attempts
    values = {"last_error": error[:2000], "locked_by": None, "locked_at": None}
    if give_up:
        values["status"] = JobStatus.FAILED.value
    else:
        values["status"] = JobStatus.QUEUED.value
        values["run_after"] = text(f"now() + interval '{30 * attempts} seconds'")
    async with SessionLocal() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(**values))
        await s.commit()
    return give_up


async def requeue_stale(minutes: int) -> int:
    async with SessionLocal() as s:
        rows = (await s.execute(REQUEUE_STALE_SQL, {"mins": minutes})).all()
        await s.commit()
    if rows:
        log.warning("requeued %d stale job(s)", len(rows))
    return len(rows)