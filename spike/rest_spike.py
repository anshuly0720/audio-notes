"""
Day 0 spike: how does Gnani's REST STT behave?

Usage (from repo root, venv active):
  python spike/rest_spike.py single     <audio_file> <language_code> [verbatim|transcribe]
  python spike/rest_spike.py parallel   <audio_file> <language_code> <n_requests>
  python spike/rest_spike.py sequential <audio_file> <language_code> <n_requests> [gap_seconds]
"""
import asyncio
import json
import os
import pathlib
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()  # reads GNANI_API_KEY from .env

API_URL = "https://api.vachana.ai/stt/v3"
API_KEY = os.environ["GNANI_API_KEY"]

OUT_DIR = pathlib.Path("spike_outputs")
OUT_DIR.mkdir(exist_ok=True)

# Content-Type per extension, so Gnani knows what it's receiving
MIME_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
}

# Header prefixes that usually describe rate limits / retry timing
RATE_LIMIT_HEADER_PREFIXES = ("retry-after", "x-ratelimit", "ratelimit")

TIMEOUT = httpx.Timeout(90.0, connect=10.0)


async def transcribe(client: httpx.AsyncClient, path: str, lang: str, fmt: str = "verbatim") -> dict:
    """Send one file to Gnani and return status, latency, rate-limit headers and the parsed body."""
    audio = pathlib.Path(path)
    mime = MIME_TYPES.get(audio.suffix.lower(), "application/octet-stream")

    files = {"audio_file": (audio.name, audio.read_bytes(), mime)}
    data = {"language_code": lang, "format": fmt}
    headers = {"X-API-Key-ID": API_KEY}

    started = time.perf_counter()
    response = await client.post(API_URL, headers=headers, files=files, data=data)
    elapsed = time.perf_counter() - started

    try:
        body = response.json()
    except ValueError:  # error pages are not always JSON
        body = {"raw_text": response.text[:500]}

    # keep only headers that explain rate limits / retries
    interesting = {
        k: v for k, v in response.headers.items()
        if k.lower().startswith(RATE_LIMIT_HEADER_PREFIXES)
    }

    return {
        "file": audio.name,
        "language_code": lang,
        "format": fmt,
        "status": response.status_code,
        "seconds": round(elapsed, 2),
        "headers": interesting,
        "body": body,
    }


def save(result: dict, name: str) -> None:
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved -> {path}")


def describe(i: int, r: dict) -> str:
    """One readable line per request; show the body only when it failed."""
    detail = "ok" if r["status"] == 200 else r["body"]
    return f"#{i}: status={r['status']} seconds={r['seconds']} headers={r['headers']} body={detail}"


async def run_single(path: str, lang: str, fmt: str) -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        result = await transcribe(client, path, lang, fmt)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    save(result, f"single_{pathlib.Path(path).stem}_{lang}_{fmt}")


async def run_parallel(path: str, lang: str, n: int) -> None:
    """Fire n identical requests at once to see if we get rate-limited (429)."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        started = time.perf_counter()
        results = await asyncio.gather(
            *(transcribe(client, path, lang) for _ in range(n)),
            return_exceptions=True,
        )
        total = time.perf_counter() - started

    summary = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            print(f"#{i}: EXCEPTION {type(r).__name__}: {r}")
            summary.append({"index": i, "exception": repr(r)})
        else:
            print(describe(i, r))
            summary.append({"index": i, **r})

    ok = sum(1 for r in summary if r.get("status") == 200)
    print(f"{ok}/{n} succeeded · total wall time {total:.2f}s")
    save({"n": n, "total_seconds": round(total, 2), "results": summary}, f"parallel_{n}")


async def run_sequential(path: str, lang: str, n: int, gap: float) -> None:
    """Send n requests one after another (optional pause between) to find a safe sustained pace."""
    summary = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        started = time.perf_counter()
        for i in range(n):
            r = await transcribe(client, path, lang)
            print(describe(i, r))
            summary.append({"index": i, **r})
            if gap > 0 and i < n - 1:
                await asyncio.sleep(gap)
        total = time.perf_counter() - started

    ok = sum(1 for r in summary if r["status"] == 200)
    print(f"{ok}/{n} succeeded · gap={gap}s · total wall time {total:.2f}s")
    save({"n": n, "gap_seconds": gap, "total_seconds": round(total, 2), "results": summary},
         f"sequential_{n}_gap{gap}")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    mode, audio_path, language = sys.argv[1], sys.argv[2], sys.argv[3]

    if mode == "single":
        fmt_arg = sys.argv[4] if len(sys.argv) > 4 else "verbatim"
        asyncio.run(run_single(audio_path, language, fmt_arg))
    elif mode == "parallel":
        asyncio.run(run_parallel(audio_path, language, int(sys.argv[4])))
    elif mode == "sequential":
        gap_arg = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
        asyncio.run(run_sequential(audio_path, language, int(sys.argv[4]), gap_arg))
    else:
        print(__doc__)