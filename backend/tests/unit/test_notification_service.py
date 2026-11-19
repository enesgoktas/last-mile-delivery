import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.enums import NotificationChannel, PushProvider
from app.services import notification_service


def test_dedupe_key_is_stable_and_case_insensitive():
    order_id = uuid.uuid4()
    key1 = notification_service.build_notification_dedupe_key(
        channel=NotificationChannel.EMAIL,
        event_type="order.delivered",
        recipient="Customer@Example.com",
        order_id=order_id,
    )
    key2 = notification_service.build_notification_dedupe_key(
        channel=NotificationChannel.EMAIL,
        event_type="order.delivered",
        recipient="customer@example.com",
        order_id=order_id,
    )
    assert key1 == key2


def test_render_notification_content_includes_reason():
    subject, body = notification_service.render_notification_content(
        "order.failed",
        {
            "order_id": "abc-123",
            "status": "FAILED",
            "reason": "NOT_HOME",
            "timestamp": "2026-02-24T01:00:00Z",
        },
    )
    assert "FAILED".title() in subject
    assert "Reason: NOT_HOME" in body


def test_render_notification_content_includes_reschedule_link_for_failed_events():
    _, body = notification_service.render_notification_content(
        "order.failed",
        {
            "order_id": "abc-123",
            "status": "FAILED",
            "reason": "NOT_HOME",
            "reschedule_url": "https://tracking.example.com/tracking/token123",
            "timestamp": "2026-02-24T01:00:00Z",
        },
    )
    assert "Reschedule: https://tracking.example.com/tracking/token123" in body


def test_parse_optional_uuid_returns_uuid_for_valid_value():
    value = str(uuid.uuid4())
    parsed = notification_service._parse_optional_uuid(value)
    assert parsed == uuid.UUID(value)


def test_parse_optional_uuid_returns_none_for_invalid_values():
    assert notification_service._parse_optional_uuid(None) is None
    assert notification_service._parse_optional_uuid("") is None
    assert notification_service._parse_optional_uuid("not-a-uuid") is None


@pytest.mark.asyncio
async def test_should_suppress_notification_only_on_duplicate(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.seen: set[str] = set()

        async def set(self, key, value, ex, nx):
            if nx and key in self.seen:
                return False
            self.seen.add(key)
            return True

    fake = FakeRedis()

    async def fake_get_redis():
        return fake

    monkeypatch.setattr(notification_service, "get_redis", fake_get_redis)

    key = "same-key"
    first = await notification_service.should_suppress_notification(key)
    second = await notification_service.should_suppress_notification(key)
    assert first is False
    assert second is True


@pytest.mark.asyncio
async def test_send_push_dry_run_skips_network(monkeypatch):
    """When NOTIFICATION_DRY_RUN=True the function must return success without making any HTTP calls."""
    monkeypatch.setattr(notification_service.settings, "NOTIFICATION_DRY_RUN", True)

    result = await notification_service._send_push(
        "device-token-xyz", "Hello", "Body text", {"order_id": "abc"}, provider=PushProvider.FCM
    )

    assert result.success is True
    assert result.provider_message_id == "dry-run-push"


@pytest.mark.asyncio
async def test_send_push_returns_error_when_config_missing(monkeypatch):
    """Missing FCM config should return a clear error without raising."""
    monkeypatch.setattr(notification_service.settings, "NOTIFICATION_DRY_RUN", False)
    monkeypatch.setattr(notification_service.settings, "FCM_PROJECT_ID", None)
    monkeypatch.setattr(notification_service.settings, "FCM_SERVICE_ACCOUNT_JSON", None)

    result = await notification_service._send_push(
        "device-token-xyz", "Hello", "Body text", {"order_id": "abc"}, provider=PushProvider.FCM
    )

    assert result.success is False
    assert "FCM_PROJECT_ID" in (result.error_message or "")


@pytest.mark.asyncio
async def test_send_push_uses_v1_endpoint_and_bearer_auth(monkeypatch):
    """A successful push must hit the FCM HTTP v1 endpoint with Bearer auth."""
    project_id = "test-project-123"
    monkeypatch.setattr(notification_service.settings, "NOTIFICATION_DRY_RUN", False)
    monkeypatch.setattr(notification_service.settings, "FCM_PROJECT_ID", project_id)
    monkeypatch.setattr(
        notification_service.settings,
        "FCM_SERVICE_ACCOUNT_JSON",
        json.dumps({"type": "service_account"}),
    )

    fake_token = "fake-oauth2-access-token"

    # Patch the blocking credential-refresh call so we don't need real google-auth creds.
    monkeypatch.setattr(
        notification_service,
        "_refresh_fcm_credentials",
        lambda: fake_token,
    )

    # Build a fake HTTP response
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.content = b'{"name": "projects/test-project-123/messages/abc123"}'
    fake_response.json.return_value = {"name": "projects/test-project-123/messages/abc123"}

    captured: dict = {}

    async def fake_post(url, *, headers, json, **_):
        captured["url"] = url
        captured["auth"] = headers.get("Authorization", "")
        captured["body"] = json
        return fake_response

    fake_client = MagicMock()
    fake_client.post = fake_post

    monkeypatch.setattr(notification_service, "_get_fcm_http_client", lambda: fake_client)

    result = await notification_service._send_push(
        "device-token-abc",
        "Test subject",
        "Test body",
        {"order_id": "order-1", "status": "DELIVERED"},
        provider=PushProvider.FCM,
    )

    assert result.success is True
    assert result.provider_message_id == "projects/test-project-123/messages/abc123"

    assert captured["url"] == f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    assert captured["auth"] == f"Bearer {fake_token}"

    # Verify v1 payload shape
    msg = captured["body"]["message"]
    assert msg["token"] == "device-token-abc"
    assert msg["notification"] == {"title": "Test subject", "body": "Test body"}

    # All data values must be strings in v1
    for v in msg["data"].values():
        assert isinstance(v, str), f"data value {v!r} is not a string"


@pytest.mark.asyncio
async def test_send_push_returns_error_on_non_2xx(monkeypatch):
    """A non-2xx FCM response should be surfaced as a failure result."""
    monkeypatch.setattr(notification_service.settings, "NOTIFICATION_DRY_RUN", False)
    monkeypatch.setattr(notification_service.settings, "FCM_PROJECT_ID", "proj")
    monkeypatch.setattr(
        notification_service.settings,
        "FCM_SERVICE_ACCOUNT_JSON",
        json.dumps({"type": "service_account"}),
    )
    monkeypatch.setattr(notification_service, "_refresh_fcm_credentials", lambda: "token")

    fake_response = MagicMock()
    fake_response.status_code = 400
    fake_response.content = b'{"error": {"message": "INVALID_ARGUMENT"}}'
    fake_response.text = '{"error": {"message": "INVALID_ARGUMENT"}}'

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_response)

    monkeypatch.setattr(notification_service, "_get_fcm_http_client", lambda: fake_client)

    result = await notification_service._send_push("bad-token", "S", "B", {}, provider=PushProvider.FCM)

    assert result.success is False
    assert "400" in (result.error_message or "")


