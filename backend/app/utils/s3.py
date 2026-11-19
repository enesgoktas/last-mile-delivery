from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from app.config import settings
from app.utils.compat import utcnow_naive


def build_s3_file_url(object_key: str) -> str:
    return f"https://{settings.S3_BUCKET}.s3.{settings.AWS_REGION}.amazonaws.com/{object_key}"


async def generate_presigned_upload_url(
    *,
    object_key: str,
    content_type: str,
) -> tuple[str, str, datetime]:
    """Return (upload_url, file_url, expires_at)."""
    expires_in = settings.POD_UPLOAD_URL_EXPIRES_SECONDS
    expires_at = utcnow_naive() + timedelta(seconds=expires_in)
    file_url = build_s3_file_url(object_key)

    if settings.S3_PRESIGN_DRY_RUN:
        upload_url = f"{file_url}?mock_presigned=1&expires_in={expires_in}"
        return upload_url, file_url, expires_at

    try:
        import boto3
    except Exception as exc:
        raise RuntimeError("boto3 is required when S3_PRESIGN_DRY_RUN is false") from exc

    def _generate_sync() -> str:
        client = boto3.client("s3", region_name=settings.AWS_REGION)
        return client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.S3_BUCKET,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )

    upload_url = await asyncio.to_thread(_generate_sync)
    return upload_url, file_url, expires_at
