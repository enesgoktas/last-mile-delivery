import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models.enums import OrderStatus, OrderType, StopStatus, StopType
from app.services import eta_service


class _RouteResult:
    def __init__(self, route):
        self._route = route

    def scalar_one_or_none(self):
        return self._route


class _FakeDB:
    def __init__(self, route):
        self._route = route

    async def execute(self, _query):
        return _RouteResult(self._route)


class _StatsResult:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


class _StatsDB:
    def __init__(self, row):
        self._row = row

    async def execute(self, _query):
        return _StatsResult(self._row)


def _stop(
    *,
    seq: int,
    order_id: uuid.UUID,
    stop_type: StopType,
    status: StopStatus,
    lat: float = 41.0,
    lon: float = 29.0,
):
    return SimpleNamespace(
        sequence_number=seq,
        order_id=order_id,
        stop_type=stop_type,
        status=status,
        lat=lat,
        lon=lon,
    )


def test_build_route_progress_identifies_target_delivery_stop():
    target_order_id = uuid.uuid4()
    other_order_id = uuid.uuid4()
    stops = [
        _stop(seq=0, order_id=other_order_id, stop_type=StopType.PICKUP, status=StopStatus.COMPLETED),
        _stop(seq=1, order_id=other_order_id, stop_type=StopType.DELIVERY, status=StopStatus.PENDING),
        _stop(seq=2, order_id=target_order_id, stop_type=StopType.PICKUP, status=StopStatus.PENDING),
        _stop(seq=3, order_id=target_order_id, stop_type=StopType.DELIVERY, status=StopStatus.PENDING),
    ]

    progress = eta_service.build_route_progress(stops, order_id=target_order_id)

    assert progress.total_stops == 4
    assert progress.completed_stops == 1
    assert progress.remaining_stops == 3
    assert progress.next_stop_sequence == 1
    assert progress.target_stop_sequence == 3
    assert progress.route_slice_to_target is not None
    assert [s.sequence_number for s in progress.route_slice_to_target] == [1, 2, 3]


def test_build_route_progress_without_target_delivery():
    target_order_id = uuid.uuid4()
    other_order_id = uuid.uuid4()
    stops = [
        _stop(seq=0, order_id=other_order_id, stop_type=StopType.PICKUP, status=StopStatus.PENDING),
        _stop(seq=1, order_id=other_order_id, stop_type=StopType.DELIVERY, status=StopStatus.PENDING),
    ]

    progress = eta_service.build_route_progress(stops, order_id=target_order_id)

    assert progress.total_stops == 2
    assert progress.completed_stops == 0
    assert progress.remaining_stops == 2
    assert progress.next_stop_sequence == 0
    assert progress.target_stop_sequence is None
    assert progress.route_slice_to_target is None


@pytest.mark.asyncio
async def test_compute_tracking_eta_uses_route_sequence_when_target_exists(monkeypatch):
    target_order_id = uuid.uuid4()
    route_id = uuid.uuid4()
    order = SimpleNamespace(
        id=target_order_id,
        route_id=route_id,
        status=OrderStatus.IN_TRANSIT,
        delivery_lat=41.3,
        delivery_lon=29.3,
    )
    route = SimpleNamespace(
        id=route_id,
        # `eta_service` resolves the OSRM profile from the route's courier
        # before computing distances; supply a minimal stand-in so the
        # fall-back doesn't blow up on attribute access.
        courier=None,
        stops=[
            _stop(seq=1, order_id=target_order_id, stop_type=StopType.PICKUP, status=StopStatus.PENDING, lat=41.1, lon=29.1),
            _stop(seq=2, order_id=target_order_id, stop_type=StopType.DELIVERY, status=StopStatus.PENDING, lat=41.2, lon=29.2),
        ],
    )
    fake_db = _FakeDB(route)
    now = datetime(2026, 2, 24, 12, 0, 0)

    class _Matrix:
        durations = [
            [0, 60, 0],
            [0, 0, 120],
            [0, 0, 0],
        ]

    async def _mock_matrix(_coords, *, profile: str = "driving"):
        return _Matrix()

    async def _mock_route(*_args, **_kwargs):
        raise AssertionError("Direct route fallback should not be used for route_sequence ETA")

    monkeypatch.setattr(eta_service.osrm_client, "get_distance_matrix", _mock_matrix)
    monkeypatch.setattr(eta_service.osrm_client, "get_route", _mock_route)

    result = await eta_service.compute_tracking_eta(
        fake_db,
        order=order,
        courier_lat=41.0,
        courier_lon=29.0,
        now=now,
    )

    assert result.eta_source == "route_sequence"
    assert result.estimated_arrival == datetime(2026, 2, 24, 12, 3, 0)
    assert result.total_stops == 2
    assert result.completed_stops == 0
    assert result.remaining_stops == 2
    assert result.next_stop_sequence == 1
    assert result.target_stop_sequence == 2


