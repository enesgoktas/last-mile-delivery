import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.utils.compat import UTC
from tests.integration.test_utils import build_test_client, find_route_dependency


def _order_response(order_id: uuid.UUID) -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
    return {
        "id": str(order_id),
        "type": "SCHEDULED",
        "status": "RESCHEDULED",
        "priority": "NORMAL",
        "pickup_address": "Warehouse A",
        "pickup_lat": 41.0,
        "pickup_lon": 29.0,
        "delivery_address": "Customer Address",
        "delivery_lat": 41.01,
        "delivery_lon": 29.01,
        "weight_kg": 3.2,
        "is_fragile": False,
        "is_temp_sensitive": False,
        "earliest_pickup": None,
        "latest_pickup": None,
        "earliest_delivery": (now + timedelta(hours=3)).isoformat(),
        "latest_delivery": (now + timedelta(hours=5)).isoformat(),
        "assigned_courier_id": None,
        "route_id": None,
        "customer_email": "customer@example.com",
        "customer_phone": "+15550001111",
        "tracking_token": "tracking-token",
        "external_ref": "ext-reschedule-1",
        "attempt_count": 1,
        "max_attempts": 3,
        "failed_at": now.isoformat(),
        "customer_response_status": "RESPONDED",
        "customer_response_due_at": None,
        "escalated_at": None,
        "notes": "rescheduled by dispatcher",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def test_orders_reschedule_endpoint_calls_service_with_requested_window(monkeypatch):
    from app.api import orders as orders_api

    role_dep = find_route_dependency("/api/v1/orders/{order_id}/reschedule", "current_user")

    dispatcher_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    async def _override_role():
        return SimpleNamespace(id=dispatcher_id, tenant_id=tenant_id, role="DISPATCHER", is_active=True)

    captured = {"order_id": None, "earliest": None, "latest": None, "actor_user_id": None}

    async def _mock_reschedule(
        _db,
        order_id,
        earliest_delivery,
        latest_delivery,
        *,
        principal=None,
        actor_user_id=None,
    ):
        assert principal.tenant_id == tenant_id
        captured["order_id"] = order_id
        captured["earliest"] = earliest_delivery
        captured["latest"] = latest_delivery
        captured["actor_user_id"] = actor_user_id
        return _order_response(order_id)

    monkeypatch.setattr(orders_api.order_service, "reschedule_order", _mock_reschedule)

    order_id = uuid.uuid4()
    earliest = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=4)
    latest = earliest + timedelta(hours=2)

    with build_test_client(dependency_overrides={role_dep: _override_role}) as client:
        response = client.post(
            f"/api/v1/orders/{order_id}/reschedule",
            json={
                "earliest_delivery": earliest.isoformat(),
                "latest_delivery": latest.isoformat(),
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "RESCHEDULED"
    assert captured["order_id"] == order_id
    assert captured["earliest"] == earliest
    assert captured["latest"] == latest
    assert captured["actor_user_id"] == dispatcher_id
