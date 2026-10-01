"""Transcript -> structured summary with Gemini (REST via httpx, no SDK to explain away)."""
import asyncio
import json
import logging

import httpx

from .config import get_settings

log = logging.getLogger("audio-notes.summarizer")

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_TRANSCRIPT_CHARS = 200_000  # ~3+ hours of speech; beyond this we'd need map-reduce (documented trade-off)

PROMPT = """You summarize transcripts produced by automatic speech recognition (ASR).
The transcript has no punctuation and may contain recognition errors; infer meaning carefully.
Audio language code: {lang}. Write the summary in English.

Return ONLY JSON with exactly these keys:
- "title": a short title, at most 8 words
- "summary": 2 to 4 sentences describing what the audio is about
- "key_points": 3 to 7 short strings
- "action_items": concrete tasks, decisions or deadlines that were mentioned (empty list if none)

Use only information present in the transcript. Do not invent names, numbers or facts.

Transcript:
\"\"\"{transcript}\"\"\"
"""


class SummaryError(Exception):
    pass


async def summarize(transcript: str, language_code: str) -> dict:
    """Try each configured model in order. Overload (429/5xx/timeout) -> retry once, then fall back
    to the next model. Any other error (bad key, bad request) fails immediately."""
    s = get_settings()
    if not s.gemini_api_key:
        raise SummaryError("GEMINI_API_KEY is not set")
    models = [m.strip() for m in s.gemini_models.split(",") if m.strip()]

    prompt = PROMPT.format(lang=language_code, transcript=transcript[:MAX_TRANSCRIPT_CHARS])
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
        for model in models:
            for attempt in range(2):
                try:
                    r = await client.post(API.format(model=model),
                                          headers={"x-goog-api-key": s.gemini_api_key}, json=payload)
                except httpx.HTTPError as exc:
                    log.warning("gemini %s network error/timeout (try %d): %s", model, attempt + 1, exc)
                else:
                    if r.status_code == 200:
                        log.info("summary produced by %s", model)
                        return _parse(r.json())
                    if r.status_code not in (429, 500, 502, 503, 504):
                        raise SummaryError(f"Gemini {model} HTTP {r.status_code}: {r.text[:300]}")
                    log.warning("gemini %s HTTP %d (try %d)", model, r.status_code, attempt + 1)
                await asyncio.sleep(2 + attempt * 3)
            log.warning("gemini %s overloaded, falling back to next model", model)
    raise SummaryError("All Gemini models are overloaded right now")


def _parse(body: dict) -> dict:
    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise SummaryError(f"Unexpected Gemini response: {str(body)[:300]}") from exc
    # normalize shape so the frontend can rely on it
    return {
        "title": str(data.get("title", "")).strip(),
        "summary": str(data.get("summary", "")).strip(),
        "key_points": [str(x) for x in data.get("key_points", [])][:10],
        "action_items": [str(x) for x in data.get("action_items", [])][:10],
    }