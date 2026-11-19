from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import get_async_session_factory
from app.models.enums import WebhookDeliveryStatus
from app.models.webhook import WebhookDeliveryLog, WebhookEndpoint
from app.services import webhook_service
from app.workers import webhook_tasks
from tests.integration.live_api_helpers import auth_headers, signup_owner
from tests.integration.live_test_utils import run_async

pytestmark = pytest.mark.live_infra


async def _deliver_webhook_attempt(
    endpoint_id: str,
    *,
    event_type: str,
    payload: dict,
    attempt_number: int,
):
    async with get_async_session_factory()() as db:
        result = await webhook_service.deliver_webhook(
            db,
            endpoint_id=uuid.UUID(endpoint_id),
            event_type=event_type,
            payload=payload,
            attempt_number=attempt_number,
        )
        await db.commit()
        return result


async def _get_webhook_snapshot(endpoint_id: str) -> dict:
    async with get_async_session_factory()() as db:
        endpoint_result = await db.execute(
            select(WebhookEndpoint).where(WebhookEndpoint.id == uuid.UUID(endpoint_id))
        )
        endpoint = endpoint_result.scalar_one()

        logs_result = await db.execute(
            select(WebhookDeliveryLog)
            .where(WebhookDeliveryLog.webhook_endpoint_id == uuid.UUID(endpoint_id))
            .order_by(WebhookDeliveryLog.sent_at.asc())
        )
        logs = list(logs_result.scalars().all())
        return {
            "encrypted_secret": endpoint.secret,
            "log_count": len(logs),
            "log_attempt_numbers": [log.attempt_number for log in logs],
            "log_statuses": [log.delivery_status for log in logs],
            "log_response_statuses": [log.response_status for log in logs],
            "log_error_messages": [log.error_message for log in logs],
            "log_next_retry_ats": [log.next_retry_at for log in logs],
            "last_payload": logs[-1].payload if logs else None,
        }


def test_live_webhook_delivery_logs_and_replay_queue_are_persisted(
    live_test_client,
    monkeypatch,
):
    owner = signup_owner(live_test_client, "webhooks")

    api_key_response = live_test_client.post(
        "/api/v1/api-keys",
        headers=auth_headers(owner["access_token"]),
        json={
            "name": "Webhook integration client",
            "permissions": ["orders:read"],
            "rate_limit_per_min": 120,
        },
    )
    assert api_key_response.status_code == 201
    api_key = api_key_response.json()

    create_webhook_response = live_test_client.post(
        "/api/v1/webhooks",
        headers=auth_headers(owner["access_token"]),
        json={
            "client_id": api_key["id"],
            "url": "https://example.invalid/webhooks/orders",
            "events": ["order.updated"],
            "secret": "super-secret-value",
        },
    )
    assert create_webhook_response.status_code == 201
    webhook = create_webhook_response.json()

    list_webhooks_response = live_test_client.get(
        "/api/v1/webhooks",
        headers=auth_headers(owner["access_token"]),
    )
    assert list_webhooks_response.status_code == 200
    assert [endpoint["id"] for endpoint in list_webhooks_response.json()] == [webhook["id"]]

    class _FailingAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def post(self, url, json, headers):
            del url, json, headers
            return type("Response", (), {"status_code": 500})()

    monkeypatch.setattr(
        "app.services.webhook_service.httpx.AsyncClient",
        lambda timeout: _FailingAsyncClient(),
    )

    payload = {"tenant_id": "ignored-for-direct-delivery", "order_id": "ord_123"}
    success, response_status, next_retry_at, delivery_status = run_async(
        _deliver_webhook_attempt(
            webhook["id"],
            event_type="order.updated",
            payload=payload,
            attempt_number=settings.WEBHOOK_MAX_ATTEMPTS,
        )
    )
    assert success is False
    assert response_status == 500
    assert next_retry_at is None
    assert delivery_status == WebhookDeliveryStatus.DEAD_LETTER

    snapshot = run_async(_get_webhook_snapshot(webhook["id"]))
    assert snapshot["encrypted_secret"] != "super-secret-value"
    assert snapshot["log_count"] == 1
    assert snapshot["log_statuses"] == [WebhookDeliveryStatus.DEAD_LETTER]
    assert snapshot["log_error_messages"] == ["Webhook returned status 500"]
    assert snapshot["last_payload"]["data"] == payload

    logs_response = live_test_client.get(
        f"/api/v1/webhooks/{webhook['id']}/logs",
        headers=auth_headers(owner["access_token"]),
        params={"delivery_status": WebhookDeliveryStatus.DEAD_LETTER.value},
    )
    assert logs_response.status_code == 200
    assert len(logs_response.json()) == 1
    assert logs_response.json()[0]["delivery_status"] == WebhookDeliveryStatus.DEAD_LETTER

    replay_calls: list[tuple[str, str, dict, int]] = []

    def _capture_enqueue(_task, *, args=None, **_kwargs):
        replay_calls.append(tuple(args or []))

    monkeypatch.setattr("app.services.webhook_service.enqueue_task", _capture_enqueue)

    replay_response = live_test_client.post(
        f"/api/v1/webhooks/{webhook['id']}/replay",
        headers=auth_headers(owner["access_token"]),
        params={"limit": 5},
    )
    assert replay_response.status_code == 200
    assert replay_response.json()["requeued_count"] == 1
    assert replay_calls == [
        (webhook["id"], "order.updated", payload, 1),
    ]


