import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.algorithms.vrp_solver import PlannedRoute, PlannedStop, SolverStatus, VRPSolution
from app.models.enums import OrderStatus, OrderType
from app.models.route import Route, RouteStop
from app.services import dispatch_service
from app.services.dispatch_service import _persist_solution


class _ScalarCollection:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ExecuteResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _ScalarCollection(self._values)


class _FakeSession:
    def __init__(self, execute_results):
        self._execute_results = list(execute_results)

    async def execute(self, _query):
        if not self._execute_results:
            return _ExecuteResult([])
        return self._execute_results.pop(0)

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
async def test_dispatch_batch_returns_empty_unassigned_list_when_no_orders(monkeypatch):
    fake_session = _FakeSession([_ExecuteResult([])])
    monkeypatch.setattr(
        dispatch_service,
        "get_async_session_factory",
        lambda: lambda: _FakeSessionManager(fake_session),
    )
    async def _true(): return True
    monkeypatch.setattr(dispatch_service.osrm_client, "check_osrm_health", _true)

    result = await dispatch_service.dispatch_batch()
    assert result["solver_status"] == "NO_ORDERS"
    assert result["unassigned_count"] == 0
    assert result["unassigned_order_ids"] == []


@pytest.mark.asyncio
async def test_dispatch_batch_returns_order_ids_when_no_couriers(monkeypatch):
    tenant_id = uuid.uuid4()
    orders = [
        SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id),
        SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id),
    ]
    fake_session = _FakeSession(
        [
            _ExecuteResult(orders),
            _ExecuteResult([]),
        ]
    )
    monkeypatch.setattr(
        dispatch_service,
        "get_async_session_factory",
        lambda: lambda: _FakeSessionManager(fake_session),
    )
    async def _true(): return True
    monkeypatch.setattr(dispatch_service.osrm_client, "check_osrm_health", _true)

    result = await dispatch_service.dispatch_batch(tenant_id=tenant_id)
    assert result["solver_status"] == "NO_COURIERS"
    assert result["unassigned_count"] == 2
    assert result["unassigned_order_ids"] == [str(order.id) for order in orders]


def test_initial_home_depot_pickup_is_hidden_until_route_work_starts():
    order = SimpleNamespace(type=OrderType.SCHEDULED)
    depot_loc = (41.0, 29.0)

    assert dispatch_service._should_hide_initial_depot_pickup(
        stop_type="PICKUP",
        stop_lat=41.0001,
        stop_lon=29.0001,
        order=order,
        courier_depot_loc=depot_loc,
        warehouse_locs=None,
        route_work_started=False,
    )
    assert not dispatch_service._should_hide_initial_depot_pickup(
        stop_type="PICKUP",
        stop_lat=41.0001,
        stop_lon=29.0001,
        order=order,
        courier_depot_loc=depot_loc,
        warehouse_locs=None,
        route_work_started=True,
    )


def test_pickup_at_any_known_warehouse_is_hidden_during_preload():
    """Multi-warehouse: courier loads multiple orders at one warehouse before
    leaving. Each order's PICKUP must collapse into a single load-up event
    even when the warehouse isn't the courier's legacy home depot."""
    order = SimpleNamespace(type=OrderType.SCHEDULED)
    courier_home = (41.0, 29.0)
    warehouses = {(41.0431, 28.9026), (40.9923, 29.0274)}

    assert dispatch_service._should_hide_initial_depot_pickup(
        stop_type="PICKUP",
        stop_lat=41.0431,
        stop_lon=28.9026,
        order=order,
        courier_depot_loc=courier_home,
        warehouse_locs=warehouses,
        route_work_started=False,
    )
    assert not dispatch_service._should_hide_initial_depot_pickup(
        stop_type="PICKUP",
        stop_lat=41.0431,
        stop_lon=28.9026,
        order=order,
        courier_depot_loc=courier_home,
        warehouse_locs=warehouses,
        route_work_started=True,
    )


def test_other_depot_and_reverse_pickups_stay_visible():
    depot_loc = (41.0, 29.0)

    assert not dispatch_service._should_hide_initial_depot_pickup(
        stop_type="PICKUP",
        stop_lat=41.05,
        stop_lon=29.05,
        order=SimpleNamespace(type=OrderType.SCHEDULED),
        courier_depot_loc=depot_loc,
        warehouse_locs=None,
        route_work_started=False,
    )
    assert not dispatch_service._should_hide_initial_depot_pickup(
        stop_type="PICKUP",
        stop_lat=41.0001,
        stop_lon=29.0001,
        order=SimpleNamespace(type=OrderType.REVERSE),
        courier_depot_loc=depot_loc,
        warehouse_locs=None,
        route_work_started=False,
    )


