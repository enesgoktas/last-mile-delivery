import pytest

from app.utils.secret_encryption import decrypt_secret, encrypt_secret


def test_encrypt_secret_round_trip():
    raw = "s3cr3t-value"
    encrypted = encrypt_secret(raw, seed="seed-one")

    assert encrypted != raw
    assert encrypted.startswith("enc::")
    assert decrypt_secret(encrypted, seed="seed-one") == raw


def test_decrypt_secret_supports_plaintext_legacy_values():
    raw = "legacy-plaintext-secret"

    assert decrypt_secret(raw, seed="seed-one") == raw


def test_decrypt_secret_rejects_wrong_seed():
    encrypted = encrypt_secret("top-secret", seed="correct-seed")

    with pytest.raises(ValueError, match="Invalid encrypted secret"):
        decrypt_secret(encrypted, seed="wrong-seed")
