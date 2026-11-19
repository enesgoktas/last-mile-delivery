import pytest

from app.config import settings
from app.main import startup_checks


@pytest.mark.asyncio
async def test_startup_checks_fail_closed_for_production_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(settings, "TRACKING_JWT_SECRET", "tracking-secret")
    monkeypatch.setattr(settings, "AUTH_JWT_ACTIVE_KID", "auth-v1")
    monkeypatch.setattr(settings, "AUTH_JWT_PREVIOUS_KEYS", {})
    monkeypatch.setattr(settings, "TRACKING_JWT_ACTIVE_KID", "tracking-v1")
    monkeypatch.setattr(settings, "TRACKING_JWT_PREVIOUS_KEYS", {})
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "SKIP_DB_STARTUP_CHECKS", True)
    monkeypatch.setattr(settings, "S3_PRESIGN_DRY_RUN", True)
    monkeypatch.setattr(settings, "NOTIFICATION_DRY_RUN", False)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET_ENCRYPTION_KEY", "encrypted")

    with pytest.raises(RuntimeError, match="S3_PRESIGN_DRY_RUN"):
        await startup_checks()


@pytest.mark.asyncio
async def test_startup_checks_allow_dev_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(settings, "TRACKING_JWT_SECRET", None)
    monkeypatch.setattr(settings, "AUTH_JWT_ACTIVE_KID", "auth-v1")
    monkeypatch.setattr(settings, "AUTH_JWT_PREVIOUS_KEYS", {})
    monkeypatch.setattr(settings, "TRACKING_JWT_ACTIVE_KID", "tracking-v1")
    monkeypatch.setattr(settings, "TRACKING_JWT_PREVIOUS_KEYS", {})
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "SKIP_DB_STARTUP_CHECKS", True)
    monkeypatch.setattr(settings, "S3_PRESIGN_DRY_RUN", True)
    monkeypatch.setattr(settings, "NOTIFICATION_DRY_RUN", True)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET_ENCRYPTION_KEY", None)

    await startup_checks()


@pytest.mark.asyncio
async def test_startup_checks_fail_closed_for_production_shared_tracking_secret(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(settings, "TRACKING_JWT_SECRET", "test-secret")
    monkeypatch.setattr(settings, "AUTH_JWT_ACTIVE_KID", "auth-v1")
    monkeypatch.setattr(settings, "AUTH_JWT_PREVIOUS_KEYS", {})
    monkeypatch.setattr(settings, "TRACKING_JWT_ACTIVE_KID", "tracking-v1")
    monkeypatch.setattr(settings, "TRACKING_JWT_PREVIOUS_KEYS", {})
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "SKIP_DB_STARTUP_CHECKS", True)
    monkeypatch.setattr(settings, "S3_PRESIGN_DRY_RUN", False)
    monkeypatch.setattr(settings, "NOTIFICATION_DRY_RUN", False)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET_ENCRYPTION_KEY", "encrypted")

    with pytest.raises(RuntimeError, match="TRACKING_JWT_SECRET matches JWT_SECRET"):
        await startup_checks()


@pytest.mark.asyncio
async def test_startup_checks_fail_closed_when_auth_previous_keys_reuse_active_kid(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(settings, "TRACKING_JWT_SECRET", "tracking-secret")
    monkeypatch.setattr(settings, "AUTH_JWT_ACTIVE_KID", "auth-v2")
    monkeypatch.setattr(settings, "AUTH_JWT_PREVIOUS_KEYS", {"auth-v2": "old-secret"})
    monkeypatch.setattr(settings, "TRACKING_JWT_ACTIVE_KID", "tracking-v1")
    monkeypatch.setattr(settings, "TRACKING_JWT_PREVIOUS_KEYS", {})
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "SKIP_DB_STARTUP_CHECKS", True)
    monkeypatch.setattr(settings, "S3_PRESIGN_DRY_RUN", False)
    monkeypatch.setattr(settings, "NOTIFICATION_DRY_RUN", False)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET_ENCRYPTION_KEY", "encrypted")

    with pytest.raises(RuntimeError, match="AUTH_JWT_PREVIOUS_KEYS reuses AUTH_JWT_ACTIVE_KID"):
        await startup_checks()
