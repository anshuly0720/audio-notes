"""Upload a file via presigned PUT (like the browser will), check it, download it back.
Run from backend/:  python scripts/storage_smoke.py ..\\spike_audio\\clip_25s_en.m4a
"""
import pathlib
import sys
import tempfile

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app import storage  # noqa: E402

src = pathlib.Path(sys.argv[1])
key = f"smoke-test/{src.name}"
content_type = "audio/mp4"

put_url = storage.presign_put(key, content_type)
r = httpx.put(put_url, content=src.read_bytes(), headers={"Content-Type": content_type}, timeout=60)
print("PUT:", r.status_code, r.text[:200])

print("HEAD size:", storage.object_size(key), "local size:", src.stat().st_size)
print("missing key HEAD:", storage.object_size("does/not/exist"))

with tempfile.TemporaryDirectory() as d:
    out = pathlib.Path(d) / "back.m4a"
    storage.download_to(key, str(out))
    print("downloaded bytes match:", out.read_bytes() == src.read_bytes())

print("GET url (open in browser, expires 1 h):", storage.presign_get(key)[:120], "...")