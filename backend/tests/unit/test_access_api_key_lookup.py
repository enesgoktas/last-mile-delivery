import uuid
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.models.webhook import ApiClient
from app.utils.access import _find_api_client_for_key
from app.utils.api_keys import get_api_key_lookup_prefix, hash_api_key
from app.utils.compat import UTC


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarResult(self._items)


def _make_client(*, key: str, key_prefix: str | None) -> ApiClient:
    return ApiClient(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name=f"client-{uuid.uuid4()}",
        api_key_hash=hash_api_key(key),
        api_key_prefix=key_prefix,
        permissions=["orders:create"],
        rate_limit_per_min=60,
        is_active=True,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )


@pytest.mark.asyncio
async def test_prefixed_lookup_matches_without_legacy_scan():
    key = "lmd_test_prefixed_key"
    client = _make_client(key=key, key_prefix=get_api_key_lookup_prefix(key))

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_ExecuteResult([client])])

    found = await _find_api_client_for_key(db, key)

    assert found == client
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_lookup_falls_back_to_legacy_null_prefix_clients():
    requested_key = "lmd_test_legacy_key"
    wrong_prefixed = _make_client(
        key="lmd_other_key",
        key_prefix=get_api_key_lookup_prefix(requested_key),
    )
    legacy_client = _make_client(key=requested_key, key_prefix=None)

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _ExecuteResult([wrong_prefixed]),
            _ExecuteResult([legacy_client]),
        ]
    )

    found = await _find_api_client_for_key(db, requested_key)

    assert found == legacy_client
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_lookup_returns_none_for_invalid_key():
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _ExecuteResult([]),
            _ExecuteResult([]),
        ]
    )

    found = await _find_api_client_for_key(db, "lmd_invalid_key")

    assert found is None
    assert db.execute.await_count == 2
