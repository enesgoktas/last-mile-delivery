import uuid
from datetime import datetime

import pytest

from app.models.enums import WebhookDeliveryStatus
from app.models.webhook import WebhookEndpoint
from app.services import webhook_service
from app.utils.compat import UTC
from app.utils.perf_metrics import InMemoryPerfMetrics


class _ExecuteResult:
    def __init__(self, endpoint: WebhookEndpoint | None):
        self._endpoint = endpoint

    def scalar_one_or_none(self):
        return self._endpoint


class _FakeSession:
    def __init__(self, endpoint: WebhookEndpoint | None):
        self._endpoint = endpoint
        self.added = []

    async def execute(self, _query):
        return _ExecuteResult(self._endpoint)

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        return None


def _make_endpoint(secret: str) -> WebhookEndpoint:
    return WebhookEndpoint(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        url="https://example.com/webhook",
        secret=secret,
        events=["order.delivered"],
        is_active=True,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )


@pytest.mark.asyncio
async def test_deliver_webhook_success_signs_payload_with_decrypted_secret(monkeypatch):
    raw_secret = "whsec_test_secret"
    endpoint = _make_endpoint(secret="enc::stored-secret")
    db = _FakeSession(endpoint)
    captured = {}
    metrics = InMemoryPerfMetrics()

    class _FakeResponse:
        status_code = 204

    class _FakeHttpClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResponse()

    monkeypatch.setattr(webhook_service.httpx, "AsyncClient", _FakeHttpClient)
    monkeypatch.setattr(webhook_service, "perf_metrics", metrics)
    monkeypatch.setattr(
        webhook_service,
        "decrypt_webhook_secret",
        lambda encrypted_secret: raw_secret if encrypted_secret == endpoint.secret else "",
    )

    payload = {"order_id": "ord_123", "status": "DELIVERED"}
    success, status_code, next_retry_at, delivery_status = await webhook_service.deliver_webhook(
        db,
        endpoint.id,
        "order.delivered",
        payload,
        1,
    )

    assert success is True
    assert status_code == 204
    assert next_retry_at is None
    assert delivery_status == WebhookDeliveryStatus.SUCCESS

    envelope = captured["json"]
    expected_signature = webhook_service._sign_payload(raw_secret, envelope)
    assert captured["url"] == endpoint.url
    assert captured["headers"]["X-Webhook-Signature"] == expected_signature

    assert len(db.added) == 1
    assert db.added[0].delivery_status == WebhookDeliveryStatus.SUCCESS
    assert db.added[0].response_status == 204
    assert db.added[0].error_message is None
    assert metrics.get_counter("webhook_delivery_attempts") == 1
    assert metrics.get_counter("webhook_delivery_success") == 1
    assert metrics.summary("webhook_delivery_latency_ms")["count"] == 1


@pytest.mark.asyncio
async def test_deliver_webhook_invalid_encrypted_secret_marks_dead_letter_on_final_attempt(monkeypatch):
    endpoint = _make_endpoint(secret="enc::invalid-token")
    db = _FakeSession(endpoint)
    metrics = InMemoryPerfMetrics()

    class _ShouldNotBeCalledHttpClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("HTTP client should not be called for invalid encrypted secrets")

    monkeypatch.setattr(webhook_service.httpx, "AsyncClient", _ShouldNotBeCalledHttpClient)
    monkeypatch.setattr(webhook_service, "perf_metrics", metrics)

    success, status_code, next_retry_at, delivery_status = await webhook_service.deliver_webhook(
        db,
        endpoint.id,
        "order.failed",
        {"order_id": "ord_456", "status": "FAILED"},
        webhook_service.settings.WEBHOOK_MAX_ATTEMPTS,
    )

    assert success is False
    assert status_code is None
    assert next_retry_at is None
    assert delivery_status == WebhookDeliveryStatus.DEAD_LETTER
    assert len(db.added) == 1
    assert db.added[0].delivery_status == WebhookDeliveryStatus.DEAD_LETTER
    assert "Invalid encrypted webhook secret" in (db.added[0].error_message or "")
    assert metrics.get_counter("webhook_delivery_attempts") == 1
    assert metrics.get_counter("webhook_delivery_dead_letter") == 1
    assert metrics.summary("webhook_delivery_latency_ms")["count"] == 1
