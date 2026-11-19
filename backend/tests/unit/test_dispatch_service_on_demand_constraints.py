import uuid
from types import SimpleNamespace

import pytest

from app.models.enums import OrderStatus, OrderType
from app.services import constraint_service, dispatch_service


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ScalarCollection:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ExecuteResult:
    def __init__(self, *, scalar_one_or_none=None, scalars_all=None):
        self._scalar_one_or_none = scalar_one_or_none
        self._scalars_all = scalars_all if scalars_all is not None else []

    def scalar_one_or_none(self):
        return self._scalar_one_or_none

    def scalars(self):
        return _ScalarCollection(self._scalars_all)

    def all(self):
        return self._scalars_all


class _FakeSession:
    def __init__(self, execute_results):
        self._execute_results = list(execute_results)
        self.added = []

    async def execute(self, _query):
        if not self._execute_results:
            return _ExecuteResult()
        return self._execute_results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def refresh(self, _obj):
        return None

    async def commit(self):
        return None


class _FakeSessionManager:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


@pytest.mark.asyncio
async def test_dispatch_on_demand_rejects_when_all_online_couriers_are_out_of_zone(monkeypatch):
    order_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        status=OrderStatus.PENDING,
        pickup_lat=41.0,
        pickup_lon=29.0,
    )
    courier = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        zone_polygon={"type": "Polygon", "coordinates": []},
    )
    fake_session = _FakeSession(
        [
            _ExecuteResult(scalar_one_or_none=order),
            _ExecuteResult(scalars_all=[courier]),
        ]
    )

    monkeypatch.setattr(
        dispatch_service,
        "get_async_session_factory",
        lambda: lambda: _FakeSessionManager(fake_session),
    )
    async def _true(): return True
    monkeypatch.setattr(dispatch_service.osrm_client, "check_osrm_health", _true)
    monkeypatch.setattr(dispatch_service, "point_in_geojson_polygon", lambda *_args, **_kwargs: False)

    result = await dispatch_service.dispatch_on_demand(str(order_id))
    assert result["result"] == "NO_ELIGIBLE_COURIERS_IN_ZONE"


@pytest.mark.asyncio
async def test_dispatch_on_demand_continues_when_zone_is_not_restrictive(monkeypatch):
    order_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        type=OrderType.SCHEDULED,
        status=OrderStatus.PENDING,
        pickup_lat=41.0,
        pickup_lon=29.0,
        delivery_lat=41.01,
        delivery_lon=29.01,
        weight_kg=1.0,
        is_fragile=False,
        is_temp_sensitive=False,
        earliest_pickup=None,
        latest_pickup=None,
        earliest_delivery=SimpleNamespace(hour=10, minute=0, second=0),
        latest_delivery=SimpleNamespace(hour=11, minute=0, second=0),
        priority="NORMAL",
        passenger_count=0,
        pickup_buffer_minutes_override=None,
    )
    courier = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        zone_polygon=None,
    )
    fake_session = _FakeSession(
        [
            _ExecuteResult(scalar_one_or_none=order),
            _ExecuteResult(scalars_all=[courier]),
        ]
    )

    class _NoCandidateResult:
        candidate = None
        reason = "no_eligible_courier"
        rejection_summary = {}

    async def _mock_constraints(_db, principal):
        assert principal == tenant_id
        return {"service_time_minutes": 5}

    async def _mock_package_summaries(_db, order_ids, *, tenant_id):
        assert order_ids == [order.id]
        assert tenant_id == order.tenant_id
        return {order.id: {"count": 1, "total_volume_cm3": 0}}

    async def _mock_build_eligible(_db, couriers, *, tenant_id):
        assert couriers == [courier]
        assert tenant_id == order.tenant_id
        return []

    async def _mock_find_best(_order, _eligible, **_kwargs):
        return _NoCandidateResult()

    monkeypatch.setattr(
        dispatch_service,
        "get_async_session_factory",
        lambda: lambda: _FakeSessionManager(fake_session),
    )
    async def _true(): return True
    monkeypatch.setattr(dispatch_service.osrm_client, "check_osrm_health", _true)
    monkeypatch.setattr(constraint_service, "get_global_constraints", _mock_constraints)
    monkeypatch.setattr(dispatch_service, "_load_order_package_summaries", _mock_package_summaries)
    monkeypatch.setattr(dispatch_service, "_build_eligible_couriers", _mock_build_eligible)
    monkeypatch.setattr(dispatch_service, "find_best_insertion", _mock_find_best)

    result = await dispatch_service.dispatch_on_demand(str(order_id))
    assert result["result"] == "no_eligible_courier"


