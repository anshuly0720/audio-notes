"""Exercise the upload flow like the browser will.
Run from backend/:
  python scripts/api_smoke.py ..\\spike_audio\\clip_25s_en.m4a                 (local)
  python scripts/api_smoke.py ..\\spike_audio\\clip_25s_en.m4a https://audio-notes-44g4.onrender.com
"""
import json
import pathlib
import sys

import httpx

audio = pathlib.Path(sys.argv[1])
base = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"

with httpx.Client(base_url=base, timeout=90) as api:
    # 1. ask for an upload slot
    r = api.post("/api/recordings", json={
        "filename": audio.name,
        "size_bytes": audio.stat().st_size,
        "content_type": "audio/mp4",
        "language_code": "en-IN",
    })
    print("create:", r.status_code)
    r.raise_for_status()
    slot = r.json()

    # 2. upload straight to the bucket (not through our API)
    put = httpx.put(slot["upload_url"], content=audio.read_bytes(), headers=slot["upload_headers"], timeout=120)
    print("PUT to bucket:", put.status_code)

    # 3. tell the API we're done -> it verifies and enqueues
    r = api.post(f"/api/recordings/{slot['id']}/complete")
    print("complete:", r.status_code, r.json()["status"])
    r2 = api.post(f"/api/recordings/{slot['id']}/complete")
    print("complete again (idempotent):", r2.status_code, r2.json()["status"])

    # 4. read it back
    print("detail:", json.dumps(api.get(f"/api/recordings/{slot['id']}").json(), indent=2, default=str)[:600])
    print("list count:", len(api.get("/api/recordings").json()))

    # 5. error shapes
    bad = api.post("/api/recordings", json={"filename": "x.pdf", "size_bytes": 10, "content_type": "application/pdf", "language_code": "en-IN"})
    print("bad type:", bad.status_code, bad.json())
    missing = api.get("/api/recordings/00000000-0000-0000-0000-000000000000")
    print("missing:", missing.status_code, missing.json())