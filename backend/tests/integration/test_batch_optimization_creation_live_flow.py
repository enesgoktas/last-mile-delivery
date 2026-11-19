from __future__ import annotations

import uuid
from types import SimpleNamespace

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
from app.models.enums import (
    OptimizationRunStatus,
    OptimizationRunType,
    OrderStatus,
    RouteStatus,
    UserRole,
)
from app.models.order import Order
from app.models.route import Route
from app.services import dispatch_service
from app.services.traffic_profile_service import DurationAdjustmentResult
from tests.integration.live_api_helpers import auth_headers, signup_owner
from tests.integration.live_dispatch_helpers import (
    assert_run_snapshot,
    build_linear_matrix,
    create_online_courier,
    create_order,
    create_user_session,
    get_current_user,
    get_run_snapshot,
    run_batch_task_inline,
)
from tests.integration.live_test_utils import run_async

pytestmark = pytest.mark.live_infra


def _install_batch_dispatch_stubs(monkeypatch) -> None:
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

    def _fake_solve(vrp_input, *, initial_routes=None, time_limit_seconds=None, **_kwargs):
        del initial_routes
        del time_limit_seconds
        order = vrp_input.orders[0]
        vehicle = vrp_input.vehicles[0]
        pickup_lat, pickup_lon = vrp_input.locations[order.pickup_index]
        delivery_lat, delivery_lon = vrp_input.locations[order.delivery_index]
        return VRPSolution(
            routes=[
                PlannedRoute(
                    courier_id=vehicle.physical_courier_id or vehicle.courier_id,
                    stops=[
                        PlannedStop(
                            order_id=order.order_id,
                            stop_type="PICKUP",
                            location_index=order.pickup_index,
                            lat=pickup_lat,
                            lon=pickup_lon,
                            arrival_time_s=9 * 3600,
                            departure_time_s=(9 * 3600) + 300,
                        ),
                        PlannedStop(
                            order_id=order.order_id,
                            stop_type="DELIVERY",
                            location_index=order.delivery_index,
                            lat=delivery_lat,
                            lon=delivery_lon,
                            arrival_time_s=(9 * 3600) + 1200,
                            departure_time_s=(9 * 3600) + 1500,
                        ),
                    ],
                    total_distance_m=4_000,
                    total_duration_s=1_800,
                )
            ],
            unassigned_orders=[],
            total_distance_m=4_000,
            total_duration_s=1_800,
            solver_status=SolverStatus.FEASIBLE,
        )

    monkeypatch.setattr(dispatch_service.osrm_client, "check_osrm_health", _fake_check_osrm_health)
    monkeypatch.setattr(dispatch_service.osrm_client, "get_distance_matrix", _fake_get_distance_matrix)
    monkeypatch.setattr(
        dispatch_service.traffic_profile_service,
        "adjust_duration_matrix",
        _fake_adjust_duration_matrix,
    )
    monkeypatch.setattr(dispatch_service, "solve", _fake_solve)


async def _get_route_and_order_state(*, run_id: str, order_id: str) -> dict:
    async with get_async_session_factory()() as db:
        routes_result = await db.execute(
            select(Route)
            .where(Route.optimization_run_id == uuid.UUID(run_id))
            .options(selectinload(Route.stops))
        )
        routes = list(routes_result.scalars().unique().all())

        order_result = await db.execute(
            select(Order).where(Order.id == uuid.UUID(order_id))
        )
        order = order_result.scalar_one()
        return {
            "route_count": len(routes),
            "route_ids": [str(route.id) for route in routes],
            "route_statuses": [route.status for route in routes],
            "stop_counts": [len(route.stops) for route in routes],
            "order_status": order.status,
            "order_route_id": str(order.route_id) if order.route_id else None,
            "order_courier_id": str(order.assigned_courier_id) if order.assigned_courier_id else None,
        }


