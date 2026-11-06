"""Opt-in S3 connectivity probe. Uploads, downloads, then removes one temporary object."""

from __future__ import annotations

import secrets
import sys
from urllib.parse import quote, urlparse

from .main import settings
from .storage import _is_supabase_storage, _s3_client, delete_object, get_object, put_object


def main() -> int:
    if settings.storage_backend.strip().lower() != "s3":
        print("Storage check needs STORAGE_BACKEND=s3")
        return 2
    if not _is_supabase_storage(settings) or settings.s3_bucket != "markwise-assets":
        print("Storage check requires the configured Supabase endpoint and markwise-assets bucket")
        return 2

    object_id = f"connectivity-{secrets.token_hex(12)}"
    payload = secrets.token_bytes(48)
    key = f"s3:0/{object_id}-markwise-storage-check.bin"
    uploaded = False
    try:
        actual_key = put_object(0, object_id, "markwise-storage-check.bin", payload, settings)
        if actual_key != key:
            key = actual_key
        uploaded = True
        downloaded = get_object(key, settings)
        if downloaded != payload:
            raise RuntimeError("Downloaded object did not match the uploaded probe")
        endpoint_host = urlparse(settings.s3_endpoint_url).hostname or ""
        project_ref = endpoint_host.removesuffix(".storage.supabase.co").removesuffix(".supabase.co")
        public_url = (
            f"https://{project_ref}.supabase.co/storage/v1/object/public/"
            f"{quote(settings.s3_bucket, safe='')}/{quote(key[3:], safe='/')}"
        )
        import httpx

        public_response = httpx.get(public_url, timeout=15, follow_redirects=False)
        if public_response.status_code not in {400, 401, 403, 404}:
            raise RuntimeError("Temporary probe object was accessible without authentication")
        delete_object(key, settings)
        from botocore.exceptions import ClientError

        try:
            _s3_client(settings).head_object(Bucket=settings.s3_bucket, Key=key[3:])
        except ClientError as exc:
            response = exc.response.get("ResponseMetadata", {})
            error_code = exc.response.get("Error", {}).get("Code", "")
            if response.get("HTTPStatusCode") != 404 and error_code not in {"404", "NoSuchKey", "NotFound"}:
                raise
        else:
            raise RuntimeError("Temporary probe object still exists after cleanup")
        print("Storage connectivity, upload, authenticated download, private-bucket check, and cleanup passed.")
        return 0
    except Exception as exc:
        if uploaded:
            try:
                delete_object(key, settings)
            except Exception:
                print("Probe failed; cleanup also failed. Remove the temporary connectivity object from the private bucket.")
        print(f"Storage connectivity probe failed ({type(exc).__name__}); error details suppressed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