def test_live_webhook_worker_retries_then_succeeds_with_expected_backoff(
    live_test_client,
    monkeypatch,
):
    owner = signup_owner(live_test_client, "webhooks-worker")
    me_response = live_test_client.get(
        "/api/v1/auth/me",
        headers=auth_headers(owner["access_token"]),
    )
    assert me_response.status_code == 200
    tenant_id = me_response.json()["tenant_id"]

    api_key_response = live_test_client.post(
        "/api/v1/api-keys",
        headers=auth_headers(owner["access_token"]),
        json={
            "name": "Webhook worker integration client",
            "permissions": ["orders:read"],
            "rate_limit_per_min": 120,
        },
    )
    assert api_key_response.status_code == 201
    api_key = api_key_response.json()

    create_webhook_response = live_test_client.post(
        "/api/v1/webhooks",
        headers=auth_headers(owner["access_token"]),
        json={
            "client_id": api_key["id"],
            "url": "https://example.invalid/webhooks/retry",
            "events": ["order.updated"],
            "secret": "worker-retry-secret",
        },
    )
    assert create_webhook_response.status_code == 201
    webhook = create_webhook_response.json()

    dispatched_calls: list[tuple[str, str, dict, int]] = []

    def _capture_enqueue(_task, *, args=None, **_kwargs):
        endpoint_id, event_type, payload, attempt = args or []
        dispatched_calls.append((endpoint_id, event_type, payload, attempt))
        return SimpleNamespace(id=f"dispatch-{attempt}")

    monkeypatch.setattr(
        "app.workers.webhook_tasks.enqueue_task",
        _capture_enqueue,
    )

    payload = {"tenant_id": tenant_id, "order_id": "ord_worker_retry"}
    dispatch_result = webhook_tasks.dispatch_webhook_event("order.updated", payload)
    assert dispatch_result == {"event": "order.updated", "enqueued": 1}
    assert dispatched_calls == [(webhook["id"], "order.updated", payload, 1)]

    response_codes = [500, 204]

    class _SequencedAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def post(self, url, json, headers):
            del url, json, headers
            return type("Response", (), {"status_code": response_codes.pop(0)})()

    monkeypatch.setattr(
        "app.services.webhook_service.httpx.AsyncClient",
        lambda timeout: _SequencedAsyncClient(),
    )

    retry_calls: list[dict[str, object]] = []

    def _capture_retry(_task, *, args=None, countdown=None, **_kwargs):
        retry_calls.append({"args": list(args or []), "countdown": countdown})
        return SimpleNamespace(id=f"retry-{len(retry_calls)}")

    monkeypatch.setattr(
        "app.workers.webhook_tasks.enqueue_task",
        _capture_retry,
    )

    first_attempt = webhook_tasks.deliver_webhook(
        webhook["id"],
        "order.updated",
        payload,
        1,
    )
    assert first_attempt["success"] is False
    assert first_attempt["response_status"] == 500
    assert first_attempt["delivery_status"] == WebhookDeliveryStatus.RETRY_SCHEDULED.value
    assert first_attempt["attempt"] == 1
    assert first_attempt["next_retry_at"] is not None
    assert retry_calls == [
        {
            "args": [webhook["id"], "order.updated", payload, 2],
            "countdown": settings.WEBHOOK_BACKOFF_BASE_SECONDS,
        }
    ]

    second_attempt = webhook_tasks.deliver_webhook(*retry_calls[0]["args"])
    assert second_attempt["success"] is True
    assert second_attempt["response_status"] == 204
    assert second_attempt["delivery_status"] == WebhookDeliveryStatus.SUCCESS.value
    assert second_attempt["attempt"] == 2
    assert second_attempt["next_retry_at"] is None
    assert len(retry_calls) == 1

    snapshot = run_async(_get_webhook_snapshot(webhook["id"]))
    assert snapshot["log_count"] == 2
    assert snapshot["log_attempt_numbers"] == [1, 2]
    assert snapshot["log_statuses"] == [
        WebhookDeliveryStatus.RETRY_SCHEDULED,
        WebhookDeliveryStatus.SUCCESS,
    ]
    assert snapshot["log_response_statuses"] == [500, 204]
    assert snapshot["log_next_retry_ats"][0] is not None
    assert snapshot["log_next_retry_ats"][1] is None


