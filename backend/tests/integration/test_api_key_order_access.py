import hashlib
import uuid
from datetime import timedelta

from app.config import settings
from app.models.webhook import ApiClient
from app.utils import access
from app.utils.api_keys import get_api_key_lookup_prefix, hash_api_key
from app.utils.compat import utcnow_naive
from tests.integration.test_utils import build_test_client


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


class _FakeDBSession:
    def __init__(self, execute_results: list[_ExecuteResult]):
        self._execute_results = list(execute_results)
        self.execute_calls = 0

    async def execute(self, _query):
        self.execute_calls += 1
        if self._execute_results:
            return self._execute_results.pop(0)
        return _ExecuteResult([])


def _sample_order_payload() -> dict:
    now = utcnow_naive()
    return {
        "type": "ON_DEMAND",
        "priority": "NORMAL",
        "pickup_address": "Warehouse A",
        "pickup_lat": 41.0,
        "pickup_lon": 29.0,
        "delivery_address": "Customer Address",
        "delivery_lat": 41.01,
        "delivery_lon": 29.01,
        "weight_kg": 2.5,
        "is_fragile": False,
        "is_temp_sensitive": False,
        "earliest_delivery": (now + timedelta(hours=1)).isoformat(),
        "latest_delivery": (now + timedelta(hours=2)).isoformat(),
        "customer_email": "customer@example.com",
        "customer_phone": "+15550001111",
        "external_ref": "ext-ref-1",
        "notes": "Leave at reception",
    }


def _mock_order_response() -> dict:
    now = utcnow_naive()
    return {
        "id": str(uuid.uuid4()),
        "type": "ON_DEMAND",
        "status": "PENDING",
        "priority": "NORMAL",
        "pickup_address": "Warehouse A",
        "pickup_lat": 41.0,
        "pickup_lon": 29.0,
        "delivery_address": "Customer Address",
        "delivery_lat": 41.01,
        "delivery_lon": 29.01,
        "weight_kg": 2.5,
        "is_fragile": False,
        "is_temp_sensitive": False,
        "earliest_pickup": None,
        "latest_pickup": None,
        "earliest_delivery": now + timedelta(hours=1),
        "latest_delivery": now + timedelta(hours=2),
        "assigned_courier_id": None,
        "route_id": None,
        "customer_email": "customer@example.com",
        "customer_phone": "+15550001111",
        "tracking_token": "tracking-token",
        "external_ref": "ext-ref-1",
        "attempt_count": 0,
        "max_attempts": 3,
        "failed_at": None,
        "customer_response_status": None,
        "customer_response_due_at": None,
        "escalated_at": None,
        "notes": "Leave at reception",
        "created_at": now,
        "updated_at": now,
    }


def _make_api_client(*, key: str, permissions: list[str], legacy: bool = False) -> ApiClient:
    if legacy:
        key_hash = hashlib.sha256(f"{settings.JWT_SECRET}:{key}".encode()).hexdigest()
        key_prefix = None
    else:
        key_hash = hash_api_key(key)
        key_prefix = get_api_key_lookup_prefix(key)

    return ApiClient(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="Integration key",
        api_key_hash=key_hash,
        api_key_prefix=key_prefix,
        permissions=permissions,
        rate_limit_per_min=120,
        is_active=True,
        created_at=utcnow_naive(),
    )


def _mock_runtime_dependencies(
    monkeypatch,
):
    async def _noop_async(*_args, **_kwargs):
        return None

    async def _mock_create_order(*_args, **_kwargs):
        return _mock_order_response()

    monkeypatch.setattr(access, "enforce_fixed_window_limit", _noop_async)
    monkeypatch.setattr(access, "log_audit_event", _noop_async)

    # Route imports the module object, so patch via the route module reference.
    from app.api import orders as orders_api

    monkeypatch.setattr(orders_api.order_service, "create_order", _mock_create_order)


def test_api_key_can_create_order_with_required_scope(monkeypatch):
    key = "lmd_valid_scoped_key"
    api_client = _make_api_client(key=key, permissions=["orders:create", "orders:read"])
    fake_db = _FakeDBSession([_ExecuteResult([api_client])])
    _mock_runtime_dependencies(monkeypatch)
    with build_test_client(db_session=fake_db) as client:
        response = client.post(
            "/api/v1/orders",
            json=_sample_order_payload(),
            headers={"X-API-Key": key},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["external_ref"] == "ext-ref-1"
    # Prefixed lookup should satisfy this request without legacy fallback query.
    assert fake_db.execute_calls == 1


def test_api_key_without_required_scope_gets_403(monkeypatch):
    key = "lmd_missing_scope_key"
    api_client = _make_api_client(key=key, permissions=["orders:read"])
    fake_db = _FakeDBSession([_ExecuteResult([api_client])])
    _mock_runtime_dependencies(monkeypatch)
    with build_test_client(db_session=fake_db) as client:
        response = client.post(
            "/api/v1/orders",
            json=_sample_order_payload(),
            headers={"X-API-Key": key},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "API key does not have required scope"
    assert fake_db.execute_calls == 1


def test_invalid_api_key_gets_401(monkeypatch):
    fake_db = _FakeDBSession([_ExecuteResult([]), _ExecuteResult([])])
    _mock_runtime_dependencies(monkeypatch)
    with build_test_client(db_session=fake_db) as client:
        response = client.post(
            "/api/v1/orders",
            json=_sample_order_payload(),
            headers={"X-API-Key": "lmd_invalid_key"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key"
    assert fake_db.execute_calls == 2


def test_legacy_api_key_hash_still_authenticates(monkeypatch):
    key = "lmd_legacy_hashed_key"
    legacy_client = _make_api_client(key=key, permissions=["orders:create"], legacy=True)
    fake_db = _FakeDBSession([_ExecuteResult([]), _ExecuteResult([legacy_client])])
    _mock_runtime_dependencies(monkeypatch)
    with build_test_client(db_session=fake_db) as client:
        response = client.post(
            "/api/v1/orders",
            json=_sample_order_payload(),
            headers={"X-API-Key": key},
        )

    assert response.status_code == 201
    assert response.json()["status"] == "PENDING"
    # First query is prefix match set, second is legacy null-prefix fallback set.
    assert fake_db.execute_calls == 2
