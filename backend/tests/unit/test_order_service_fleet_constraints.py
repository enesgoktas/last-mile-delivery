import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import settings
from app.models.enums import OrderType, StopStatus, VehicleType
from app.services import order_service
from app.utils.compat import UTC


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, route):
        self._route = route

    async def execute(self, _query):
        return _ScalarResult(self._route)


@pytest.mark.asyncio
async def test_bulk_order_motorcycle_assignment_rejects_when_stop_threshold_exceeded(monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    tenant_id = uuid.uuid4()
    order = SimpleNamespace(
        type=OrderType.BULK,
        pickup_lat=41.0,
        pickup_lon=29.0,
        delivery_lat=41.01,
        delivery_lon=29.01,
        latest_pickup=now + timedelta(hours=4),
        latest_delivery=now + timedelta(hours=8),
    )
    courier = SimpleNamespace(
        id="courier-1",
        tenant_id=tenant_id,
        depot_lat=41.0,
        depot_lon=29.0,
        vehicle_type=VehicleType.MOTORCYCLE,
    )
    route = SimpleNamespace(
        tenant_id=tenant_id,
        stops=[
            SimpleNamespace(
                status=StopStatus.PENDING,
                sequence_number=idx,
                lat=41.0 + idx * 0.001,
                lon=29.0 + idx * 0.001,
                planned_arrival=now + timedelta(minutes=idx * 5),
            )
            for idx in range(3)
        ]
    )
    db = _FakeDB(route)

    async def _no_cache(*_args, **_kwargs):
        return None

    monkeypatch.setattr(order_service, "get_courier_location_from_cache", _no_cache)
    monkeypatch.setattr(settings, "BULK_MOTORCYCLE_MAX_STOP_COUNT", 4)

    with pytest.raises(HTTPException) as exc:
        await order_service._assert_time_window_feasible(db, order, courier)

    assert exc.value.status_code == 400
    assert "Bulk orders cannot be assigned to motorcycles" in exc.value.detail


@pytest.mark.asyncio
async def test_bulk_order_motorcycle_assignment_allows_when_under_stop_threshold(monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    tenant_id = uuid.uuid4()
    order = SimpleNamespace(
        type=OrderType.BULK,
        pickup_lat=41.0,
        pickup_lon=29.0,
        delivery_lat=41.01,
        delivery_lon=29.01,
        latest_pickup=now + timedelta(hours=4),
        latest_delivery=now + timedelta(hours=8),
    )
    courier = SimpleNamespace(
        id="courier-1",
        tenant_id=tenant_id,
        depot_lat=41.0,
        depot_lon=29.0,
        vehicle_type=VehicleType.MOTORCYCLE,
    )
    route = SimpleNamespace(
        tenant_id=tenant_id,
        stops=[
            SimpleNamespace(
                status=StopStatus.PENDING,
                sequence_number=0,
                lat=41.0,
                lon=29.0,
                planned_arrival=now,
            )
        ]
    )
    db = _FakeDB(route)

    async def _no_cache(*_args, **_kwargs):
        return None

    monkeypatch.setattr(order_service, "get_courier_location_from_cache", _no_cache)
    monkeypatch.setattr(settings, "BULK_MOTORCYCLE_MAX_STOP_COUNT", 4)

    await order_service._assert_time_window_feasible(db, order, courier)
