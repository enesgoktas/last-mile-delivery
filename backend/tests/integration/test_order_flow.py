import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models.enums import FailedOrderCustomerResponseStatus, OrderStatus
from app.utils.access import AccessContext
from app.utils.compat import UTC
from tests.integration.test_utils import build_test_client, find_route_dependency


def _sample_order_create_payload() -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
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
        "external_ref": "ext-order-flow-1",
        "notes": "Leave at reception",
    }


def _order_response(order_id: uuid.UUID, *, status: str = "PENDING") -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
    return {
        "id": str(order_id),
        "type": "ON_DEMAND",
        "status": status,
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
        "preferred_courier_id": None,
        "route_id": None,
        "customer_email": "customer@example.com",
        "customer_phone": "+15550001111",
        "tracking_token": "tracking-token",
        "delivery_otp_code": None,
        "external_ref": "ext-order-flow-1",
        "custom_fields": None,
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


def test_create_order_passes_actor_user_id_for_jwt_access(monkeypatch):
    from app.api import orders as orders_api

    access_dep = find_route_dependency("/api/v1/orders", "access")
    dispatcher_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    order_id = uuid.uuid4()
    captured = {"actor_user_id": None}

    async def _override_access():
        return AccessContext(
            user=SimpleNamespace(id=dispatcher_id, tenant_id=tenant_id),
            api_client=None,
            source="jwt",
        )

    async def _mock_create_order(_db, _data, **kwargs):
        captured["actor_user_id"] = kwargs.get("actor_user_id")
        return _order_response(order_id)

    monkeypatch.setattr(orders_api.order_service, "create_order", _mock_create_order)

    with build_test_client(dependency_overrides={access_dep: _override_access}) as client:
        response = client.post("/api/v1/orders", json=_sample_order_create_payload())

    assert response.status_code == 201
    assert response.json()["id"] == str(order_id)
    assert captured["actor_user_id"] == dispatcher_id


def test_create_order_sets_actor_user_id_none_for_api_key_access(monkeypatch):
    from app.api import orders as orders_api

    access_dep = find_route_dependency("/api/v1/orders", "access")
    order_id = uuid.uuid4()
    captured = {"actor_user_id": "sentinel"}

    async def _override_access():
        return AccessContext(
            user=None,
            api_client=SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4()),
            source="api_key",
        )

    async def _mock_create_order(_db, _data, **kwargs):
        captured["actor_user_id"] = kwargs.get("actor_user_id")
        return _order_response(order_id)

    monkeypatch.setattr(orders_api.order_service, "create_order", _mock_create_order)

    with build_test_client(dependency_overrides={access_dep: _override_access}) as client:
        response = client.post("/api/v1/orders", json=_sample_order_create_payload())

    assert response.status_code == 201
    assert response.json()["id"] == str(order_id)
    assert captured["actor_user_id"] is None


def test_assign_order_passes_actor_user_id(monkeypatch):
    from app.api import orders as orders_api

    user_dep = find_route_dependency("/api/v1/orders/{order_id}/assign", "current_user")
    dispatcher_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    order_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    captured = {"actor_user_id": None}

    async def _override_current_user():
        return SimpleNamespace(id=dispatcher_id, tenant_id=tenant_id, role="DISPATCHER", is_active=True)

    async def _mock_assign_order(_db, requested_order_id, requested_courier_id, **kwargs):
        assert requested_order_id == order_id
        assert requested_courier_id == courier_id
        captured["actor_user_id"] = kwargs.get("actor_user_id")
        return _order_response(order_id, status="ASSIGNED") | {"assigned_courier_id": courier_id}

    monkeypatch.setattr(orders_api.order_service, "assign_order", _mock_assign_order)

    with build_test_client(dependency_overrides={user_dep: _override_current_user}) as client:
        response = client.post(
            f"/api/v1/orders/{order_id}/assign",
            json={"courier_id": str(courier_id)},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ASSIGNED"
    assert captured["actor_user_id"] == dispatcher_id


def test_set_preferred_courier_forwards_requested_pin(monkeypatch):
    from app.api import orders as orders_api

    user_dep = find_route_dependency("/api/v1/orders/{order_id}/preferred-courier", "current_user")
    tenant_id = uuid.uuid4()
    order_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    captured = {"preferred_courier_id": "sentinel"}

    async def _override_current_user():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="DISPATCHER", is_active=True)

    async def _mock_set_preferred_courier(_db, requested_order_id, requested_courier_id, **kwargs):
        assert requested_order_id == order_id
        assert kwargs["principal"].tenant_id == tenant_id
        captured["preferred_courier_id"] = requested_courier_id
        return _order_response(order_id) | {"preferred_courier_id": courier_id}

    monkeypatch.setattr(orders_api.order_service, "set_preferred_courier", _mock_set_preferred_courier)

    with build_test_client(dependency_overrides={user_dep: _override_current_user}) as client:
        response = client.put(
            f"/api/v1/orders/{order_id}/preferred-courier",
            json={"courier_id": str(courier_id)},
        )

    assert response.status_code == 200
    assert response.json()["preferred_courier_id"] == str(courier_id)
    assert captured["preferred_courier_id"] == courier_id


def test_cancel_order_passes_actor_user_id(monkeypatch):
    from app.api import orders as orders_api

    user_dep = find_route_dependency("/api/v1/orders/{order_id}/cancel", "current_user")
    dispatcher_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    order_id = uuid.uuid4()
    captured = {"actor_user_id": None}

    async def _override_current_user():
        return SimpleNamespace(id=dispatcher_id, tenant_id=tenant_id, role="DISPATCHER", is_active=True)

    async def _mock_cancel_order(_db, requested_order_id, **kwargs):
        assert requested_order_id == order_id
        captured["actor_user_id"] = kwargs.get("actor_user_id")
        return _order_response(order_id, status="CANCELLED")

    monkeypatch.setattr(orders_api.order_service, "cancel_order", _mock_cancel_order)

    with build_test_client(dependency_overrides={user_dep: _override_current_user}) as client:
        response = client.post(f"/api/v1/orders/{order_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert captured["actor_user_id"] == dispatcher_id


def test_list_orders_forwards_customer_response_status_filter(monkeypatch):
    from app.api import orders as orders_api

    user_dep = find_route_dependency("/api/v1/orders", "current_user")
    captured = {"status": None, "customer_response_status": None}
    tenant_id = uuid.uuid4()

    async def _override_current_user():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="DISPATCHER", is_active=True)

    async def _mock_count_orders(_db, principal, **kwargs):
        assert principal.tenant_id == tenant_id
        return 0

    async def _mock_get_orders(
        _db,
        principal,
        *,
        status_filter=None,
        customer_response_status_filter=None,
        **_kwargs,
    ):
        assert principal.tenant_id == tenant_id
        captured["status"] = status_filter
        captured["customer_response_status"] = customer_response_status_filter
        return []

    monkeypatch.setattr(orders_api.order_service, "count_orders", _mock_count_orders)
    monkeypatch.setattr(orders_api.order_service, "get_orders", _mock_get_orders)

    with build_test_client(dependency_overrides={user_dep: _override_current_user}) as client:
        response = client.get(
            "/api/v1/orders?status=FAILED&customer_response_status=ESCALATED&limit=20"
        )

    assert response.status_code == 200
    assert response.json() == []
    assert captured["status"] == OrderStatus.FAILED
    assert captured["customer_response_status"] == FailedOrderCustomerResponseStatus.ESCALATED