# ---------------------------------------------------------------------------
# _persist_solution end-to-end depot persistence tests
# ---------------------------------------------------------------------------

def _fake_order(
    *,
    order_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    order_type: OrderType = OrderType.SCHEDULED,
    earliest_delivery: datetime | None = None,
) -> SimpleNamespace:
    oid = order_id or uuid.uuid4()
    tid = tenant_id or uuid.uuid4()
    return SimpleNamespace(
        id=oid,
        tenant_id=tid,
        type=order_type,
        status=OrderStatus.PENDING,
        pickup_address="Depot St 1",
        delivery_address="Customer Ave 2",
        assigned_courier_id=None,
        route_id=None,
        scheduled_for_date=None,
        earliest_delivery=earliest_delivery or datetime(2026, 5, 6, 9, 0),
    )


def _planned_stop(
    *,
    order_id: uuid.UUID,
    stop_type: str,
    lat: float,
    lon: float,
    arrival_time_s: int = 28800,
) -> PlannedStop:
    return PlannedStop(
        order_id=str(order_id),
        stop_type=stop_type,
        location_index=0,
        lat=lat,
        lon=lon,
        arrival_time_s=arrival_time_s,
        departure_time_s=arrival_time_s + 300,
    )


class _FakeDB:
    """Minimal async-session stub that records added objects."""

    def __init__(self):
        self.added: list = []
        self._route_id = uuid.uuid4()

    async def flush(self):
        # Assign id to any unflushed Route objects
        for obj in self.added:
            if isinstance(obj, Route) and not hasattr(obj, "id"):
                obj.id = self._route_id

    async def refresh(self, obj):
        if isinstance(obj, Route) and not obj.id:
            obj.id = self._route_id

    def add(self, obj):
        # Assign id to Route on add so refresh works
        if isinstance(obj, Route):
            obj.id = self._route_id
        self.added.append(obj)

    def add_all(self, objs):
        for obj in objs:
            self.add(obj)


def _make_solution(courier_id: str, stops: list[PlannedStop]) -> VRPSolution:
    return VRPSolution(
        routes=[
            PlannedRoute(
                courier_id=courier_id,
                stops=stops,
                total_distance_m=5_000,
                total_duration_s=1_800,
                route_start_time_s=28800,
                route_end_time_s=30600,
            )
        ],
        unassigned_orders=[],
        total_distance_m=5_000,
        total_duration_s=1_800,
        solver_status=SolverStatus.FEASIBLE,
    )


@pytest.mark.asyncio
async def test_persist_solution_hides_initial_depot_pickup_and_still_assigns_order(monkeypatch):
    """Initial pickup at the courier's own depot is skipped as a stop but the
    order is still transitioned to ASSIGNED."""
    courier_id = str(uuid.uuid4())
    order = _fake_order()
    depot_loc = (41.0, 29.0)

    # Pickup is at the exact depot location, delivery is elsewhere — no prior work
    stops = [
        _planned_stop(order_id=order.id, stop_type="PICKUP", lat=41.0001, lon=29.0001),
        _planned_stop(order_id=order.id, stop_type="DELIVERY", lat=41.1, lon=29.1),
    ]
    solution = _make_solution(courier_id, stops)

    assigned_orders: list[uuid.UUID] = []

    async def _fake_transition(db, order_obj, new_status, *, reason=""):
        assigned_orders.append(order_obj.id)

    async def _fake_bulk_transition(db, transitions, *, reason="", **kwargs):
        for order_obj, _new_status in transitions:
            assigned_orders.append(order_obj.id)

    monkeypatch.setattr(dispatch_service, "apply_order_status_transition", _fake_transition)
    monkeypatch.setattr(dispatch_service, "apply_order_status_transitions_bulk", _fake_bulk_transition)

    db = _FakeDB()
    await _persist_solution(
        db,
        solution,
        [order],
        uuid.uuid4(),
        draft=False,
        courier_depot_locs={courier_id: depot_loc},
    )

    route_stops = [obj for obj in db.added if isinstance(obj, RouteStop)]
    stop_types = [s.stop_type.value for s in route_stops]

    # The hidden preload pickup must not produce a stop row
    assert "PICKUP" not in stop_types
    # The delivery stop must still be present
    assert "DELIVERY" in stop_types
    # The order must still be assigned
    assert order.id in assigned_orders


