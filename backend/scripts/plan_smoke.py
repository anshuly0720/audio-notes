"""Normalize -> detect pauses -> plan -> cut the 3 min clip into pause-aligned parts.
Run from backend/:  python scripts/plan_smoke.py ..\\spike_audio\\clip_3min.m4a
Then:               python ..\\spike\\chunked_spike.py ..\\spike_audio\\parts_pause en-IN   (from backend/)
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app import audio  # noqa: E402


async def main(src: str):
    out_dir = pathlib.Path(src).parent / "parts_pause"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("part_*.wav"):
        old.unlink()

    duration = await audio.probe(src)
    wav = str(out_dir / "full.wav")
    await audio.normalize(src, wav)
    silences = await audio.detect_silences(wav)
    parts = audio.plan_chunks(duration, silences)

    print(f"duration={duration:.1f}s  pauses found={len(silences)}  parts={len(parts)}")
    for i, (start, end) in enumerate(parts):
        await audio.cut(wav, str(out_dir / f"part_{i:03d}.wav"), start, end)
        print(f"  part_{i:03d}: {start:7.2f} -> {end:7.2f}  ({end - start:5.2f}s)")


asyncio.run(main(sys.argv[1]))