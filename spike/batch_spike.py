"""
Day 0 spike: how does Gnani's Batch STT behave?

Usage (from repo root, venv active):
  python spike/batch_spike.py <audio_file> <language_code> [--diarize]
  python spike/batch_spike.py --job <job_id> <label>        # resume an existing job

Flow: create job -> start -> poll status (>=10 s) -> list files -> download transcript JSON.
All calls go through paced_request(): >=1.1 s between calls + retry on 429.
"""
import json
import os
import pathlib
import random
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.vachana.ai/stt/v3/batch/jobs"
API_KEY = os.environ["GNANI_API_KEY"]
HEADERS = {"X-API-Key-ID": API_KEY}

POLL_SECONDS = 10            # Gnani docs: do not poll faster than every 10 s
MAX_WAIT_SECONDS = 20 * 60   # give up after 20 min
MIN_GAP_SECONDS = 1.1        # measured in REST spike: ~1 request/second
MAX_RETRIES = 4              # 429 backoff: 1, 2, 4, 8 s (+ jitter)

TERMINAL = {"COMPLETED", "PARTIAL_FAILURE", "FAILED", "START_FAILED", "CANCELLED"}

OUT_DIR = pathlib.Path("spike_outputs")
OUT_DIR.mkdir(exist_ok=True)

MIME_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
}

_last_call = 0.0  # time of the previous request start (perf_counter)


def paced_request(client: httpx.Client, method: str, url: str, step: str, **kwargs) -> dict:
    """Send a request no sooner than MIN_GAP_SECONDS after the previous one.
    Retry 429 with exponential backoff + jitter. Exit on any other error."""
    global _last_call
    for attempt in range(MAX_RETRIES + 1):
        wait = MIN_GAP_SECONDS - (time.perf_counter() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.perf_counter()

        response = client.request(method, url, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text": response.text[:500]}

        if response.status_code == 429 and attempt < MAX_RETRIES:
            backoff = 2 ** attempt + random.uniform(0, 0.5)
            print(f"  [{step}] 429 rate limited, retry {attempt + 1}/{MAX_RETRIES} in {backoff:.1f}s")
            time.sleep(backoff)
            continue
        if response.status_code >= 300:
            print(f"[{step}] HTTP {response.status_code}: {json.dumps(body, ensure_ascii=False)}")
            sys.exit(1)
        return body
    sys.exit(f"[{step}] still rate limited after {MAX_RETRIES} retries")


def save(obj: dict, name: str) -> None:
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved -> {path}")


def create_job(client: httpx.Client, audio: pathlib.Path, lang: str, diarize: bool) -> str:
    config = {
        "model": "gnani-prisma-v2.5",
        "language_code": lang,
        "mode": "transcribe",
        "with_diarization": diarize,
        "is_multi_channel": False,
    }
    if diarize:
        config["num_speakers"] = 2  # docs: required with diarization, max 2

    # multipart: a JSON "config" part (no filename) + the audio file
    files = {
        "config": (None, json.dumps(config), "application/json"),
        "files": (audio.name, audio.read_bytes(),
                  MIME_TYPES.get(audio.suffix.lower(), "application/octet-stream")),
    }
    created = paced_request(client, "POST", BASE_URL, "create", headers=HEADERS, files=files)
    print(f"created job {created['job_id']} status={created.get('status')}")
    return created["job_id"]


def run(client: httpx.Client, job_id: str, label: str) -> None:
    t0 = time.perf_counter()

    # Start only if the job hasn't been started yet (resumed jobs may already be done)
    job = paced_request(client, "GET", f"{BASE_URL}/{job_id}", "status", headers=HEADERS)
    if job["status"] == "CREATED":
        started = paced_request(client, "POST", f"{BASE_URL}/{job_id}/start", "start", headers=HEADERS)
        print(f"start -> {started.get('status')}")
    else:
        print(f"job already {job['status']}, skipping start")

    # Poll until a terminal status
    while job["status"] not in TERMINAL:
        time.sleep(POLL_SECONDS)
        job = paced_request(client, "GET", f"{BASE_URL}/{job_id}", "poll", headers=HEADERS)
        elapsed = time.perf_counter() - t0
        print(f"  t={elapsed:5.1f}s status={job['status']:<12} progress={job.get('progress')}")
        if elapsed > MAX_WAIT_SECONDS:
            print("gave up waiting")
            break

    total = time.perf_counter() - t0
    save(job, f"batch_job_{label}")

    # List files -> presigned transcript URLs (expire in 1 h)
    listing = paced_request(client, "GET", f"{BASE_URL}/{job_id}/files", "files", headers=HEADERS)
    save(listing, f"batch_files_{label}")

    for f in listing.get("data", []):
        print(f"file {f.get('original_path')} status={f.get('status')} error={f.get('error_message')}")
        url = f.get("transcript_url")
        if not url:
            continue
        transcript = client.get(url).json()  # presigned S3 URL: no API key, no Gnani rate limit
        save(transcript, f"batch_transcript_{label}")
        print("\n--- full_transcript ---")
        print(transcript.get("full_transcript"))
        print("\n--- first 5 segments ---")
        for seg in transcript.get("segments", [])[:5]:
            print(f"  [{seg.get('start_time')}–{seg.get('end_time')}] spk={seg.get('speaker_id')}: {seg.get('text')}")

    print(f"\nEND-TO-END (from start/resume): {total:.1f}s · final status {job.get('status')}")


if __name__ == "__main__":
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as http:
        if len(sys.argv) >= 4 and sys.argv[1] == "--job":
            run(http, sys.argv[2], sys.argv[3])
        elif len(sys.argv) >= 3:
            audio_file = pathlib.Path(sys.argv[1])
            language = sys.argv[2]
            new_job = create_job(http, audio_file, language, "--diarize" in sys.argv)
            run(http, new_job, f"{audio_file.stem}_{language}")
        else:
            print(__doc__)