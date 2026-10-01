"""Run from backend/:  python scripts/asr_smoke.py ..\\spike_audio\\parts_pause"""
import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.asr import AsrError, GnaniClient  # noqa: E402
from app.config import get_settings  # noqa: E402


async def on_retry(attempt: int, code: str):
    print(f"    retrying (try {attempt}) after {code}")


async def main(folder: str):
    s = get_settings()
    client = GnaniClient(s.gnani_api_key, s.asr_min_gap_seconds)
    parts = sorted(pathlib.Path(folder).glob("part_*.wav"))[:3]
    t0 = time.perf_counter()
    try:
        for p in parts:
            r = await client.transcribe(str(p), "en-IN", on_retry)
            print(f"{p.name}: {len(r.text.split())} words | {r.text[:70]}…")
        # a part that is too long must fail fast, not retry
        try:
            await client.transcribe(str(pathlib.Path(folder) / "full.wav"), "en-IN", on_retry)
        except AsrError as e:
            print(f"full.wav -> {e.code} retryable={e.retryable}")
    finally:
        await client.close()
    print(f"total {time.perf_counter() - t0:.1f}s")


asyncio.run(main(sys.argv[1]))