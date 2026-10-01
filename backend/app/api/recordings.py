import asyncio
import logging
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import storage
from ..config import get_settings
from ..db import get_session
from ..models import Job, JobKind, Recording, RecordingStatus
from ..schemas import (
    LANGUAGES, CreateRecordingIn, CreateRecordingOut, RecordingDetailOut, RecordingSummaryOut,
)

log = logging.getLogger("audio-notes.api")
router = APIRouter(prefix="/api/recordings", tags=["recordings"])

UPLOAD_URL_TTL = 900  # seconds the presigned PUT stays valid

# Browsers report audio types inconsistently (e.g. Windows m4a = audio/x-m4a),
# so we accept any audio/* plus the video containers browsers record into.
EXTRA_ALLOWED_TYPES = {"video/mp4", "video/webm"}


def api_error(status: int, code: str, message: str) -> HTTPException:
    """One error shape everywhere: {"detail": {"code": ..., "message": ...}}.
    `code` is for the frontend's logic, `message` is shown to the user."""
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def client_ip(request: Request) -> str:
    # Render sits behind a proxy; the real client is the first X-Forwarded-For entry
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def get_recording_or_404(session: AsyncSession, recording_id: uuid.UUID, with_chunks=False) -> Recording:
    query = select(Recording).where(Recording.id == recording_id)
    if with_chunks:
        query = query.options(selectinload(Recording.chunks))
    recording = (await session.execute(query)).scalar_one_or_none()
    if recording is None:
        raise api_error(404, "NOT_FOUND", "This recording doesn't exist or was deleted.")
    return recording


@router.post("", response_model=CreateRecordingOut, status_code=201)
async def create_recording(body: CreateRecordingIn, request: Request, session: AsyncSession = Depends(get_session)):
    settings = get_settings()

    # --- validate before spending anything ---
    if body.language_code not in LANGUAGES:
        raise api_error(422, "UNSUPPORTED_LANGUAGE", f"Pick one of: {', '.join(LANGUAGES.values())}.")

    content_type = body.content_type.lower()
    if not (content_type.startswith("audio/") or content_type in EXTRA_ALLOWED_TYPES):
        raise api_error(415, "UNSUPPORTED_TYPE", "That doesn't look like an audio file. Try MP3, M4A, WAV, OGG, FLAC or WebM.")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    if body.size_bytes > max_bytes:
        raise api_error(413, "FILE_TOO_LARGE", f"Files can be up to {settings.max_upload_mb} MB in this demo.")

    # --- per-IP limit (protects the free ASR credits) ---
    ip = client_ip(request)
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    recent = await session.scalar(
        select(func.count()).select_from(Recording)
        .where(Recording.client_ip == ip, Recording.created_at >= one_hour_ago)
    )
    if recent >= settings.upload_limit_per_hour:
        raise api_error(429, "UPLOAD_LIMIT", f"Demo limit: {settings.upload_limit_per_hour} uploads per hour. Try again later.")

    # --- create the row, then hand back a URL to upload straight to the bucket ---
    recording_id = uuid.uuid4()
    ext = pathlib.Path(body.filename).suffix.lower()[:10]
    key = f"recordings/{recording_id}/original{ext}"

    recording = Recording(
        id=recording_id,
        filename=body.filename,
        content_type=content_type,
        size_bytes=body.size_bytes,
        storage_key=key,
        language_code=body.language_code,
        client_ip=ip,
        status=RecordingStatus.AWAITING_UPLOAD.value,
    )
    session.add(recording)
    await session.commit()

    upload_url = storage.presign_put(key, content_type, expires=UPLOAD_URL_TTL)  # local signing, no network
    return CreateRecordingOut(
        id=recording_id,
        upload_url=upload_url,
        upload_headers={"Content-Type": content_type},
        expires_in=UPLOAD_URL_TTL,
    )


@router.post("/{recording_id}/complete", response_model=RecordingSummaryOut, status_code=202)
async def complete_upload(recording_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    recording = await get_recording_or_404(session, recording_id)

    # Idempotent: calling twice (double click, retry after timeout) doesn't enqueue twice
    if recording.status != RecordingStatus.AWAITING_UPLOAD.value:
        return recording

    # Trust the bucket, not the browser: is the file really there, and how big?
    size = await asyncio.to_thread(storage.object_size, recording.storage_key)
    if size is None:
        raise api_error(409, "UPLOAD_NOT_FOUND", "The upload didn't reach storage. Please upload the file again.")

    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if size > max_bytes:
        recording.status = RecordingStatus.FAILED.value
        recording.error_code = "FILE_TOO_LARGE"
        recording.error_message = f"Files can be up to {get_settings().max_upload_mb} MB in this demo."
        await session.commit()
        return recording

    # Status change + job row in ONE transaction: either both happen or neither
    recording.size_bytes = size
    recording.status = RecordingStatus.QUEUED.value
    session.add(Job(recording_id=recording.id, kind=JobKind.TRANSCRIBE.value))
    try:
        await session.commit()
    except IntegrityError:
        # partial unique index: an active transcribe job already exists (concurrent /complete)
        await session.rollback()
        recording = await get_recording_or_404(session, recording_id)

    log.info("recording %s queued (%d bytes)", recording.id, size)
    return recording


@router.get("", response_model=list[RecordingSummaryOut])
async def list_recordings(limit: int = 50, session: AsyncSession = Depends(get_session)):
    limit = max(1, min(limit, 100))
    rows = await session.scalars(
        select(Recording)
        .where(Recording.status != RecordingStatus.AWAITING_UPLOAD.value)  # hide abandoned uploads
        .order_by(Recording.created_at.desc())
        .limit(limit)
    )
    return list(rows)


@router.get("/{recording_id}", response_model=RecordingDetailOut)
async def get_recording(recording_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await get_recording_or_404(session, recording_id, with_chunks=True)


@router.get("/{recording_id}/audio")
async def get_audio_url(recording_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    recording = await get_recording_or_404(session, recording_id)
    return {"url": storage.presign_get(recording.storage_key, expires=3600), "expires_in": 3600}