@pytest.mark.asyncio
async def test_compute_tracking_eta_falls_back_to_direct_route(monkeypatch):
    order = SimpleNamespace(
        id=uuid.uuid4(),
        route_id=None,
        status=OrderStatus.ASSIGNED,
        delivery_lat=41.3,
        delivery_lon=29.3,
    )
    fake_db = _FakeDB(route=None)
    now = datetime(2026, 2, 24, 12, 0, 0)

    class _Route:
        duration_s = 180

    async def _mock_route(*_args, **_kwargs):
        return _Route()

    monkeypatch.setattr(eta_service.osrm_client, "get_route", _mock_route)
    monkeypatch.setattr(
        eta_service,
        "_load_historical_eta_delay_seconds",
        lambda *_args, **_kwargs: None,
    )

    result = await eta_service.compute_tracking_eta(
        fake_db,
        order=order,
        courier_lat=41.0,
        courier_lon=29.0,
        now=now,
    )

    assert result.eta_source == "direct_route"
    assert result.estimated_arrival == datetime(2026, 2, 24, 12, 3, 0)


@pytest.mark.asyncio
async def test_compute_tracking_eta_applies_historical_delay_adjustment(monkeypatch):
    order = SimpleNamespace(
        id=uuid.uuid4(),
        route_id=None,
        status=OrderStatus.ASSIGNED,
        type=OrderType.SCHEDULED,
        delivery_lat=41.3,
        delivery_lon=29.3,
    )
    fake_db = _FakeDB(route=None)
    now = datetime(2026, 2, 24, 12, 0, 0)

    class _Route:
        duration_s = 180

    async def _mock_route(*_args, **_kwargs):
        return _Route()

    async def _mock_delay(*_args, **_kwargs):
        return 120

    monkeypatch.setattr(eta_service.osrm_client, "get_route", _mock_route)
    monkeypatch.setattr(eta_service, "_load_historical_eta_delay_seconds", _mock_delay)

    result = await eta_service.compute_tracking_eta(
        fake_db,
        order=order,
        courier_lat=41.0,
        courier_lon=29.0,
        now=now,
    )

    assert result.eta_source == "direct_route+historical_delay"
    assert result.estimated_arrival == datetime(2026, 2, 24, 12, 5, 0)


@pytest.mark.asyncio
async def test_compute_tracking_eta_returns_empty_for_terminal_order():
    order = SimpleNamespace(
        id=uuid.uuid4(),
        route_id=None,
        status=OrderStatus.DELIVERED,
        delivery_lat=41.3,
        delivery_lon=29.3,
    )
    fake_db = _FakeDB(route=None)

    result = await eta_service.compute_tracking_eta(
        fake_db,
        order=order,
        courier_lat=41.0,
        courier_lon=29.0,
    )

    assert result.estimated_arrival is None
    assert result.eta_source is None


@pytest.mark.asyncio
async def test_load_historical_eta_delay_requires_minimum_samples():
    db = _StatsDB((2, 300.0))
    delay = await eta_service._load_historical_eta_delay_seconds(
        db,
        order_type=OrderType.SCHEDULED,
        planned_hour_utc=12,
    )
    assert delay is None


@pytest.mark.asyncio
async def test_load_historical_eta_delay_clamps_large_values():
    db = _StatsDB((20, 10_000.0))
    delay = await eta_service._load_historical_eta_delay_seconds(
        db,
        order_type=OrderType.SCHEDULED,
        planned_hour_utc=12,
    )
    assert delay == eta_service.settings.ETA_MAX_HISTORICAL_DELAY_SECONDS
