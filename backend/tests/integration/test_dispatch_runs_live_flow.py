from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.algorithms.vrp_solver import PlannedRoute, PlannedStop, SolverStatus, VRPSolution
from app.database import get_async_session_factory
from app.models.enums import (
    AvailabilityStatus,
    OptimizationRunStatus,
    OptimizationRunType,
    OrderStatus,
    RouteStatus,
    UserRole,
)
from app.models.optimization_run import OptimizationRun
from app.models.order import Order
from app.models.route import Route
from app.services import dispatch_service, optimization_run_service
from tests.integration.live_api_helpers import (
    auth_headers,
    build_order_payload,
    create_courier_profile,
    register_user,
    set_courier_status,
    signup_owner,
)
from tests.integration.live_test_utils import run_async

pytestmark = pytest.mark.live_infra


def _create_order(client, *, access_token: str, external_ref: str, notes: str) -> dict:
    response = client.post(
        "/api/v1/orders",
        headers=auth_headers(access_token),
        json=build_order_payload(external_ref=external_ref, notes=notes),
    )
    assert response.status_code == 201
    return response.json()


def _create_online_courier(client, *, owner_access_token: str, label: str) -> dict:
    courier_user = register_user(
        client,
        access_token=owner_access_token,
        email=f"{label}-{uuid.uuid4().hex[:10]}@example.com",
        password="Password1!",
        role=UserRole.COURIER,
    )
    courier = create_courier_profile(
        client,
        access_token=owner_access_token,
        user_id=courier_user["id"],
    )
    updated = set_courier_status(
        client,
        access_token=owner_access_token,
        courier_id=courier["id"],
        status=AvailabilityStatus.ONLINE,
    )
    assert updated["availability_status"] == AvailabilityStatus.ONLINE
    return courier


async def _seed_draft_batch_run(
    *,
    routed_order_ids: list[str],
    courier_id: str,
    unassigned_order_ids: list[str] | None = None,
) -> dict:
    order_ids = routed_order_ids + list(unassigned_order_ids or [])
    async with get_async_session_factory()() as db:
        result = await db.execute(
            select(Order).where(Order.id.in_([uuid.UUID(order_id) for order_id in order_ids]))
        )
        orders_by_id = {str(order.id): order for order in result.scalars().all()}
        routed_orders = [orders_by_id[order_id] for order_id in routed_order_ids]

        run = await optimization_run_service.create_run(
            db,
            tenant_id=routed_orders[0].tenant_id,
            run_type=OptimizationRunType.BATCH,
            task_id=f"live-draft-{uuid.uuid4().hex[:8]}",
        )

        stops: list[PlannedStop] = []
        for index, order_id in enumerate(routed_order_ids):
            order = orders_by_id[order_id]
            pickup_arrival = 9 * 3600 + (index * 1800)
            delivery_arrival = pickup_arrival + 1200
            stops.extend(
                [
                    PlannedStop(
                        order_id=order_id,
                        stop_type="PICKUP",
                        location_index=(index * 2) + 1,
                        lat=order.pickup_lat,
                        lon=order.pickup_lon,
                        arrival_time_s=pickup_arrival,
                        departure_time_s=pickup_arrival + 300,
                    ),
                    PlannedStop(
                        order_id=order_id,
                        stop_type="DELIVERY",
                        location_index=(index * 2) + 2,
                        lat=order.delivery_lat,
                        lon=order.delivery_lon,
                        arrival_time_s=delivery_arrival,
                        departure_time_s=delivery_arrival + 300,
                    ),
                ]
            )

        solution = VRPSolution(
            routes=[
                PlannedRoute(
                    courier_id=courier_id,
                    stops=stops,
                    total_distance_m=12_000,
                    total_duration_s=3_000,
                )
            ],
            unassigned_orders=list(unassigned_order_ids or []),
            total_distance_m=12_000,
            total_duration_s=3_000,
            solver_status=SolverStatus.FEASIBLE,
        )
        persisted = await dispatch_service._persist_solution(
            db,
            solution,
            routed_orders,
            run.id,
            draft=True,
        )
        await optimization_run_service.complete_run(
            db,
            run_id=run.id,
            status_value=OptimizationRunStatus.PENDING_APPROVAL,
            result={
                "routes_created": persisted["routes_created"],
                "unassigned_count": len(unassigned_order_ids or []),
                "unassigned_order_ids": list(unassigned_order_ids or []),
                "solver_status": SolverStatus.FEASIBLE.value,
                "route_cost_breakdowns": persisted["route_cost_breakdowns"],
            },
        )
        await db.commit()
        return {"run_id": str(run.id)}


async def _get_run_snapshot(run_id: str, *, order_ids: list[str]) -> dict:
    async with get_async_session_factory()() as db:
        run_result = await db.execute(
            select(OptimizationRun).where(OptimizationRun.id == uuid.UUID(run_id))
        )
        run = run_result.scalar_one()

        routes_result = await db.execute(
            select(Route)
            .where(Route.optimization_run_id == uuid.UUID(run_id))
            .options(selectinload(Route.stops))
            .order_by(Route.created_at.asc())
        )
        routes = list(routes_result.scalars().unique().all())

        orders_result = await db.execute(
            select(Order).where(Order.id.in_([uuid.UUID(order_id) for order_id in order_ids]))
        )
        orders = list(orders_result.scalars().all())
        return {
            "run_status": run.status,
            "error_message": run.error_message,
            "route_count": len(routes),
            "route_statuses": [route.status for route in routes],
            "route_ids": [str(route.id) for route in routes],
            "stop_counts": [len(route.stops) for route in routes],
            "order_statuses": {str(order.id): order.status for order in orders},
            "order_route_ids": {str(order.id): str(order.route_id) if order.route_id else None for order in orders},
            "order_courier_ids": {
                str(order.id): str(order.assigned_courier_id) if order.assigned_courier_id else None
                for order in orders
            },
        }


