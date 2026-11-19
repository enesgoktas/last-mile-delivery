from app.config import settings
from app.models.enums import WebhookDeliveryStatus
from app.services import webhook_service


def test_webhook_backoff_is_exponential_with_cap():
    assert webhook_service.calculate_webhook_backoff_seconds(1) == settings.WEBHOOK_BACKOFF_BASE_SECONDS
    assert webhook_service.calculate_webhook_backoff_seconds(2) == settings.WEBHOOK_BACKOFF_BASE_SECONDS * 2
    assert (
        webhook_service.calculate_webhook_backoff_seconds(99)
        == settings.WEBHOOK_BACKOFF_MAX_SECONDS
    )


def test_webhook_delivery_status_success():
    result = webhook_service.resolve_webhook_delivery_status(
        success=True,
        attempt_number=1,
        max_attempts=5,
    )
    assert result == WebhookDeliveryStatus.SUCCESS


def test_webhook_delivery_status_retry_scheduled():
    result = webhook_service.resolve_webhook_delivery_status(
        success=False,
        attempt_number=2,
        max_attempts=5,
    )
    assert result == WebhookDeliveryStatus.RETRY_SCHEDULED


def test_webhook_delivery_status_dead_letter():
    result = webhook_service.resolve_webhook_delivery_status(
        success=False,
        attempt_number=5,
        max_attempts=5,
    )
    assert result == WebhookDeliveryStatus.DEAD_LETTER
