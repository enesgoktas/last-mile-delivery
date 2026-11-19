from __future__ import annotations

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _configure_mocked_jwt_settings(request, monkeypatch):
    if request.node.get_closest_marker("live_infra"):
        return

    monkeypatch.setattr(settings, "JWT_SECRET", "integration-mocked-auth-secret-32b!")
    monkeypatch.setattr(settings, "AUTH_JWT_ACTIVE_KID", "test-auth-v1")
    monkeypatch.setattr(settings, "AUTH_JWT_PREVIOUS_KEYS", {})
    monkeypatch.setattr(settings, "TRACKING_JWT_SECRET", "integration-mocked-tracking-secret!")
    monkeypatch.setattr(settings, "TRACKING_JWT_ACTIVE_KID", "test-tracking-v1")
    monkeypatch.setattr(settings, "TRACKING_JWT_PREVIOUS_KEYS", {})