def test_live_webhook_worker_final_attempt_dead_letters_without_requeue(
    live_test_client,
    monkeypatch,
):
    owner = signup_owner(live_test_client, "webhooks-deadletter")

    api_key_response = live_test_client.post(
        "/api/v1/api-keys",
        headers=auth_headers(owner["access_token"]),
        json={
            "name": "Webhook dead-letter client",
            "permissions": ["orders:read"],
            "rate_limit_per_min": 120,
        },
    )
    assert api_key_response.status_code == 201
    api_key = api_key_response.json()

    create_webhook_response = live_test_client.post(
        "/api/v1/webhooks",
        headers=auth_headers(owner["access_token"]),
        json={
            "client_id": api_key["id"],
            "url": "https://example.invalid/webhooks/final",
            "events": ["order.failed"],
            "secret": "worker-dead-letter-secret",
        },
    )
    assert create_webhook_response.status_code == 201
    webhook = create_webhook_response.json()

    class _FailingAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def post(self, url, json, headers):
            del url, json, headers
            return type("Response", (), {"status_code": 500})()

    monkeypatch.setattr(
        "app.services.webhook_service.httpx.AsyncClient",
        lambda timeout: _FailingAsyncClient(),
    )

    retry_calls: list[dict[str, object]] = []

    def _capture_retry(_task, *, args=None, countdown=None, **_kwargs):
        retry_calls.append({"args": list(args or []), "countdown": countdown})
        return SimpleNamespace(id="retry-should-not-happen")

    monkeypatch.setattr(
        "app.workers.webhook_tasks.enqueue_task",
        _capture_retry,
    )

    payload = {"tenant_id": "tenant-dead-letter", "order_id": "ord_dead_letter"}
    result = webhook_tasks.deliver_webhook(
        webhook["id"],
        "order.failed",
        payload,
        settings.WEBHOOK_MAX_ATTEMPTS,
    )
    assert result["success"] is False
    assert result["response_status"] == 500
    assert result["delivery_status"] == WebhookDeliveryStatus.DEAD_LETTER.value
    assert result["attempt"] == settings.WEBHOOK_MAX_ATTEMPTS
    assert result["next_retry_at"] is None
    assert retry_calls == []

    snapshot = run_async(_get_webhook_snapshot(webhook["id"]))
    assert snapshot["log_count"] == 1
    assert snapshot["log_attempt_numbers"] == [settings.WEBHOOK_MAX_ATTEMPTS]
    assert snapshot["log_statuses"] == [WebhookDeliveryStatus.DEAD_LETTER]
    assert snapshot["log_response_statuses"] == [500]
    assert snapshot["log_next_retry_ats"] == [None]
