"""Gnani REST speech-to-text client, shaped by what we measured on Day 0:
- max 30 s per request          -> parts are cut to <= 27 s (+0.6 s padding) upstream
- ~1 request/second, no concurrency -> a lock + >= MIN_GAP between request starts
- 429 has no Retry-After header -> our own exponential backoff with jitter
- format=transcribe returns both normalized `transcript` and verbatim `output.literal`
"""
import asyncio
import logging
import pathlib
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

log = logging.getLogger("audio-notes.asr")

API_URL = "https://api.vachana.ai/stt/v3"
MAX_RETRIES = 4  # backoff 1, 2, 4, 8 s (+ jitter)


class AsrError(Exception):
    """`retryable=False` means retrying the same part won't help (bad audio, bad key)."""

    def __init__(self, code: str, message: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class AsrResult:
    text: str           # normalized (ITN): "₹2,500", "1st October 2026"
    literal_text: str   # verbatim: "two thousand five hundred rupees"


# called before each retry: (attempt_number, error_code) -> lets the UI say "retrying…"
RetryHook = Callable[[int, str], Awaitable[None]]


class GnaniClient:
    def __init__(self, api_key: str, min_gap_seconds: float = 1.1, timeout: float = 60.0):
        self._api_key = api_key
        self._min_gap = min_gap_seconds
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))
        self._lock = asyncio.Lock()   # one request in flight, ever
        self._last_start = 0.0

    async def close(self) -> None:
        await self._http.aclose()

    async def _send_once(self, wav_path: str, language: str) -> httpx.Response:
        async with self._lock:
            wait = self._min_gap - (time.monotonic() - self._last_start)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()

            path = pathlib.Path(wav_path)
            return await self._http.post(
                API_URL,
                headers={"X-API-Key-ID": self._api_key},
                files={"audio_file": (path.name, path.read_bytes(), "audio/wav")},
                data={"language_code": language, "format": "transcribe"},
            )

    async def transcribe(self, wav_path: str, language: str, on_retry: RetryHook | None = None) -> AsrResult:
        last_code = "ASR_UNAVAILABLE"
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._send_once(wav_path, language)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_code = "ASR_TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "ASR_NETWORK"
                log.warning("asr %s on attempt %d: %s", last_code, attempt + 1, exc)
            else:
                status = response.status_code
                if status == 200:
                    body = response.json()
                    text = (body.get("transcript") or "").strip()
                    literal = ((body.get("output") or {}).get("literal") or text).strip()
                    return AsrResult(text=text, literal_text=literal)

                error_type = _error_type(response)
                if status in (401, 403):
                    raise AsrError("ASR_AUTH", "The transcription service refused our request (API key or credits).", retryable=False)
                if status == 400:
                    if error_type == "MAX_AUDIO_DURATION_EXCEEDED":
                        raise AsrError("PART_TOO_LONG", "An audio part exceeded the 30 s limit.", retryable=False)
                    raise AsrError("ASR_BAD_AUDIO", "The transcription service couldn't process this part of the audio.", retryable=False)
                if status == 429:
                    last_code = "ASR_RATE_LIMITED"
                elif status >= 500:
                    last_code = "ASR_UNAVAILABLE"
                else:
                    raise AsrError("ASR_ERROR", f"Unexpected response from the transcription service ({status}).", retryable=False)
                log.warning("asr %s (HTTP %d) on attempt %d", last_code, status, attempt + 1)

            if attempt < MAX_RETRIES:
                if on_retry:
                    await on_retry(attempt + 2, last_code)  # the try we're about to make
                await asyncio.sleep(2 ** attempt + random.uniform(0, 0.5))

        raise AsrError(last_code, "The transcription service kept failing for this part. You can retry it.", retryable=True)


def _error_type(response: httpx.Response) -> str | None:
    """Gnani error bodies come in two shapes (seen in the spike):
    {"error": {"type": ...}} for 400s and {"detail": {"error_code": ...}} for 429s."""
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body.get("error"), dict):
        return body["error"].get("type")
    if isinstance(body.get("detail"), dict):
        return body["detail"].get("error_code")
    return None