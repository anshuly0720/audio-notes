import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


# Status values live in Python enums; the DB stores plain strings.
# (Native Postgres ENUM types make every new value a migration — not worth it here.)
class RecordingStatus(str, enum.Enum):
    AWAITING_UPLOAD = "awaiting_upload"   # row created, browser still uploading to the bucket
    QUEUED = "queued"                     # upload confirmed, job waiting for the worker
    PROCESSING = "processing"             # worker is on it (see `stage`)
    COMPLETED = "completed"
    FAILED = "failed"


class Stage(str, enum.Enum):
    PROBING = "probing"           # ffprobe: is it decodable audio? how long?
    CHUNKING = "chunking"         # convert + cut into ≤30 s parts at pauses
    TRANSCRIBING = "transcribing" # parts → Gnani, one at a time
    SUMMARIZING = "summarizing"   # transcript → LLM


class ChunkStatus(str, enum.Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class JobKind(str, enum.Enum):
    TRANSCRIBE = "transcribe"
    SUMMARIZE = "summarize"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    language_code: Mapped[str] = mapped_column(String(10))
    client_ip: Mapped[str | None] = mapped_column(String(64))  # for the per-IP upload limit

    duration_sec: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default=RecordingStatus.AWAITING_UPLOAD.value, index=True)
    stage: Mapped[str | None] = mapped_column(String(32))
    chunks_done: Mapped[int] = mapped_column(Integer, default=0)
    chunks_total: Mapped[int] = mapped_column(Integer, default=0)

    transcript: Mapped[str | None] = mapped_column(Text)  # stitched, normalized (ITN) text
    summary: Mapped[dict | None] = mapped_column(JSONB)   # {title, summary, key_points, action_items}
    summary_status: Mapped[str] = mapped_column(String(32), default="pending")

    error_code: Mapped[str | None] = mapped_column(String(64))  # machine-readable, e.g. DECODE_FAILED
    error_message: Mapped[str | None] = mapped_column(Text)      # what the user sees

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="recording", cascade="all, delete-orphan", order_by="Chunk.idx"
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("recording_id", "idx", name="uq_chunk_recording_idx"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)          # order in the file: 0, 1, 2…
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)

    status: Mapped[str] = mapped_column(String(32), default=ChunkStatus.PENDING.value)
    text: Mapped[str | None] = mapped_column(Text)          # normalized (ITN) — shown to users
    literal_text: Mapped[str | None] = mapped_column(Text)  # verbatim — kept for reference
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    recording: Mapped[Recording] = relationship(back_populates="chunks")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # Index for the worker's claim query: WHERE status='queued' AND run_after<=now() ORDER BY created_at
        Index("ix_jobs_claim", "status", "run_after", "created_at"),
        # Idempotency: at most ONE active job of each kind per recording.
        # Calling /complete twice can't enqueue the same work twice.
        Index(
            "uq_jobs_active_per_kind",
            "recording_id", "kind",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.QUEUED.value)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    locked_by: Mapped[str | None] = mapped_column(String(64))           # which worker holds it
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # heartbeat; stale = worker died
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())