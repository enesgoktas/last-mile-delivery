import pytest

from app.config import settings
from app.utils.webhooks import decrypt_webhook_secret, encrypt_webhook_secret


def test_encrypt_decrypt_webhook_secret_round_trip(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET", "unit-test-jwt-secret-32bytes!!")
    raw = "whsec_12345"
    encrypted = encrypt_webhook_secret(raw)

    assert encrypted != raw
    assert encrypted.startswith("enc::")
    assert decrypt_webhook_secret(encrypted) == raw


def test_decrypt_webhook_secret_supports_plaintext_legacy_values():
    raw = "legacy-plaintext-secret"
    assert decrypt_webhook_secret(raw) == raw
