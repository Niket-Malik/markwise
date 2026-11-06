"""Organisation-scoped local or S3-compatible object storage."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from .main import Settings, settings


class StoredObjectNotFound(FileNotFoundError):
    """The metadata exists but the private object is already absent."""


def _safe_filename(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:180] or "upload"


def _s3_client(config: Settings):
    if not config.s3_bucket or not config.s3_access_key_id or not config.s3_secret_access_key:
        raise RuntimeError("S3_BUCKET, S3_ACCESS_KEY_ID, and S3_SECRET_ACCESS_KEY are required for S3 storage")
    if _is_supabase_storage(config) and urlparse(config.s3_endpoint_url).scheme != "https":
        raise RuntimeError("Supabase Storage S3 endpoint must use HTTPS")
    import boto3
    from botocore.config import Config

    # Supabase requires path-style requests and Signature V4 for its S3 endpoint.
    return boto3.client(
        "s3", endpoint_url=config.s3_endpoint_url or None, region_name=config.s3_region,
        aws_access_key_id=config.s3_access_key_id, aws_secret_access_key=config.s3_secret_access_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _is_supabase_storage(config: Settings) -> bool:
    parsed = urlparse(config.s3_endpoint_url)
    host = parsed.hostname or ""
    return host.endswith(".storage.supabase.co") or (
        host.endswith(".supabase.co") and parsed.path.rstrip("/").endswith("/storage/v1/s3")
    )


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
    client = _s3_client(config)
    put_args = {"Bucket": config.s3_bucket, "Key": key, "Body": payload}
    encryption = config.s3_server_side_encryption.strip()
    endpoint_host = urlparse(config.s3_endpoint_url).hostname or ""
    is_cloudflare_r2 = endpoint_host.endswith(".r2.cloudflarestorage.com")
    is_supabase = _is_supabase_storage(config)
    if is_cloudflare_r2 and encryption == "aws:kms":
        raise RuntimeError("Cloudflare R2 uses managed encryption at rest and does not support AWS KMS headers")
    if is_supabase and encryption == "aws:kms":
        raise RuntimeError("Supabase Storage S3 does not support AWS SSE-KMS headers")
    # R2 encrypts every object at rest itself; its S3 API rejects PutObject SSE headers.
    # Supabase Storage's S3 API also rejects SSE headers; leave encryption to the service.
    if is_cloudflare_r2 or is_supabase:
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

        client = _s3_client(config)
        client.delete_object(Bucket=config.s3_bucket, Key=storage_key[3:])
        return
    raise RuntimeError("Stored object provider does not match current storage configuration")


def restore_object(storage_key: str, payload: bytes, config: Settings = settings) -> None:
    """Restore an object at its original key when a surrounding DB deletion rolls back."""
    if storage_key.startswith("local:"):
        path = Path(storage_key[6:]).resolve()
        root = Path(config.upload_dir).resolve()
        if root not in path.parents:
            raise RuntimeError("Stored local object is outside the configured upload directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return
    if storage_key.startswith("s3:"):
        client = _s3_client(config)
        key = storage_key[3:]
        put_args = {"Bucket": config.s3_bucket, "Key": key, "Body": payload}
        encryption = config.s3_server_side_encryption.strip()
        endpoint_host = urlparse(config.s3_endpoint_url).hostname or ""
        is_cloudflare_r2 = endpoint_host.endswith(".r2.cloudflarestorage.com")
        is_supabase = _is_supabase_storage(config)
        if is_cloudflare_r2 or is_supabase:
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
        return
    raise RuntimeError("Stored object provider does not match current storage configuration")


def get_object(storage_key: str, config: Settings = settings) -> bytes:
    """Read an object for server-side processing; never expose a public object URL."""
    if storage_key.startswith("local:"):
        path = Path(storage_key[6:]).resolve()
        root = Path(config.upload_dir).resolve()
        if root not in path.parents:
            raise RuntimeError("Stored local object is outside the configured upload directory")
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StoredObjectNotFound("Stored local object is missing") from exc
    if storage_key.startswith("s3:"):
        client = _s3_client(config)
        try:
            response = client.get_object(Bucket=config.s3_bucket, Key=storage_key[3:])
        except Exception as exc:
            error = getattr(exc, "response", {})
            code = error.get("Error", {}).get("Code")
            status = error.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {"NoSuchKey", "NotFound", "404"} or status == 404:
                raise StoredObjectNotFound("Stored S3 object is missing") from exc
            raise
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()
    raise RuntimeError("Stored object provider does not match current storage configuration")
