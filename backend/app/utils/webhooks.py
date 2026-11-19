from app.config import settings
from app.utils.secret_encryption import decrypt_secret, encrypt_secret

_ENCRYPTED_PREFIX = "enc::"


def _webhook_secret_seed() -> str:
    return settings.WEBHOOK_SECRET_ENCRYPTION_KEY or settings.JWT_SECRET


def encrypt_webhook_secret(secret: str) -> str:
    return encrypt_secret(secret, seed=_webhook_secret_seed(), prefix=_ENCRYPTED_PREFIX)


def decrypt_webhook_secret(secret_value: str) -> str:
    # Backward compatibility for legacy plaintext rows.
    return decrypt_secret(
        secret_value,
        seed=_webhook_secret_seed(),
        prefix=_ENCRYPTED_PREFIX,
        allow_plaintext_legacy=True,
    )
