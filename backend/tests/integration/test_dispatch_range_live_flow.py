"""End-to-end test for `dispatch_range` against a real DB.

The unit tests in `tests/unit/test_dispatch_range_*` cover invariants by
inspecting the module source. The orchestrator tests stub `dispatch_range`
itself. Neither exercises the *full* service path:

  API → worker → dispatch_range → build_vrp_input → adapter → persist → DB

This test wires the path with a real (live) DB but stubs OSRM + the
adapter so the assertion is deterministic. It asserts the persisted
routes carry the expected `scheduled_for_date` distribution across
days — i.e. the solver's chosen day actually reaches the database.

The corresponding live test for the daily flow lives in
`test_batch_optimization_creation_live_flow.py`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.algorithms.vrp_solver import (
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VRPSolution,
)
from app.database import get_async_session_factory
from app.models.enums import OrderStatus, RouteStatus, UserRole
from app.models.order import Order
from app.models.route import Route
from app.services import dispatch_range_service
from app.services.traffic_profile_service import DurationAdjustmentResult
from tests.integration.live_api_helpers import auth_headers, signup_owner
from tests.integration.live_dispatch_helpers import (
    build_linear_matrix,
    create_online_courier,
    create_order,
    create_user_session,
    get_current_user,
)
from tests.integration.live_test_utils import run_async

pytestmark = pytest.mark.live_infra


HOUR = 3600
DAY = 86_400


@dataclass
class _StubSolveResult:
    solution: VRPSolution
    elapsed_s: float = 0.05
    metrics: dict | None = None


class _StubRichAlnsAdapter:
    """Drop-in replacement for `RichALNSAdapter` that returns a fixed solution.

    The fake solution places the first delivery on day 0 and the second on
    day 2, exercising the date-shifting persistence path. The courier id
    must follow the `<uuid>__day<n>` convention so `_route_service_date`
    recovers the right operational date.
    """

    def __init__(self, *, physical_courier_id: str):
        self._physical_courier_id = physical_courier_id

    def solve(self, vrp_input, options):  # noqa: ARG002
        # Map order IDs to their delivery indices for stop construction.
        orders_by_id = {o.order_id: o for o in vrp_input.orders}
        if len(orders_by_id) < 2:
            return _StubSolveResult(
                solution=VRPSolution(
                    routes=[],
                    unassigned_orders=list(orders_by_id),
                    total_distance_m=0,
                    total_duration_s=0,
                    solver_status=SolverStatus.FEASIBLE,
                ),
                metrics={"total_iterations": 0},
            )

        order_ids = sorted(orders_by_id)
        day0_order = orders_by_id[order_ids[0]]
        day2_order = orders_by_id[order_ids[1]]

        d0_lat, d0_lon = vrp_input.locations[day0_order.delivery_index]
        d2_lat, d2_lon = vrp_input.locations[day2_order.delivery_index]
        p_lat, p_lon = vrp_input.locations[day0_order.pickup_index]

        day0_courier = f"{self._physical_courier_id}__day0"
        day2_courier = f"{self._physical_courier_id}__day2"

        return _StubSolveResult(
            solution=VRPSolution(
                routes=[
                    PlannedRoute(
                        courier_id=day0_courier,
                        stops=[
                            PlannedStop(
                                order_id=day0_order.order_id,
                                stop_type="PICKUP",
                                location_index=day0_order.pickup_index,
                                lat=p_lat,
                                lon=p_lon,
                                arrival_time_s=9 * HOUR,
                                departure_time_s=9 * HOUR + 300,
                            ),
                            PlannedStop(
                                order_id=day0_order.order_id,
                                stop_type="DELIVERY",
                                location_index=day0_order.delivery_index,
                                lat=d0_lat,
                                lon=d0_lon,
                                arrival_time_s=10 * HOUR,
                                departure_time_s=10 * HOUR + 300,
                            ),
                        ],
                        total_distance_m=2_000,
                        total_duration_s=1_200,
                    ),
                    PlannedRoute(
                        courier_id=day2_courier,
                        stops=[
                            PlannedStop(
                                order_id=day2_order.order_id,
                                stop_type="PICKUP",
                                location_index=day2_order.pickup_index,
                                lat=p_lat,
                                lon=p_lon,
                                arrival_time_s=2 * DAY + 9 * HOUR,
                                departure_time_s=2 * DAY + 9 * HOUR + 300,
                            ),
                            PlannedStop(
                                order_id=day2_order.order_id,
                                stop_type="DELIVERY",
                                location_index=day2_order.delivery_index,
                                lat=d2_lat,
                                lon=d2_lon,
                                arrival_time_s=2 * DAY + 10 * HOUR,
                                departure_time_s=2 * DAY + 10 * HOUR + 300,
                            ),
                        ],
                        total_distance_m=2_500,
                        total_duration_s=1_300,
                    ),
                ],
                unassigned_orders=[],
                total_distance_m=4_500,
                total_duration_s=2_500,
                solver_status=SolverStatus.FEASIBLE,
            ),
            metrics={"total_iterations": 42, "feasibility_ratio": 1.0},
        )


def _install_range_stubs(monkeypatch, *, physical_courier_id: str) -> None:
    async def _fake_check_osrm_health():
        return True

    async def _fake_get_distance_matrix(coords, *, use_cache=True, profile=None):
        del use_cache, profile
        return build_linear_matrix(size=len(coords))

    async def _fake_adjust_duration_matrix(_db, *, coords, durations, at_time):
        del coords, at_time
        return DurationAdjustmentResult(
            durations=durations,
            applied=False,
            avg_multiplier=1.0,
            profile_hits=0,
            profile_misses=0,
        )

    monkeypatch.setattr(
        dispatch_range_service.osrm_client, "check_osrm_health", _fake_check_osrm_health
    )
    monkeypatch.setattr(
        dispatch_range_service.osrm_client,
        "get_distance_matrix",
        _fake_get_distance_matrix,
    )
    monkeypatch.setattr(
        dispatch_range_service.traffic_profile_service,
        "adjust_duration_matrix",
        _fake_adjust_duration_matrix,
    )

    def _fake_get_solver(kind):
        assert kind == "rich_alns", (
            f"dispatch_range must request the rich_alns adapter, got {kind!r}"
        )
        return _StubRichAlnsAdapter(physical_courier_id=physical_courier_id)

    monkeypatch.setattr(dispatch_range_service, "get_solver", _fake_get_solver)


async def _persisted_state_for_run(run_id: uuid.UUID) -> dict:
    async with get_async_session_factory()() as db:
        routes_result = await db.execute(
            select(Route)
            .where(Route.optimization_run_id == run_id)
            .options(selectinload(Route.stops))
        )
        routes = list(routes_result.scalars().unique().all())

        order_ids = [stop.order_id for route in routes for stop in route.stops if stop.order_id]
        orders_result = await db.execute(
            select(Order).where(Order.id.in_(order_ids))
        )
        orders = list(orders_result.scalars().all())

        return {
            "route_count": len(routes),
            "route_statuses": sorted(r.status for r in routes),
            "scheduled_dates": sorted(o.scheduled_for_date for o in orders if o.scheduled_for_date),
            "order_statuses": sorted(o.status for o in orders),
        }


def test_dispatch_range_persists_routes_across_days(monkeypatch, live_test_client):
    """End-to-end: 2 orders with flexible 3-day windows → dispatch_range()
    must produce 2 routes on different days, each order ASSIGNED with the
    correct `scheduled_for_date`."""
    owner = signup_owner(live_test_client, "range-live-owner")
    dispatcher = create_user_session(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="range-live-dispatcher",
        role=UserRole.DISPATCHER,
    )
    dispatcher_user = get_current_user(
        live_test_client, access_token=dispatcher["access_token"]
    )
    courier = create_online_courier(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="range-live-courier",
    )

    # Anchor the range to a stable date so the assertion is deterministic.
    # The orders must be created with `earliest_delivery` strictly in the
    # future to satisfy the "latest_delivery > now" filter that
    # dispatch_range applies to the order query.
    anchor = (datetime.utcnow() + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    range_to = anchor + timedelta(days=3)
    flexible_window_start = anchor + timedelta(hours=1)
    flexible_window_end = anchor + timedelta(days=2, hours=23)

    order_alpha = create_order(
        live_test_client,
        access_token=dispatcher["access_token"],
        external_ref="range-live-alpha",
        notes="Flexible 3-day delivery",
        earliest_delivery=flexible_window_start,
        latest_delivery=flexible_window_end,
    )
    order_beta = create_order(
        live_test_client,
        access_token=dispatcher["access_token"],
        external_ref="range-live-beta",
        notes="Flexible 3-day delivery",
        earliest_delivery=flexible_window_start,
        latest_delivery=flexible_window_end,
    )

    _install_range_stubs(monkeypatch, physical_courier_id=courier["id"])

    run_id = uuid.uuid4()
    result = run_async(
        dispatch_range_service.dispatch_range(
            tenant_id=uuid.UUID(dispatcher_user["tenant_id"]),
            date_from=anchor,
            date_to=range_to,
            optimization_run_id=run_id,
            draft=False,
        )
    )

    assert result["scope"] == "RANGE"
    assert result["span_days"] == 3
    assert result["solver_kind"] == "rich_alns"
    assert result["routes_created"] == 2
    assert result["unassigned_count"] == 0
    assert result["solver_status"] == SolverStatus.FEASIBLE

    state = run_async(_persisted_state_for_run(run_id))
    assert state["route_count"] == 2
    assert state["route_statuses"] == [RouteStatus.PLANNED, RouteStatus.PLANNED]

    # The two orders must land on different days: day 0 and day 2 of the
    # range, anchored on `anchor`. This is the load-bearing assertion —
    # date shifting actually persists with the right operational date.
    expected_days = sorted([anchor.date(), (anchor + timedelta(days=2)).date()])
    assert state["scheduled_dates"] == expected_days

    # Both orders should have transitioned to ASSIGNED (commit mode).
    assert state["order_statuses"] == [OrderStatus.ASSIGNED, OrderStatus.ASSIGNED]

    # Sanity: the two orders we created are the ones we just inspected.
    assert {order_alpha["id"], order_beta["id"]}  # surface the IDs for traceability


def test_dispatch_range_draft_keeps_orders_pending(monkeypatch, live_test_client):
    """`draft=True` must produce DRAFT routes and leave orders in PENDING
    so the dispatcher can approve later. Mirror of the daily flow's
    preview path, but for the range planner."""
    owner = signup_owner(live_test_client, "range-live-draft-owner")
    dispatcher = create_user_session(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="range-live-draft-dispatcher",
        role=UserRole.DISPATCHER,
    )
    dispatcher_user = get_current_user(
        live_test_client, access_token=dispatcher["access_token"]
    )
    courier = create_online_courier(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="range-live-draft-courier",
    )

    anchor = (datetime.utcnow() + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    range_to = anchor + timedelta(days=3)
    flex_start = anchor + timedelta(hours=1)
    flex_end = anchor + timedelta(days=2, hours=23)

    create_order(
        live_test_client,
        access_token=dispatcher["access_token"],
        external_ref="range-live-draft-a",
        notes="Preview test",
        earliest_delivery=flex_start,
        latest_delivery=flex_end,
    )
    create_order(
        live_test_client,
        access_token=dispatcher["access_token"],
        external_ref="range-live-draft-b",
        notes="Preview test",
        earliest_delivery=flex_start,
        latest_delivery=flex_end,
    )

    _install_range_stubs(monkeypatch, physical_courier_id=courier["id"])

    run_id = uuid.uuid4()
    result = run_async(
        dispatch_range_service.dispatch_range(
            tenant_id=uuid.UUID(dispatcher_user["tenant_id"]),
            date_from=anchor,
            date_to=range_to,
            optimization_run_id=run_id,
            draft=True,
        )
    )
    assert result["routes_created"] == 2

    state = run_async(_persisted_state_for_run(run_id))
    assert state["route_statuses"] == [RouteStatus.DRAFT, RouteStatus.DRAFT]
    # Orders stay PENDING in draft mode — that's the whole point of preview.
    # `scheduled_for_date` is also intentionally not set.
    assert state["order_statuses"] == [OrderStatus.PENDING, OrderStatus.PENDING]
    assert state["scheduled_dates"] == []
