import hashlib
import hmac
import secrets

from passlib.context import CryptContext

from app.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
LOOKUP_PREFIX_LENGTH = 16


def generate_api_key() -> str:
    return f"lmd_{secrets.token_urlsafe(32)}"


def _legacy_hash_api_key(api_key: str) -> str:
    payload = f"{settings.JWT_SECRET}:{api_key}".encode()
    return hashlib.sha256(payload).hexdigest()


def get_api_key_lookup_prefix(api_key: str) -> str:
    digest = hashlib.sha256(api_key.encode()).hexdigest()
    return digest[:LOOKUP_PREFIX_LENGTH]


def hash_api_key(api_key: str) -> str:
    return _pwd_context.hash(api_key)


def verify_api_key(api_key: str, hashed: str) -> bool:
    if hashed.startswith("$2"):
        try:
            return _pwd_context.verify(api_key, hashed)
        except ValueError:
            return False

    # Backward-compatible verification for legacy deterministic hashes.
    return hmac.compare_digest(_legacy_hash_api_key(api_key), hashed)