@pytest.mark.asyncio
async def test_persist_solution_anchors_planned_arrivals_to_service_day(monkeypatch):
    courier_id = str(uuid.uuid4())
    order = _fake_order(earliest_delivery=datetime(2026, 5, 6, 11, 0))
    stops = [
        _planned_stop(
            order_id=order.id,
            stop_type="DELIVERY",
            lat=41.1,
            lon=29.1,
            arrival_time_s=12 * 3600,
        )
    ]
    solution = _make_solution(courier_id, stops)

    async def _fake_transition(db, order_obj, new_status, *, reason=""):
        order_obj.status = new_status

    async def _fake_bulk_transition(db, transitions, *, reason="", **kwargs):
        for o, s in transitions:
            o.status = s

    monkeypatch.setattr(dispatch_service, "apply_order_status_transition", _fake_transition)
    monkeypatch.setattr(dispatch_service, "apply_order_status_transitions_bulk", _fake_bulk_transition)

    db = _FakeDB()
    await _persist_solution(
        db,
        solution,
        [order],
        uuid.uuid4(),
        draft=False,
        service_anchor=datetime(2026, 5, 6),
    )

    route_stop = next(obj for obj in db.added if isinstance(obj, RouteStop))
    assert route_stop.planned_arrival == datetime(2026, 5, 6, 12, 0)
    assert order.scheduled_for_date == datetime(2026, 5, 6).date()


@pytest.mark.asyncio
async def test_persist_solution_retains_mid_route_depot_pickup(monkeypatch):
    """A pickup at the courier's own depot that occurs after a delivery (i.e.,
    route work has started) must remain as a visible stop."""
    courier_id = str(uuid.uuid4())
    order_a = _fake_order()
    order_b = _fake_order(tenant_id=order_a.tenant_id)
    depot_loc = (41.0, 29.0)

    stops = [
        # First delivery — establishes route_work_started=True
        _planned_stop(order_id=order_a.id, stop_type="DELIVERY", lat=41.1, lon=29.1),
        # Pickup back at the depot *after* delivery work — must stay visible
        _planned_stop(order_id=order_b.id, stop_type="PICKUP", lat=41.0001, lon=29.0001),
        _planned_stop(order_id=order_b.id, stop_type="DELIVERY", lat=41.2, lon=29.2),
    ]
    solution = _make_solution(courier_id, stops)

    async def _fake_transition(db, order_obj, new_status, *, reason=""):
        pass

    async def _fake_bulk_transition(db, transitions, *, reason="", **kwargs):
        pass

    monkeypatch.setattr(dispatch_service, "apply_order_status_transition", _fake_transition)
    monkeypatch.setattr(dispatch_service, "apply_order_status_transitions_bulk", _fake_bulk_transition)

    db = _FakeDB()
    await _persist_solution(
        db,
        solution,
        [order_a, order_b],
        uuid.uuid4(),
        draft=False,
        courier_depot_locs={courier_id: depot_loc},
    )

    route_stops = [obj for obj in db.added if isinstance(obj, RouteStop)]
    stop_types = [s.stop_type.value for s in route_stops]

    # Two deliveries + the mid-route pickup = 3 stops
    assert stop_types.count("PICKUP") == 1
    assert stop_types.count("DELIVERY") == 2


@pytest.mark.asyncio
async def test_persist_solution_retains_pickup_at_different_depot(monkeypatch):
    """A pickup at a warehouse that is NOT the courier's own start depot must
    always be retained as a visible stop, even at route start."""
    courier_id = str(uuid.uuid4())
    order = _fake_order()
    courier_depot_loc = (41.0, 29.0)
    other_depot_lat, other_depot_lon = 41.05, 29.05  # >0.009° away

    stops = [
        _planned_stop(order_id=order.id, stop_type="PICKUP", lat=other_depot_lat, lon=other_depot_lon),
        _planned_stop(order_id=order.id, stop_type="DELIVERY", lat=41.1, lon=29.1),
    ]
    solution = _make_solution(courier_id, stops)

    async def _fake_transition(db, order_obj, new_status, *, reason=""):
        pass

    async def _fake_bulk_transition(db, transitions, *, reason="", **kwargs):
        pass

    monkeypatch.setattr(dispatch_service, "apply_order_status_transition", _fake_transition)
    monkeypatch.setattr(dispatch_service, "apply_order_status_transitions_bulk", _fake_bulk_transition)

    db = _FakeDB()
    await _persist_solution(
        db,
        solution,
        [order],
        uuid.uuid4(),
        draft=False,
        courier_depot_locs={courier_id: courier_depot_loc},
    )

    route_stops = [obj for obj in db.added if isinstance(obj, RouteStop)]
    stop_types = [s.stop_type.value for s in route_stops]

    assert "PICKUP" in stop_types
    assert "DELIVERY" in stop_types
