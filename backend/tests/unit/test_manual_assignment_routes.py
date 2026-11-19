from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.enums import (
    AvailabilityStatus,
    OrderPriority,
    OrderStatus,
    OrderType,
    RouteStatus,
    VehicleType,
)
from app.models.route import Route
from app.services import order_service
from app.utils.compat import UTC


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, order, courier):
        self._results = [_ScalarResult(order), _ScalarResult(courier)]
        self.added: list[object] = []
        self.added_batches: list[list[object]] = []

    async def execute(self, _query):
        if not self._results:
            raise AssertionError("Unexpected execute call")
        return self._results.pop(0)

    def add(self, obj):
        if isinstance(obj, Route) and obj.id is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    def add_all(self, objects):
        batch = list(objects)
        self.added_batches.append(batch)

    async def flush(self):
        return None

    async def refresh(self, _obj):
        return None


def _make_order(tenant_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        status=OrderStatus.PENDING,
        type=OrderType.ON_DEMAND,
        priority=OrderPriority.NORMAL,
        pickup_address="Pickup",
        pickup_lat=41.0,
        pickup_lon=29.0,
        delivery_address="Delivery",
        delivery_lat=41.01,
        delivery_lon=29.01,
        weight_kg=1.0,
        is_fragile=False,
        is_temp_sensitive=False,
        latest_delivery=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=4),
        latest_pickup=None,
        assigned_courier_id=None,
        route_id=None,
    )


def _make_courier(courier_id: uuid.UUID, tenant_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=courier_id,
        tenant_id=tenant_id,
        availability_status=AvailabilityStatus.ONLINE,
        vehicle_type=VehicleType.CAR_VAN,
        max_weight_kg=50.0,
        constraints=None,
        zone_polygon=None,
        depot_lat=41.0,
        depot_lon=29.0,
    )


@pytest.mark.asyncio
async def test_assign_order_creates_route_and_stops_when_courier_has_none(monkeypatch):
    courier_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    order = _make_order(tenant_id)
    courier = _make_courier(courier_id, tenant_id)
    db = _FakeDB(order, courier)
    current_user_id = uuid.uuid4()
    captured = {"retimed_route_id": None}

    async def _mock_resolved_constraints(_db, _courier):
        return {"max_weight_kg": 50.0, "allow_fragile": True, "allow_temp_sensitive": True}

    async def _mock_time_feasible(_db, _order, _courier):
        return None

    async def _mock_get_active_route(_db, _courier_id, requested_tenant_id):
        assert requested_tenant_id == tenant_id
        return None

    async def _mock_retime(_db, route_id):
        captured["retimed_route_id"] = route_id
        return []

    async def _mock_transition(_db, target_order, target_status, **kwargs):
        assert kwargs["actor_user_id"] == current_user_id
        target_order.status = target_status

    monkeypatch.setattr(
        "app.services.constraint_service.get_resolved_constraints_for_courier",
        _mock_resolved_constraints,
    )
    monkeypatch.setattr(order_service, "_assert_time_window_feasible", _mock_time_feasible)
    monkeypatch.setattr("app.services.route_service.get_active_route_for_courier", _mock_get_active_route)
    monkeypatch.setattr("app.services.route_service.retime_route_stops", _mock_retime)
    monkeypatch.setattr(order_service, "apply_order_status_transition", _mock_transition)

    assigned = await order_service.assign_order(
        db,
        order.id,
        courier_id,
        actor_user_id=current_user_id,
    )

    created_route = next(obj for obj in db.added if isinstance(obj, Route))
    assert assigned is order
    assert order.assigned_courier_id == courier_id
    assert order.route_id == created_route.id
    assert order.status == OrderStatus.ASSIGNED
    assert len(db.added_batches) == 1
    assert len(db.added_batches[0]) == 2
    assert captured["retimed_route_id"] == created_route.id


@pytest.mark.asyncio
async def test_assign_order_appends_to_existing_route(monkeypatch):
    courier_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    order = _make_order(tenant_id)
    courier = _make_courier(courier_id, tenant_id)
    db = _FakeDB(order, courier)
    route_id = uuid.uuid4()
    existing_route = SimpleNamespace(
        id=route_id,
        tenant_id=tenant_id,
        status=RouteStatus.PLANNED,
        stops=[
            SimpleNamespace(sequence_number=0),
            SimpleNamespace(sequence_number=1),
        ],
    )
    captured = {"retimed_route_id": None}

    async def _mock_resolved_constraints(_db, _courier):
        return {"max_weight_kg": 50.0, "allow_fragile": True, "allow_temp_sensitive": True}

    async def _mock_time_feasible(_db, _order, _courier):
        return None

    async def _mock_get_active_route(_db, _courier_id, requested_tenant_id):
        assert requested_tenant_id == tenant_id
        return existing_route

    async def _mock_retime(_db, requested_route_id):
        captured["retimed_route_id"] = requested_route_id
        return []

    async def _mock_transition(_db, target_order, target_status, **_kwargs):
        target_order.status = target_status

    monkeypatch.setattr(
        "app.services.constraint_service.get_resolved_constraints_for_courier",
        _mock_resolved_constraints,
    )
    monkeypatch.setattr(order_service, "_assert_time_window_feasible", _mock_time_feasible)
    monkeypatch.setattr("app.services.route_service.get_active_route_for_courier", _mock_get_active_route)
    monkeypatch.setattr("app.services.route_service.retime_route_stops", _mock_retime)
    monkeypatch.setattr(order_service, "apply_order_status_transition", _mock_transition)

    await order_service.assign_order(db, order.id, courier_id)

    assert order.route_id == route_id
    assert len(db.added_batches) == 1
    pickup_stop, delivery_stop = db.added_batches[0]
    assert pickup_stop.sequence_number == 2
    assert delivery_stop.sequence_number == 3
    assert captured["retimed_route_id"] == route_id
