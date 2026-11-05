"""Organisation-scoped local or S3-compatible object storage."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from .main import Settings, settings


def _safe_filename(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:180] or "upload"


def put_object(org_id: int, object_id: str, filename: str, payload: bytes, config: Settings = settings) -> str:
    """Persist a file and return its opaque object key for database tracking."""
    key = f"{org_id}/{object_id}-{_safe_filename(filename)}"
    backend = config.storage_backend.strip().lower()
    if backend == "local":
        target = (Path(config.upload_dir).resolve() / str(org_id) / f"{object_id}{Path(filename).suffix.lower()}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return f"local:{target}"
    if backend != "s3":
        raise RuntimeError("STORAGE_BACKEND must be local or s3")
    if not config.s3_bucket or not config.s3_access_key_id or not config.s3_secret_access_key:
        raise RuntimeError("S3_BUCKET, S3_ACCESS_KEY_ID, and S3_SECRET_ACCESS_KEY are required for S3 storage")
    import boto3

    client = boto3.client(
        "s3", endpoint_url=config.s3_endpoint_url or None, region_name=config.s3_region,
        aws_access_key_id=config.s3_access_key_id, aws_secret_access_key=config.s3_secret_access_key,
    )
    put_args = {"Bucket": config.s3_bucket, "Key": key, "Body": payload}
    encryption = config.s3_server_side_encryption.strip()
    endpoint_host = urlparse(config.s3_endpoint_url).hostname or ""
    is_cloudflare_r2 = endpoint_host.endswith(".r2.cloudflarestorage.com")
    if is_cloudflare_r2 and encryption == "aws:kms":
        raise RuntimeError("Cloudflare R2 uses managed encryption at rest and does not support AWS KMS headers")
    # R2 encrypts every object at rest itself; its S3 API rejects PutObject SSE headers.
    if is_cloudflare_r2:
        encryption = ""
    if encryption:
        if encryption not in {"AES256", "aws:kms"}:
            raise RuntimeError("S3_SERVER_SIDE_ENCRYPTION must be AES256, aws:kms, or empty")
        put_args["ServerSideEncryption"] = encryption
        if encryption == "aws:kms":
            if not config.s3_sse_kms_key_id:
                raise RuntimeError("S3_SSE_KMS_KEY_ID is required when using aws:kms encryption")
            put_args["SSEKMSKeyId"] = config.s3_sse_kms_key_id
    client.put_object(**put_args)
    return f"s3:{key}"


def delete_object(storage_key: str, config: Settings = settings) -> None:
    if storage_key.startswith("local:"):
        path = Path(storage_key[6:]).resolve()
        root = Path(config.upload_dir).resolve()
        if root not in path.parents:
            raise RuntimeError("Stored local object is outside the configured upload directory")
        path.unlink(missing_ok=True)
        return
    if storage_key.startswith("s3:"):
        if not config.s3_bucket or not config.s3_access_key_id or not config.s3_secret_access_key:
            raise RuntimeError("S3 credentials are required to delete this stored object")
        import boto3

        client = boto3.client(
            "s3", endpoint_url=config.s3_endpoint_url or None, region_name=config.s3_region,
            aws_access_key_id=config.s3_access_key_id, aws_secret_access_key=config.s3_secret_access_key,
        )
        client.delete_object(Bucket=config.s3_bucket, Key=storage_key[3:])
        return
    raise RuntimeError("Stored object provider does not match current storage configuration")
