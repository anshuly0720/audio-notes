"""
Day 0 spike: simulate approach A — send pre-cut parts to REST one at a time, then stitch.

Usage (from repo root, venv active):
  python spike/chunked_spike.py <parts_folder> <language_code>
"""
import asyncio
import json
import pathlib
import sys
import time

import httpx

# reuse the single-request function from the REST spike
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from rest_spike import transcribe, save  # noqa: E402

MIN_GAP_SECONDS = 1.1   # measured: ~1 request/second
MAX_RETRIES = 4


async def main(folder: str, lang: str) -> None:
    parts = sorted(pathlib.Path(folder).glob("part_*.wav"))
    print(f"{len(parts)} parts")
    texts, log = [], []
    last_start = 0.0
    t0 = time.perf_counter()

    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
        for i, part in enumerate(parts):
            for attempt in range(MAX_RETRIES + 1):
                wait = MIN_GAP_SECONDS - (time.perf_counter() - last_start)
                if wait > 0:
                    await asyncio.sleep(wait)
                last_start = time.perf_counter()

                r = await transcribe(client, str(part), lang, "transcribe")
                if r["status"] == 429 and attempt < MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                    continue
                break

            text = r["body"].get("transcript", "") if r["status"] == 200 else ""
            texts.append(text)
            log.append({"part": part.name, "status": r["status"], "seconds": r["seconds"], "text": text})
            print(f"[{i + 1}/{len(parts)}] {part.name} status={r['status']} {r['seconds']}s words={len(text.split())}")

    total = time.perf_counter() - t0
    stitched = " ".join(t for t in texts if t)
    print("\n--- stitched transcript ---")
    print(stitched)
    print(f"\nEND-TO-END: {total:.1f}s for {len(parts)} parts")
    save({"parts": log, "stitched": stitched, "total_seconds": round(total, 2)}, f"chunked_{lang}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))