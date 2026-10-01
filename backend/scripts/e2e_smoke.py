"""Upload a file and watch it go all the way to transcript + summary.
Run from backend/:  python scripts/e2e_smoke.py ..\\spike_audio\\clip_3min.m4a [base_url]
"""
import json, pathlib, sys, time
import httpx

audio = pathlib.Path(sys.argv[1])
base = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"

with httpx.Client(base_url=base, timeout=90) as api:
    slot = api.post("/api/recordings", json={
        "filename": audio.name, "size_bytes": audio.stat().st_size,
        "content_type": "audio/mp4", "language_code": "en-IN"}).raise_for_status().json()
    httpx.put(slot["upload_url"], content=audio.read_bytes(), headers=slot["upload_headers"], timeout=120).raise_for_status()
    api.post(f"/api/recordings/{slot['id']}/complete").raise_for_status()

    t0 = time.time()
    while True:
        d = api.get(f"/api/recordings/{slot['id']}").json()
        retrying = [c["idx"] for c in d["chunks"] if c["status"] == "pending" and c.get("text") is None]
        print(f"t={time.time() - t0:5.1f}s status={d['status']:<10} stage={d['stage']} "
              f"parts={d['chunks_done']}/{d['chunks_total']} summary={d['summary_status']}")
        if d["status"] in ("completed", "failed"):
            break
        time.sleep(2)

    print("\nerror:", d["error_code"], d["error_message"])
    print("\ntranscript:", (d["transcript"] or "")[:400], "…")
    print("\nsummary:", json.dumps(d["summary"], indent=2, ensure_ascii=False))