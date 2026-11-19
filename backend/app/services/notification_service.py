from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import uuid
from dataclasses import dataclass

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.courier import Courier
from app.models.enums import NotificationChannel, NotificationStatus, PushProvider
from app.models.notification import Notification
from app.models.order import Order
from app.services import operations_policy_service
from app.utils.compat import utcnow_naive
from app.utils.tenant_context import tenant_filter

# Redis client (initialized lazily)
_redis: aioredis.Redis | None = None
logger = logging.getLogger(__name__)

# ── FCM HTTP v1 — module-level state ──────────────────────────────────────────
# A single httpx.AsyncClient is reused across all push calls (connection pooling).
# The service-account credentials object is cached and refreshed automatically
# under a threading.Lock so concurrent async calls cannot race on refresh.
_fcm_http_client: httpx.AsyncClient | None = None
_fcm_credentials = None  # google.oauth2.service_account.Credentials
_fcm_creds_lock = threading.Lock()


def _get_fcm_http_client() -> httpx.AsyncClient:
    global _fcm_http_client
    if _fcm_http_client is None or _fcm_http_client.is_closed:
        _fcm_http_client = httpx.AsyncClient(timeout=10.0)
    return _fcm_http_client


def _refresh_fcm_credentials() -> str:
    """Synchronously refresh the FCM service-account credentials and return an access token.

    Must be called inside asyncio.to_thread() — google-auth's refresh is blocking.
    The threading.Lock prevents concurrent refreshes from multiple async tasks.
    """
    import json

    try:
        import google.auth.transport.requests as google_requests  # type: ignore[import]
        from google.oauth2 import service_account  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "google-auth package is required for FCM HTTP v1 push notifications. "
            "Install it with: pip install google-auth>=2.27"
        ) from exc

    global _fcm_credentials

    with _fcm_creds_lock:
        if _fcm_credentials is None:
            sa_info = json.loads(settings.FCM_SERVICE_ACCOUNT_JSON)  # type: ignore[arg-type]
            _fcm_credentials = service_account.Credentials.from_service_account_info(
                sa_info,
                scopes=["https://www.googleapis.com/auth/firebase.messaging"],
            )
        if not _fcm_credentials.valid:
            req = google_requests.Request()
            _fcm_credentials.refresh(req)
        return _fcm_credentials.token  # type: ignore[return-value]


@dataclass(slots=True)
class ProviderResult:
    success: bool
    provider_message_id: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class PushTarget:
    provider: PushProvider
    token: str


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


def _parse_optional_uuid(raw_value: object) -> uuid.UUID | None:
    if raw_value in (None, ""):
        return None
    try:
        return uuid.UUID(str(raw_value))
    except (TypeError, ValueError, AttributeError):
        return None


def build_notification_dedupe_key(
    *,
    channel: NotificationChannel,
    event_type: str,
    recipient: str,
    order_id: uuid.UUID | None,
) -> str:
    raw = f"{channel.value}|{event_type}|{recipient.lower()}|{order_id or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def should_suppress_notification(dedupe_key: str) -> bool:
    """Return True when a duplicate notification should be suppressed."""
    try:
        r = await get_redis()
        created = await r.set(
            f"notification:dedupe:{dedupe_key}",
            "1",
            ex=settings.NOTIFICATION_DEDUPE_TTL_SECONDS,
            nx=True,
        )
        return not bool(created)
    except Exception:
        # Redis failures should not block notifications.
        return False


def render_notification_content(event_type: str, payload: dict) -> tuple[str, str]:
    order_label = payload.get("external_ref") or payload.get("order_id") or "order"
    status = str(payload.get("status") or "UPDATED").replace("_", " ").title()
    reason = payload.get("reason")
    reschedule_url = payload.get("reschedule_url") or payload.get("tracking_url")

    subject = f"Delivery update for {order_label}: {status}"
    body_parts = [
        f"Order: {order_label}",
        f"Status: {status}",
    ]
    if reason:
        body_parts.append(f"Reason: {reason}")
    if payload.get("delivery_otp_code") and event_type in {"order.assigned", "order.in_transit"}:
        body_parts.append(f"Delivery OTP: {payload['delivery_otp_code']}")
    if event_type == "order.failed" and reschedule_url:
        body_parts.append(f"Reschedule: {reschedule_url}")
    body_parts.append(f"Event: {event_type}")
    body_parts.append(f"Updated at: {payload.get('timestamp')}")
    return subject, "\n".join(body_parts)


