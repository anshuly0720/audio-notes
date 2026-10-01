"""Thin wrapper around the S3-compatible bucket (Backblaze B2).

Audio never passes through our API: the browser PUTs straight to the bucket
with a presigned URL, and the worker downloads from it.

boto3 is synchronous; async callers must wrap these in asyncio.to_thread().
"""
from functools import lru_cache
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import get_settings


@lru_cache
def _client():
    s = get_settings()
    # B2 endpoints look like https://s3.<region>.backblazeb2.com
    region = urlparse(s.s3_endpoint).hostname.split(".")[1]
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_key_id,
        aws_secret_access_key=s.s3_secret,
        region_name=region,
        config=Config(signature_version="s3v4"),
    )


def presign_put(key: str, content_type: str, expires: int = 900) -> str:
    """URL the browser can PUT the file to for `expires` seconds.
    The browser MUST send the same Content-Type, or the signature won't match."""
    return _client().generate_presigned_url(
        "put_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )


def presign_get(key: str, expires: int = 3600) -> str:
    """Short-lived URL for playing the audio back in the browser."""
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key},
        ExpiresIn=expires,
    )


def object_size(key: str) -> int | None:
    """HEAD the object: its size in bytes, or None if it doesn't exist."""
    try:
        head = _client().head_object(Bucket=get_settings().s3_bucket, Key=key)
        return head["ContentLength"]
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def download_to(key: str, path: str) -> None:
    """Stream the object to a local file (never loads it all into RAM)."""
    _client().download_file(get_settings().s3_bucket, key, path)