import pytest

from app.config import settings
from app.utils import s3


def test_build_s3_file_url():
    key = "pod/order-1/stop-1/photo-abc"
    url = s3.build_s3_file_url(key)
    assert settings.S3_BUCKET in url
    assert key in url


@pytest.mark.asyncio
async def test_generate_presigned_upload_url_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "S3_PRESIGN_DRY_RUN", True)

    upload_url, file_url, expires_at = await s3.generate_presigned_upload_url(
        object_key="pod/x/y/z",
        content_type="image/jpeg",
    )
    assert "mock_presigned=1" in upload_url
    assert file_url.startswith("https://")
    assert expires_at is not None