@pytest.mark.asyncio
async def test_send_push_routes_expo_tokens_to_expo_provider(monkeypatch):
    monkeypatch.setattr(notification_service.settings, "NOTIFICATION_DRY_RUN", False)
    monkeypatch.setattr(notification_service.settings, "EXPO_PUSH_ACCESS_TOKEN", "expo-access-token")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.content = b'{"data": {"status": "ok", "id": "expo-msg-1"}}'
    fake_response.json.return_value = {"data": {"status": "ok", "id": "expo-msg-1"}}

    captured: dict = {}

    async def fake_post(url, *, headers, json, **_):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        return fake_response

    fake_client = MagicMock()
    fake_client.post = fake_post
    monkeypatch.setattr(notification_service, "_get_fcm_http_client", lambda: fake_client)

    result = await notification_service._send_push(
        "ExponentPushToken[test-token]",
        "Expo subject",
        "Expo body",
        {"screen": "MyRoute"},
        provider=PushProvider.EXPO,
    )

    assert result.success is True
    assert result.provider_message_id == "expo-msg-1"
    assert captured["url"] == "https://exp.host/--/api/v2/push/send"
    assert captured["headers"]["Authorization"] == "Bearer expo-access-token"
    assert captured["body"]["to"] == "ExponentPushToken[test-token]"


@pytest.mark.asyncio
async def test_send_push_returns_expo_error_message(monkeypatch):
    monkeypatch.setattr(notification_service.settings, "NOTIFICATION_DRY_RUN", False)

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.content = b'{"data": {"status": "error", "message": "DeviceNotRegistered"}}'
    fake_response.json.return_value = {"data": {"status": "error", "message": "DeviceNotRegistered"}}

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_response)
    monkeypatch.setattr(notification_service, "_get_fcm_http_client", lambda: fake_client)

    result = await notification_service._send_push(
        "ExponentPushToken[bad-token]",
        "Expo subject",
        "Expo body",
        {},
        provider=PushProvider.EXPO,
    )

    assert result.success is False
    assert result.error_message == "DeviceNotRegistered"
