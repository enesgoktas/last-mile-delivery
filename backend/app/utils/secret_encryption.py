import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

_ENCRYPTED_PREFIX = "enc::"


def _build_fernet(seed: str) -> Fernet:
    if not seed:
        raise ValueError("Secret encryption seed must not be empty")
    key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(secret: str, *, seed: str, prefix: str = _ENCRYPTED_PREFIX) -> str:
    token = _build_fernet(seed).encrypt(secret.encode("utf-8")).decode("utf-8")
    return f"{prefix}{token}"


def decrypt_secret(
    secret_value: str,
    *,
    seed: str,
    prefix: str = _ENCRYPTED_PREFIX,
    allow_plaintext_legacy: bool = True,
) -> str:
    if not secret_value.startswith(prefix):
        if allow_plaintext_legacy:
            return secret_value
        raise ValueError("Encrypted secret is missing the expected prefix")

    encrypted = secret_value[len(prefix):]
    try:
        return _build_fernet(seed).decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Invalid encrypted secret") from exc