def test_live_draft_run_preview_and_approval_assign_orders(live_test_client):
    owner = signup_owner(live_test_client, "dispatch-approve")
    courier = _create_online_courier(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="dispatch-courier",
    )
    assigned_order = _create_order(
        live_test_client,
        access_token=owner["access_token"],
        external_ref="dispatch-order-a",
        notes="Preview me",
    )
    unassigned_order = _create_order(
        live_test_client,
        access_token=owner["access_token"],
        external_ref="dispatch-order-b",
        notes="Leave unassigned",
    )

    seeded = run_async(
        _seed_draft_batch_run(
            routed_order_ids=[assigned_order["id"]],
            courier_id=courier["id"],
            unassigned_order_ids=[unassigned_order["id"]],
        )
    )
    run_id = seeded["run_id"]

    runs_response = live_test_client.get(
        "/api/v1/routes/optimization-runs",
        headers=auth_headers(owner["access_token"]),
    )
    assert runs_response.status_code == 200
    assert [run["id"] for run in runs_response.json()] == [run_id]

    preview_response = live_test_client.get(
        f"/api/v1/routes/optimization-runs/{run_id}/preview",
        headers=auth_headers(owner["access_token"]),
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["optimization_run_id"] == run_id
    assert preview["run_status"] == OptimizationRunStatus.PENDING_APPROVAL
    assert preview["solver_status"] == SolverStatus.FEASIBLE
    assert preview["summary"]["total_routes"] == 1
    assert preview["summary"]["total_orders"] == 1
    assert preview["summary"]["total_unassigned"] == 1
    assert preview["unassigned_order_ids"] == [unassigned_order["id"]]
    assert preview["routes"][0]["courier_id"] == courier["id"]
    assert preview["routes"][0]["stop_count"] == 2
    assert preview["routes"][0]["order_count"] == 1
    assert [stop["stop_type"] for stop in preview["routes"][0]["stops"]] == ["PICKUP", "DELIVERY"]
    assert preview["explainability"]["orders_newly_assigned"] == 1
    assert preview["explainability"]["orders_now_unassigned"] == 0
    assert preview["routes"][0]["explainability"]["change_type"] == "NEW_ROUTE"

    approve_response = live_test_client.post(
        f"/api/v1/routes/optimization-runs/{run_id}/approve",
        headers=auth_headers(owner["access_token"]),
    )
    assert approve_response.status_code == 200
    assert approve_response.json() == {"approved_routes": 1}

    snapshot = run_async(
        _get_run_snapshot(
            run_id,
            order_ids=[assigned_order["id"], unassigned_order["id"]],
        )
    )
    assert snapshot["run_status"] == OptimizationRunStatus.SUCCEEDED
    assert snapshot["route_count"] == 1
    assert snapshot["route_statuses"] == [RouteStatus.PLANNED]
    assert snapshot["stop_counts"] == [2]
    assert snapshot["order_statuses"][assigned_order["id"]] == OrderStatus.ASSIGNED
    assert snapshot["order_courier_ids"][assigned_order["id"]] == courier["id"]
    assert snapshot["order_route_ids"][assigned_order["id"]] == snapshot["route_ids"][0]
    assert snapshot["order_statuses"][unassigned_order["id"]] == OrderStatus.PENDING
    assert snapshot["order_courier_ids"][unassigned_order["id"]] is None
    assert snapshot["order_route_ids"][unassigned_order["id"]] is None


def test_live_draft_run_reject_deletes_routes_and_leaves_orders_pending(live_test_client):
    owner = signup_owner(live_test_client, "dispatch-reject")
    courier = _create_online_courier(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="reject-courier",
    )
    order = _create_order(
        live_test_client,
        access_token=owner["access_token"],
        external_ref="dispatch-order-reject",
        notes="Reject me",
    )

    seeded = run_async(
        _seed_draft_batch_run(
            routed_order_ids=[order["id"]],
            courier_id=courier["id"],
        )
    )
    run_id = seeded["run_id"]

    reject_response = live_test_client.post(
        f"/api/v1/routes/optimization-runs/{run_id}/reject",
        headers=auth_headers(owner["access_token"]),
    )
    assert reject_response.status_code == 200
    assert reject_response.json() == {"deleted_routes": 1}

    snapshot = run_async(_get_run_snapshot(run_id, order_ids=[order["id"]]))
    assert snapshot["run_status"] == OptimizationRunStatus.FAILED
    assert snapshot["error_message"] == "Rejected by dispatcher"
    assert snapshot["route_count"] == 0
    assert snapshot["order_statuses"][order["id"]] == OrderStatus.PENDING
    assert snapshot["order_courier_ids"][order["id"]] is None
    assert snapshot["order_route_ids"][order["id"]] is None