async def dispatch_order_event_notifications(db: AsyncSession, event_type: str, payload: dict) -> list[Notification]:
    """Dispatch customer/courier notifications for order transition events."""
    order_id = _parse_optional_uuid(payload.get("order_id"))
    tenant_id = _parse_optional_uuid(payload.get("tenant_id"))
    if payload.get("order_id") and order_id is None:
        logger.warning("Invalid notification payload order_id; continuing without order linkage.")
    if tenant_id is None and order_id is not None:
        tenant_result = await db.execute(
            select(Order.tenant_id).where(Order.id == order_id)
        )
        tenant_id = tenant_result.scalar_one_or_none()
    if tenant_id is None:
        logger.warning("Skipping notification dispatch because tenant_id could not be resolved.")
        return []
    operations_policy = await operations_policy_service.get_operations_policy(db, tenant_id)
    subject, body = render_notification_content(event_type, payload)

    targets: list[tuple[NotificationChannel, str | PushTarget]] = []
    customer_email = payload.get("customer_email")
    customer_phone = payload.get("customer_phone")
    assigned_courier_id = payload.get("assigned_courier_id")

    if customer_email and operations_policy_service.is_notification_channel_enabled(
        operations_policy,
        event_type=event_type,
        channel="email",
    ):
        targets.append((NotificationChannel.EMAIL, customer_email))
    if customer_phone and operations_policy_service.is_notification_channel_enabled(
        operations_policy,
        event_type=event_type,
        channel="sms",
    ):
        targets.append((NotificationChannel.SMS, customer_phone))

    if assigned_courier_id:
        courier_id = _parse_optional_uuid(assigned_courier_id)
        if courier_id is None:
            logger.warning("Invalid assigned_courier_id in notification payload; skipping push target.")
            courier_id = None
    else:
        courier_id = None

    if courier_id:
        result = await db.execute(
            select(Courier.push_provider, Courier.push_token, Courier.fcm_token).where(
                Courier.id == courier_id,
                tenant_filter(Courier, tenant_id),
            )
        )
        push_registration = result.one_or_none()
        push_target: PushTarget | None = None
        if push_registration is not None:
            stored_provider, push_token, legacy_fcm_token = push_registration
            if push_token and stored_provider is not None:
                push_target = PushTarget(provider=stored_provider, token=push_token)
            elif legacy_fcm_token:
                push_target = PushTarget(provider=PushProvider.FCM, token=legacy_fcm_token)

        if push_target and operations_policy_service.is_notification_channel_enabled(
            operations_policy,
            event_type=event_type,
            channel="push",
        ):
            targets.append((NotificationChannel.PUSH, push_target))

    created: list[Notification] = []
    for channel, recipient in targets:
        notification = await _create_and_send_notification(
            db=db,
            tenant_id=tenant_id,
            order_id=order_id,
            channel=channel,
            event_type=event_type,
            recipient=recipient.token if isinstance(recipient, PushTarget) else recipient,
            subject=subject,
            body=body,
            payload=payload,
            operations_policy=operations_policy,
            push_provider=recipient.provider if isinstance(recipient, PushTarget) else None,
        )
        created.append(notification)
    return created


async def _create_and_send_notification(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    order_id: uuid.UUID | None,
    channel: NotificationChannel,
    event_type: str,
    recipient: str,
    subject: str,
    body: str,
    payload: dict,
    operations_policy,
    push_provider: PushProvider | None = None,
) -> Notification:
    dedupe_key = build_notification_dedupe_key(
        channel=channel,
        event_type=event_type,
        recipient=recipient,
        order_id=order_id,
    )

    if await should_suppress_notification(dedupe_key):
        notification = Notification(
            tenant_id=tenant_id,
            order_id=order_id,
            channel=channel,
            event_type=event_type,
            recipient=recipient,
            dedupe_key=dedupe_key,
            payload=payload,
            status=NotificationStatus.SUPPRESSED,
            error_message="Suppressed duplicate notification in cooldown window",
        )
        db.add(notification)
        await db.flush()
        return notification

    notification = Notification(
        tenant_id=tenant_id,
        order_id=order_id,
        channel=channel,
        event_type=event_type,
        recipient=recipient,
        dedupe_key=dedupe_key,
        payload=payload,
        status=NotificationStatus.PENDING,
    )
    db.add(notification)
    await db.flush()

    if channel in {NotificationChannel.EMAIL, NotificationChannel.SMS} and operations_policy_service.is_within_quiet_hours(operations_policy):
        notification.status = NotificationStatus.SUPPRESSED
        notification.error_message = "Suppressed during quiet hours"
        await db.flush()
        return notification

    if channel == NotificationChannel.EMAIL:
        provider_result = await _send_email(recipient, subject, body)
    elif channel == NotificationChannel.SMS:
        provider_result = await _send_sms(recipient, body)
    else:
        provider_result = await _send_push(
            recipient,
            subject,
            body,
            payload,
            provider=push_provider or PushProvider.FCM,
        )

    notification.provider_message_id = provider_result.provider_message_id
    if provider_result.success:
        notification.status = NotificationStatus.SENT
        notification.sent_at = utcnow_naive()
        notification.error_message = None
    else:
        notification.status = NotificationStatus.FAILED
        notification.error_message = provider_result.error_message
    await db.flush()
    return notification