@pytest.mark.asyncio
async def test_dispatch_on_demand_tightens_ride_pickup_window_with_buffer(monkeypatch):
    """When a RIDE order goes through on-demand dispatch and the
    ride_pickup_buffer_enabled feature is on, pickup_latest_s must be tightened
    by the configured buffer minutes so the driver arrives before the passenger
    ready time."""
    order_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    # latest_pickup at 10:00:00 → 36_000 seconds-from-midnight.
    # Buffer of 7 minutes (420s) should produce a tightened pickup_latest_s of 35_580.
    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        type=OrderType.RIDE,
        status=OrderStatus.PENDING,
        pickup_lat=41.0, pickup_lon=29.0,
        delivery_lat=41.01, delivery_lon=29.01,
        weight_kg=0.0,
        is_fragile=False,
        is_temp_sensitive=False,
        earliest_pickup=SimpleNamespace(hour=9, minute=30, second=0),
        latest_pickup=SimpleNamespace(hour=10, minute=0, second=0),
        earliest_delivery=SimpleNamespace(hour=10, minute=30, second=0),
        latest_delivery=SimpleNamespace(hour=11, minute=0, second=0),
        priority="NORMAL",
        passenger_count=3,
        pickup_buffer_minutes_override=None,
    )
    courier = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, zone_polygon=None)
    fake_session = _FakeSession(
        [
            _ExecuteResult(scalar_one_or_none=order),
            _ExecuteResult(scalars_all=[courier]),
        ]
    )

    captured: dict = {}

    async def _mock_constraints(_db, _principal):
        return {"service_time_minutes": 5}

    async def _mock_package_summaries(_db, _order_ids, *, tenant_id):
        return {order.id: {"count": 1, "total_volume_cm3": 0}}

    async def _mock_build_eligible(_db, _couriers, *, tenant_id):
        return []

    async def _mock_find_best(new_order, _eligible, **_kwargs):
        captured["new_order"] = new_order

        class _NoCandidate:
            candidate = None
            reason = "no_eligible_courier"
            rejection_summary = {}

        return _NoCandidate()

    async def _policy_with_buffer(_db, _principal):
        from app.schemas.operations_policy import OperationsPolicy

        policy = OperationsPolicy()
        policy.feature_flags.rides_enabled = True
        policy.feature_flags.ride_pickup_buffer_enabled = True
        policy.ride_settings.pickup_buffer_minutes = 7
        return policy

    monkeypatch.setattr(
        dispatch_service,
        "get_async_session_factory",
        lambda: lambda: _FakeSessionManager(fake_session),
    )
    async def _true(): return True
    monkeypatch.setattr(dispatch_service.osrm_client, "check_osrm_health", _true)
    monkeypatch.setattr(constraint_service, "get_global_constraints", _mock_constraints)
    monkeypatch.setattr(dispatch_service, "_load_order_package_summaries", _mock_package_summaries)
    monkeypatch.setattr(dispatch_service, "_build_eligible_couriers", _mock_build_eligible)
    monkeypatch.setattr(dispatch_service, "find_best_insertion", _mock_find_best)
    monkeypatch.setattr(
        dispatch_service.operations_policy_service,
        "get_operations_policy",
        _policy_with_buffer,
    )

    await dispatch_service.dispatch_on_demand(str(order_id))

    new_order = captured["new_order"]
    assert new_order.is_ride is True
    assert new_order.passenger_count == 3
    # 10:00 = 36_000s; 7-minute buffer = 420s; expect 35_580.
    assert new_order.pickup_latest_s == 35_580
