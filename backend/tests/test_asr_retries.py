"""The Gnani client's retry rules, tested without the network (httpx.MockTransport)."""
import asyncio

import httpx
import pytest

from app import asr
from app.asr import AsrError, GnaniClient


def make_client(handler, monkeypatch) -> GnaniClient:
    async def no_sleep(_seconds):  # skip real backoff waits so tests run instantly
        return None

    monkeypatch.setattr(asr.asyncio, "sleep", no_sleep)
    client = GnaniClient("test-key", min_gap_seconds=0)
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "part.wav"
    path.write_bytes(b"RIFF....WAVE")
    return str(path)


def test_success_returns_normalized_and_literal_text(wav, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"transcript": "₹2,500", "output": {"literal": "two thousand five hundred rupees"}})

    result = asyncio.run(make_client(handler, monkeypatch).transcribe(wav, "en-IN"))
    assert result.text == "₹2,500"
    assert result.literal_text == "two thousand five hundred rupees"


def test_429_is_retried_then_succeeds(wav, monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, json={"detail": {"error_code": "RATE_LIMITED"}})
        return httpx.Response(200, json={"transcript": "hello"})

    retries = []

    async def on_retry(attempt, code):
        retries.append((attempt, code))

    result = asyncio.run(make_client(handler, monkeypatch).transcribe(wav, "en-IN", on_retry))
    assert result.text == "hello"
    assert len(calls) == 3
    assert retries == [(2, "ASR_RATE_LIMITED"), (3, "ASR_RATE_LIMITED")]


def test_too_long_fails_fast_without_retry(wav, monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {"type": "MAX_AUDIO_DURATION_EXCEEDED"}})

    with pytest.raises(AsrError) as err:
        asyncio.run(make_client(handler, monkeypatch).transcribe(wav, "en-IN"))
    assert err.value.code == "PART_TOO_LONG"
    assert err.value.retryable is False
    assert len(calls) == 1  # no retries for a permanent error


def test_403_is_auth_error_without_retry(wav, monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403, json={})

    with pytest.raises(AsrError) as err:
        asyncio.run(make_client(handler, monkeypatch).transcribe(wav, "en-IN"))
    assert err.value.code == "ASR_AUTH"
    assert len(calls) == 1


def test_persistent_503_gives_up_as_retryable(wav, monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(AsrError) as err:
        asyncio.run(make_client(handler, monkeypatch).transcribe(wav, "en-IN"))
    assert err.value.code == "ASR_UNAVAILABLE"
    assert err.value.retryable is True
    assert len(calls) == asr.MAX_RETRIES + 1  # first try + 4 retries