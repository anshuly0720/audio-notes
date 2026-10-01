import asyncio
import logging
import os
import socket
import time

from ..asr import GnaniClient
from ..config import get_settings
from ..db import SessionLocal
from ..models import JobKind, Recording, RecordingStatus
from . import queue
from .pipeline import PermanentFailure, run_summarize, run_transcribe

log = logging.getLogger("audio-notes.worker")


async def run_job(job, asr: GnaniClient) -> None:
    log.info("job %s (%s) for recording %s, attempt %d", job.id, job.kind, job.recording_id, job.attempts)
    try:
        async with SessionLocal() as session:
            if job.kind == JobKind.TRANSCRIBE.value:
                await run_transcribe(session, job.id, job.recording_id, asr)
            else:
                await run_summarize(session, job.recording_id)
        await queue.finish_job(job.id)
    except PermanentFailure:
        await queue.finish_job(job.id)  # the recording already shows the error; nothing to retry
    except Exception as exc:  # unexpected: retry the job with backoff, then give up visibly
        log.exception("job %s crashed", job.id)
        gave_up = await queue.fail_job(job.id, repr(exc), job.attempts, job.max_attempts)
        if gave_up:
            async with SessionLocal() as session:
                rec = await session.get(Recording, job.recording_id)
                if rec:
                    rec.status, rec.stage = RecordingStatus.FAILED.value, None
                    rec.error_code = "INTERNAL_ERROR"
                    rec.error_message = "Something went wrong while processing. Please try uploading again."
                    await session.commit()


async def worker_loop(stop: asyncio.Event) -> None:
    s = get_settings()
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    asr = GnaniClient(s.gnani_api_key, s.asr_min_gap_seconds)
    last_reap = 0.0
    log.info("worker %s started", worker_id)
    try:
        while not stop.is_set():
            try:
                if time.monotonic() - last_reap > 60:
                    await queue.requeue_stale(s.stale_job_minutes)
                    last_reap = time.monotonic()

                queue.wake.clear()  # clear BEFORE claiming, so an enqueue during the claim isn't missed
                job = await queue.claim_job(worker_id)
                if job is None:
                    try:
                        await asyncio.wait_for(queue.wake.wait(), timeout=s.idle_poll_seconds)
                    except asyncio.TimeoutError:
                        pass
                    continue
                await run_job(job, asr)
            except Exception:
                log.exception("worker loop error; backing off")
                await asyncio.sleep(5)  # e.g. DB briefly unreachable: don't spin
    finally:
        await asr.close()
        log.info("worker %s stopped", worker_id)