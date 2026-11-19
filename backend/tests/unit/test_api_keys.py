import hashlib

from app.config import settings
from app.utils.api_keys import (
    LOOKUP_PREFIX_LENGTH,
    generate_api_key,
    get_api_key_lookup_prefix,
    hash_api_key,
    verify_api_key,
)


def test_generate_api_key_format():
    key = generate_api_key()
    assert key.startswith("lmd_")
    assert len(key) > 20


def test_hash_and_verify_api_key():
    key = generate_api_key()
    hashed = hash_api_key(key)
    assert hashed != key
    assert hashed != hash_api_key(key)
    assert verify_api_key(key, hashed)
    assert not verify_api_key("wrong-key", hashed)


def test_verify_api_key_legacy_hash_still_supported():
    key = generate_api_key()
    legacy = hashlib.sha256(f"{settings.JWT_SECRET}:{key}".encode()).hexdigest()
    assert verify_api_key(key, legacy)
    assert not verify_api_key("wrong-key", legacy)


def test_lookup_prefix_is_stable_and_bounded():
    key = generate_api_key()
    prefix_a = get_api_key_lookup_prefix(key)
    prefix_b = get_api_key_lookup_prefix(key)

    assert prefix_a == prefix_b
    assert len(prefix_a) == LOOKUP_PREFIX_LENGTH
    assert prefix_a != key[:LOOKUP_PREFIX_LENGTH]
