import asyncio
import logging
import pathlib
import tempfile
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audio, storage
from ..asr import AsrError, GnaniClient
from ..config import get_settings
from ..models import Chunk, ChunkStatus, JobKind, Recording, RecordingStatus, Stage
from ..summarizer import SummaryError, summarize
from .queue import enqueue, heartbeat

log = logging.getLogger("audio-notes.pipeline")
MIN_WORDS_FOR_SUMMARY = 15


class PermanentFailure(Exception):
    """Already recorded on the recording for the user; retrying the job won't help."""


async def mark_failed(session: AsyncSession, rec: Recording, code: str, message: str) -> None:
    rec.status = RecordingStatus.FAILED.value
    rec.stage = None
    rec.error_code = code
    rec.error_message = message
    await session.commit()
    raise PermanentFailure(code)


async def run_transcribe(session: AsyncSession, job_id: uuid.UUID, recording_id: uuid.UUID, asr: GnaniClient) -> None:
    settings = get_settings()
    rec = await session.get(Recording, recording_id)
    if rec is None:
        return

    rec.status, rec.stage = RecordingStatus.PROCESSING.value, Stage.PROBING.value
    rec.error_code = rec.error_message = None
    await session.commit()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = pathlib.Path(tmp)
        src = str(tmp_dir / f"original{pathlib.Path(rec.storage_key).suffix}")
        await asyncio.to_thread(storage.download_to, rec.storage_key, src)

        # --- validate + prepare (all file problems become a clear user-facing error) ---
        try:
            duration = await audio.probe(src)
            if duration > settings.max_audio_minutes * 60:
                raise audio.AudioError("TOO_LONG", f"This demo accepts recordings up to {settings.max_audio_minutes} minutes.")
            rec.duration_sec, rec.stage = duration, Stage.CHUNKING.value
            await session.commit()

            wav = str(tmp_dir / "full.wav")
            await audio.normalize(src, wav)

            chunks = list(await session.scalars(
                select(Chunk).where(Chunk.recording_id == rec.id).order_by(Chunk.idx)))
            if not chunks:  # first run: plan the parts. A resumed run reuses the saved plan.
                plan = audio.plan_chunks(duration, await audio.detect_silences(wav))
                chunks = [Chunk(recording_id=rec.id, idx=i, start_sec=s, end_sec=e) for i, (s, e) in enumerate(plan)]
                session.add_all(chunks)
            rec.chunks_total = len(chunks)
            rec.stage = Stage.TRANSCRIBING.value
            await session.commit()
        except audio.AudioError as e:
            await mark_failed(session, rec, e.code, e.message)

        # --- transcribe part by part; every result is saved immediately ---
        for chunk in chunks:
            if chunk.status == ChunkStatus.DONE.value:
                continue  # resume: never pay for the same part twice
            part = str(tmp_dir / f"part_{chunk.idx:04d}.wav")
            await audio.cut(wav, part, chunk.start_sec, chunk.end_sec)

            async def on_retry(attempt: int, code: str, c: Chunk = chunk) -> None:
                c.attempts, c.last_error = attempt, f"retrying (try {attempt}) after {code}"
                await session.commit()  # the UI sees this on its next poll

            try:
                result = await asr.transcribe(part, rec.language_code, on_retry)
                chunk.status, chunk.text, chunk.literal_text, chunk.last_error = (
                    ChunkStatus.DONE.value, result.text, result.literal_text, None)
            except AsrError as e:
                if e.code == "ASR_AUTH":
                    await mark_failed(session, rec, e.code, e.message)
                chunk.status, chunk.last_error = ChunkStatus.FAILED.value, e.message
            chunk.attempts = max(chunk.attempts, 1)
            rec.chunks_done = sum(c.status == ChunkStatus.DONE.value for c in chunks)
            await session.commit()
            await heartbeat(job_id)
            pathlib.Path(part).unlink(missing_ok=True)

    # --- stitch ---
    done = [c for c in chunks if c.status == ChunkStatus.DONE.value]
    failed = [c for c in chunks if c.status == ChunkStatus.FAILED.value]
    if not done:
        await mark_failed(session, rec, "ASR_FAILED", "We couldn't transcribe any part of this audio. Please retry.")
    rec.transcript = " ".join(c.text for c in done if c.text).strip()
    if not rec.transcript:
        await mark_failed(session, rec, "NO_SPEECH", "No speech was detected in this recording.")
    if failed:
        rec.error_code = "PARTIAL_TRANSCRIPT"
        rec.error_message = f"{len(failed)} of {len(chunks)} parts couldn't be transcribed. You can retry them."

    rec.stage, rec.summary_status = Stage.SUMMARIZING.value, "pending"
    await enqueue(session, rec.id, JobKind.SUMMARIZE)  # commits rec + new job together


async def run_summarize(session: AsyncSession, recording_id: uuid.UUID) -> None:
    rec = await session.get(Recording, recording_id)
    if rec is None:
        return
    words = len((rec.transcript or "").split())
    if words < MIN_WORDS_FOR_SUMMARY:
        rec.summary, rec.summary_status = None, "skipped"  # too little speech to summarize meaningfully
    else:
        rec.summary_status = "running"
        await session.commit()
        try:
            rec.summary = await summarize(rec.transcript, rec.language_code)
            rec.summary_status = "done"
        except SummaryError as e:
            log.warning("summary failed for %s: %s", rec.id, e)
            rec.summary_status = "failed"  # transcript is still shown; user can retry the summary
    rec.status, rec.stage = RecordingStatus.COMPLETED.value, None
    rec.completed_at = datetime.now(timezone.utc)
    await session.commit()