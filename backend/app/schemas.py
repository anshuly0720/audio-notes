"""Request and response models. Pydantic validates input and shapes output,
so the API never leaks DB internals by accident."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Gnani REST STT languages (measured + documented)
LANGUAGES = {
    "en-IN": "English", "hi-IN": "Hindi", "bn-IN": "Bengali", "gu-IN": "Gujarati",
    "kn-IN": "Kannada", "ml-IN": "Malayalam", "mr-IN": "Marathi", "pa-IN": "Punjabi",
    "ta-IN": "Tamil", "te-IN": "Telugu",
}


class CreateRecordingIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    content_type: str = Field(default="", max_length=100)
    language_code: str


class CreateRecordingOut(BaseModel):
    id: uuid.UUID
    upload_url: str
    upload_headers: dict[str, str]  # the browser must send exactly these on the PUT
    expires_in: int


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    idx: int
    start_sec: float
    end_sec: float
    status: str
    text: str | None
    attempts: int
    last_error: str | None  # e.g. "retrying (try 2) after ASR_RATE_LIMITED"


class RecordingSummaryOut(BaseModel):
    """One row in the history list."""
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    filename: str
    language_code: str
    status: str
    stage: str | None
    duration_sec: float | None
    chunks_done: int
    chunks_total: int
    error_code: str | None
    created_at: datetime


class RecordingDetailOut(RecordingSummaryOut):
    """Everything the detail page needs, in one poll."""
    size_bytes: int
    error_message: str | None
    transcript: str | None
    summary: dict | None
    summary_status: str
    completed_at: datetime | None
    chunks: list[ChunkOut]