async def _send_email(recipient: str, subject: str, body: str) -> ProviderResult:
    if settings.NOTIFICATION_DRY_RUN:
        return ProviderResult(success=True, provider_message_id="dry-run-email")

    if not settings.SES_FROM_EMAIL:
        return ProviderResult(success=False, error_message="SES_FROM_EMAIL is not configured")

    try:
        import boto3
    except Exception:
        return ProviderResult(success=False, error_message="boto3 not available for SES dispatch")

    try:
        def _send_sync() -> dict:
            client = boto3.client("ses", region_name=settings.AWS_REGION)
            return client.send_email(
                Source=settings.SES_FROM_EMAIL,
                Destination={"ToAddresses": [recipient]},
                Message={
                    "Subject": {"Data": subject},
                    "Body": {"Text": {"Data": body}},
                },
            )

        response = await asyncio.to_thread(_send_sync)
        message_id = response.get("MessageId")
        return ProviderResult(success=True, provider_message_id=message_id)
    except Exception as exc:
        return ProviderResult(success=False, error_message=str(exc))


async def _send_sms(recipient: str, body: str) -> ProviderResult:
    if settings.NOTIFICATION_DRY_RUN:
        return ProviderResult(success=True, provider_message_id="dry-run-sms")

    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER):
        return ProviderResult(success=False, error_message="Twilio credentials are not fully configured")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                data={
                    "From": settings.TWILIO_FROM_NUMBER,
                    "To": recipient,
                    "Body": body,
                },
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            )
        if 200 <= response.status_code < 300:
            payload = response.json() if response.content else {}
            return ProviderResult(success=True, provider_message_id=payload.get("sid"))
        return ProviderResult(
            success=False,
            error_message=f"Twilio returned status {response.status_code}",
        )
    except Exception as exc:
        return ProviderResult(success=False, error_message=str(exc))


async def _send_expo_push(recipient: str, subject: str, body: str, payload: dict) -> ProviderResult:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if settings.EXPO_PUSH_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {settings.EXPO_PUSH_ACCESS_TOKEN}"

    try:
        client = _get_fcm_http_client()
        response = await client.post(
            "https://exp.host/--/api/v2/push/send",
            headers=headers,
            json={
                "to": recipient,
                "title": subject,
                "body": body,
                "data": payload,
                "sound": "default",
                "channelId": "default",
            },
        )
        if 200 <= response.status_code < 300:
            payload_data = response.json() if response.content else {}
            response_data = payload_data.get("data")
            entry = response_data[0] if isinstance(response_data, list) and response_data else response_data
            if isinstance(entry, dict) and entry.get("status") == "ok":
                return ProviderResult(
                    success=True,
                    provider_message_id=entry.get("id", "expo-ok"),
                )

            expo_errors = payload_data.get("errors")
            error_message = None
            if isinstance(entry, dict):
                error_message = entry.get("message")
            if not error_message and isinstance(expo_errors, list) and expo_errors:
                first_error = expo_errors[0]
                if isinstance(first_error, dict):
                    error_message = first_error.get("message")
            return ProviderResult(
                success=False,
                error_message=error_message or "Expo push response did not confirm delivery",
            )
        return ProviderResult(
            success=False,
            error_message=f"Expo returned status {response.status_code}: {response.text[:300]}",
        )
    except Exception as exc:
        return ProviderResult(success=False, error_message=str(exc))


async def _send_push(
    recipient: str,
    subject: str,
    body: str,
    payload: dict,
    *,
    provider: PushProvider,
) -> ProviderResult:
    if settings.NOTIFICATION_DRY_RUN:
        return ProviderResult(success=True, provider_message_id="dry-run-push")

    if provider == PushProvider.EXPO:
        return await _send_expo_push(recipient, subject, body, payload)

    if not settings.FCM_PROJECT_ID or not settings.FCM_SERVICE_ACCOUNT_JSON:
        return ProviderResult(
            success=False,
            error_message="FCM_PROJECT_ID and FCM_SERVICE_ACCOUNT_JSON must both be set for push notifications",
        )

    try:
        access_token = await asyncio.to_thread(_refresh_fcm_credentials)

        # FCM HTTP v1 requires all `data` values to be strings.
        data_payload = {k: str(v) for k, v in payload.items() if v is not None}

        url = f"https://fcm.googleapis.com/v1/projects/{settings.FCM_PROJECT_ID}/messages:send"
        client = _get_fcm_http_client()
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "message": {
                    "token": recipient,
                    "notification": {"title": subject, "body": body},
                    "data": data_payload,
                }
            },
        )
        if 200 <= response.status_code < 300:
            resp_data = response.json() if response.content else {}
            # v1 API returns a `name` field like "projects/{project}/messages/{id}"
            return ProviderResult(
                success=True,
                provider_message_id=resp_data.get("name", "fcm-ok"),
            )
        return ProviderResult(
            success=False,
            error_message=f"FCM returned status {response.status_code}: {response.text[:300]}",
        )
    except Exception as exc:
        return ProviderResult(success=False, error_message=str(exc))