def test_live_optimize_preview_endpoint_creates_draft_run_via_worker(monkeypatch, live_test_client):
    from app.api import routes as routes_api

    _install_batch_dispatch_stubs(monkeypatch)

    owner = signup_owner(live_test_client, "opt-preview-owner")
    dispatcher = create_user_session(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="opt-preview-dispatcher",
        role=UserRole.DISPATCHER,
    )
    dispatcher_user = get_current_user(
        live_test_client,
        access_token=dispatcher["access_token"],
    )
    courier = create_online_courier(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="opt-preview-courier",
    )
    order = create_order(
        live_test_client,
        access_token=dispatcher["access_token"],
        external_ref="opt-preview-order",
        notes="Preview route",
    )

    captured_preview: dict[str, object] = {}

    def _capture_preview_task(_task, *, kwargs=None, **_extra):
        task_id = f"preview-task-{uuid.uuid4().hex[:10]}"
        captured_preview["task_id"] = task_id
        captured_preview["kwargs"] = kwargs or {}
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(routes_api, "enqueue_task", _capture_preview_task)

    response = live_test_client.post(
        "/api/v1/routes/optimize/preview",
        headers=auth_headers(dispatcher["access_token"]),
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    assert task_id == captured_preview["task_id"]
    assert captured_preview["kwargs"] == {
        "draft": True,
        "tenant_id": dispatcher_user["tenant_id"],
    }

    run_batch_task_inline(
        task_id=task_id,
        draft=True,
        tenant_id=dispatcher_user["tenant_id"],
    )

    snapshot = get_run_snapshot(task_id=task_id)
    assert_run_snapshot(
        snapshot,
        task_id=task_id,
        tenant_id=dispatcher_user["tenant_id"],
        run_type=OptimizationRunType.BATCH,
        status=OptimizationRunStatus.PENDING_APPROVAL,
    )
    assert snapshot["routes_created"] == 1
    assert snapshot["unassigned_count"] == 0
    assert snapshot["solver_status"] == SolverStatus.FEASIBLE

    preview_response = live_test_client.get(
        f"/api/v1/routes/optimization-runs/{snapshot['id']}/preview",
        headers=auth_headers(dispatcher["access_token"]),
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["optimization_run_id"] == snapshot["id"]
    assert preview["run_status"] == OptimizationRunStatus.PENDING_APPROVAL
    assert preview["solver_status"] == SolverStatus.FEASIBLE
    assert preview["summary"]["total_routes"] == 1
    assert preview["summary"]["total_orders"] == 1
    assert preview["summary"]["total_unassigned"] == 0
    assert preview["routes"][0]["courier_id"] == courier["id"]
    assert preview["routes"][0]["stop_count"] == 1
    assert preview["explainability"]["orders_newly_assigned"] == 1
    assert preview["explainability"]["routes_created"] == 1
    assert preview["routes"][0]["explainability"]["change_type"] == "NEW_ROUTE"

    state = run_async(
        _get_route_and_order_state(
            run_id=snapshot["id"],
            order_id=order["id"],
        )
    )
    assert state["route_count"] == 1
    assert state["route_statuses"] == [RouteStatus.DRAFT]
    assert state["stop_counts"] == [1]
    assert state["order_status"] == OrderStatus.PENDING
    assert state["order_route_id"] is None
    assert state["order_courier_id"] is None


def test_live_optimize_endpoint_assigns_orders_via_worker(monkeypatch, live_test_client):
    from app.api import routes as routes_api

    _install_batch_dispatch_stubs(monkeypatch)

    owner = signup_owner(live_test_client, "opt-run-owner")
    dispatcher = create_user_session(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="opt-run-dispatcher",
        role=UserRole.DISPATCHER,
    )
    dispatcher_user = get_current_user(
        live_test_client,
        access_token=dispatcher["access_token"],
    )
    courier = create_online_courier(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="opt-run-courier",
    )
    order = create_order(
        live_test_client,
        access_token=dispatcher["access_token"],
        external_ref="opt-run-order",
        notes="Dispatch now",
    )

    captured_optimize: dict[str, object] = {}

    def _capture_optimize_task(_task, *, kwargs=None, **_extra):
        task_id = f"optimize-task-{uuid.uuid4().hex[:10]}"
        captured_optimize["task_id"] = task_id
        captured_optimize["kwargs"] = kwargs or {}
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(routes_api, "enqueue_task", _capture_optimize_task)

    response = live_test_client.post(
        "/api/v1/routes/optimize",
        headers=auth_headers(dispatcher["access_token"]),
    )
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert task_id == captured_optimize["task_id"]
    assert captured_optimize["kwargs"] == {
        "tenant_id": dispatcher_user["tenant_id"],
        "draft": False,
    }

    run_batch_task_inline(
        task_id=task_id,
        draft=False,
        tenant_id=dispatcher_user["tenant_id"],
    )

    snapshot = get_run_snapshot(task_id=task_id)
    assert_run_snapshot(
        snapshot,
        task_id=task_id,
        tenant_id=dispatcher_user["tenant_id"],
        run_type=OptimizationRunType.BATCH,
        status=OptimizationRunStatus.SUCCEEDED,
    )
    assert snapshot["routes_created"] == 1
    assert snapshot["unassigned_count"] == 0
    assert snapshot["solver_status"] == SolverStatus.FEASIBLE

    state = run_async(
        _get_route_and_order_state(
            run_id=snapshot["id"],
            order_id=order["id"],
        )
    )
    assert state["route_count"] == 1
    assert state["route_statuses"] == [RouteStatus.PLANNED]
    assert state["stop_counts"] == [1]
    assert state["order_status"] == OrderStatus.ASSIGNED
    assert state["order_route_id"] == state["route_ids"][0]
    assert state["order_courier_id"] == courier["id"]
