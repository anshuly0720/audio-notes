"""One-off: allow our frontend origins to PUT/GET the bucket directly.
Run from backend/:  python scripts/set_bucket_cors.py
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.storage import _client  # noqa: E402
from app.config import get_settings  # noqa: E402

ORIGINS = [
    "http://localhost:3000",
    "https://audio-notes-two.vercel.app",  # <- your real Vercel URL
]

_client().put_bucket_cors(
    Bucket=get_settings().s3_bucket,
    CORSConfiguration={
        "CORSRules": [{
            "AllowedOrigins": ORIGINS,
            "AllowedMethods": ["PUT", "GET", "HEAD"],
            "AllowedHeaders": ["*"],
            "ExposeHeaders": ["ETag"],
            "MaxAgeSeconds": 3600,
        }]
    },
)
print("CORS set:", _client().get_bucket_cors(Bucket=get_settings().s3_bucket)["CORSRules"])