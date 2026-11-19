from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.enums import RouteStatus, StopStatus, StopType
from app.services import route_service
from app.utils.compat import UTC


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, route):
        self._route = route
        self.flush_count = 0
        self.added = []

    async def execute(self, _query):
        return _ScalarResult(self._route)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flush_count += 1

    async def refresh(self, _obj):
        return None


@pytest.mark.asyncio
async def test_retime_route_stops_updates_pending_arrivals_sequentially(monkeypatch):
    route_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    first_eta = datetime.now(UTC).replace(tzinfo=None).replace(microsecond=0) + timedelta(hours=2)
    stop_1 = SimpleNamespace(
        id=uuid.uuid4(),
        route_id=route_id,
        order_id=uuid.uuid4(),
        sequence_number=0,
        stop_type=StopType.PICKUP,
        lat=41.01,
        lon=29.01,
        planned_arrival=first_eta,
        status=StopStatus.PENDING,
    )
    stop_2 = SimpleNamespace(
        id=uuid.uuid4(),
        route_id=route_id,
        order_id=uuid.uuid4(),
        sequence_number=1,
        stop_type=StopType.DELIVERY,
        lat=41.02,
        lon=29.02,
        planned_arrival=first_eta,
        status=StopStatus.PENDING,
    )
    from app.models.enums import VehicleType  # local import to avoid top-level churn

    route = SimpleNamespace(
        id=route_id,
        tenant_id=tenant_id,
        courier_id=courier_id,
        # `route_service` resolves the OSRM profile from the courier's
        # vehicle_type + max_weight_kg; supply both so the helper doesn't
        # raise AttributeError when reading them.
        courier=SimpleNamespace(
            depot_lat=41.0,
            depot_lon=29.0,
            vehicle_type=VehicleType.CAR_VAN,
            max_weight_kg=500,
            vehicle_spec=None,
        ),
        created_at=datetime(2026, 2, 24, 9, 0, 0),
        started_at=None,
        status=RouteStatus.PLANNED,
        stops=[stop_1, stop_2],
        planned_distance_km=0.0,
    )
    db = _FakeDB(route)

    async def _mock_get_route(_origin, _destination, *, profile: str = "driving"):
        return SimpleNamespace(duration_s=600)

    async def _mock_get_distance_matrix(_coords, *, profile: str = "driving"):
        return SimpleNamespace(
            distances=[
                [0, 1000, 2500],
                [1000, 0, 1200],
                [2500, 1200, 0],
            ],
            durations=[
                [0, 600, 1500],
                [600, 0, 300],
                [1500, 300, 0],
            ],
            source="ors",
        )

    async def _mock_adjust_duration_matrix(_db, *, coords, durations, at_time):
        assert coords[0] == (41.0, 29.0)
        assert at_time == first_eta - timedelta(minutes=10)
        return SimpleNamespace(
            durations=durations,
            applied=False,
            avg_multiplier=1.0,
            profile_hits=0,
            profile_misses=0,
        )

    async def _mock_global_constraints(_db, principal):
        assert principal == tenant_id
        return {"service_time_minutes": 5}

    monkeypatch.setattr(route_service.osrm_client, "get_route", _mock_get_route)
    monkeypatch.setattr(route_service.osrm_client, "get_distance_matrix", _mock_get_distance_matrix)
    monkeypatch.setattr(
        route_service.traffic_profile_service,
        "adjust_duration_matrix",
        _mock_adjust_duration_matrix,
    )
    monkeypatch.setattr(
        "app.services.constraint_service.get_global_constraints",
        _mock_global_constraints,
    )

    ordered = await route_service.retime_route_stops(db, route_id)

    assert ordered == [stop_1, stop_2]
    assert stop_1.planned_arrival == first_eta
    assert stop_2.planned_arrival == first_eta + timedelta(minutes=10)
    assert route.planned_distance_km == 2.2
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_reorder_stops_retimes_route(monkeypatch):
    route_id = uuid.uuid4()
    order_a = uuid.uuid4()
    order_b = uuid.uuid4()
    tenant_id = uuid.uuid4()
    first = SimpleNamespace(
        id=uuid.uuid4(),
        order_id=order_a,
        sequence_number=0,
        stop_type=StopType.PICKUP,
        status=StopStatus.PENDING,
    )
    second = SimpleNamespace(
        id=uuid.uuid4(),
        order_id=order_a,
        sequence_number=1,
        stop_type=StopType.DELIVERY,
        status=StopStatus.PENDING,
    )
    third = SimpleNamespace(
        id=uuid.uuid4(),
        order_id=order_b,
        sequence_number=2,
        stop_type=StopType.PICKUP,
        status=StopStatus.PENDING,
    )
    fourth = SimpleNamespace(
        id=uuid.uuid4(),
        order_id=order_b,
        sequence_number=3,
        stop_type=StopType.DELIVERY,
        status=StopStatus.PENDING,
    )
    route = SimpleNamespace(
        id=route_id,
        tenant_id=tenant_id,
        status=RouteStatus.PLANNED,
        stops=[first, second, third, fourth],
    )
    db = _FakeDB(route)
    captured = {"route_id": None}

    async def _mock_retime(_db, requested_route_id):
        captured["route_id"] = requested_route_id
        return sorted(route.stops, key=lambda stop: stop.sequence_number)

    async def _mock_attach_stop_action_policy(_db, stop, *, tenant_id):
        assert tenant_id == route.tenant_id
        return stop

    monkeypatch.setattr(route_service, "retime_route_stops", _mock_retime)
    monkeypatch.setattr(route_service, "_attach_stop_action_policy", _mock_attach_stop_action_policy)

    ordered = await route_service.reorder_stops(
        db,
        route_id,
        [first.id, third.id, second.id, fourth.id],
    )

    assert [stop.sequence_number for stop in ordered] == [0, 1, 2, 3]
    assert captured["route_id"] == route_id
