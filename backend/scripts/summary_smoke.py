"""Run from backend/:  python scripts/summary_smoke.py"""
import asyncio, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.summarizer import summarize  # noqa: E402

stitched = json.loads(pathlib.Path("../spike_outputs/chunked_en-IN.json").read_text(encoding="utf-8"))["stitched"]
print(json.dumps(asyncio.run(summarize(stitched, "en-IN")), indent=2, ensure_ascii=False))