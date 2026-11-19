from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.enums import (
    OptimizationRunStatus,
    OptimizationRunType,
    OrderStatus,
    OrderType,
    RouteStatus,
    UserRole,
)
from app.models.order import Order
from app.models.route import Route
from app.services import dispatch_service, order_service
from app.services.traffic_profile_service import DurationAdjustmentResult
from app.utils.compat import utcnow_naive
from tests.integration.live_api_helpers import signup_owner
from tests.integration.live_dispatch_helpers import (
    assert_run_snapshot,
    build_linear_matrix,
    create_online_courier,
    create_order,
    create_user_session,
    get_current_user,
    get_run_snapshot,
    install_route_osrm_stub,
    run_insertion_task_inline,
)
from tests.integration.live_test_utils import run_async

pytestmark = pytest.mark.live_infra


def _future_delivery_window_within_shift() -> tuple:
    earliest_delivery = utcnow_naive().replace(
        hour=10,
        minute=0,
        second=0,
        microsecond=0,
    )
    if earliest_delivery <= utcnow_naive():
        earliest_delivery += timedelta(days=1)
    latest_delivery = earliest_delivery + timedelta(hours=2)
    return earliest_delivery, latest_delivery


def _install_on_demand_dispatch_stubs(monkeypatch) -> None:
    install_route_osrm_stub(monkeypatch)

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

    monkeypatch.setattr(dispatch_service.osrm_client, "check_osrm_health", _fake_check_osrm_health)
    monkeypatch.setattr(dispatch_service.osrm_client, "get_distance_matrix", _fake_get_distance_matrix)
    monkeypatch.setattr(
        dispatch_service.traffic_profile_service,
        "adjust_duration_matrix",
        _fake_adjust_duration_matrix,
    )


async def _get_insertion_state(*, order_id: str) -> dict:
    async with dispatch_service.get_async_session_factory()() as db:
        order_result = await db.execute(
            select(Order).where(Order.id == uuid.UUID(order_id))
        )
        order = order_result.scalar_one()

        route = None
        if order.route_id is not None:
            route_result = await db.execute(
                select(Route)
                .where(Route.id == order.route_id)
                .options(selectinload(Route.stops))
            )
            route = route_result.scalar_one()
        return {
            "order_status": order.status,
            "order_route_id": str(order.route_id) if order.route_id else None,
            "order_courier_id": str(order.assigned_courier_id) if order.assigned_courier_id else None,
            "route_status": route.status if route else None,
            "route_stop_count": len(route.stops) if route else 0,
        }


def test_live_on_demand_order_create_enqueues_and_assigns_via_insertion_worker(monkeypatch, live_test_client):
    _install_on_demand_dispatch_stubs(monkeypatch)

    owner = signup_owner(live_test_client, "ondemand-owner")
    dispatcher = create_user_session(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="ondemand-dispatcher",
        role=UserRole.DISPATCHER,
    )
    dispatcher_user = get_current_user(
        live_test_client,
        access_token=dispatcher["access_token"],
    )
    courier = create_online_courier(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="ondemand-courier",
    )
    enqueue_calls: list[dict[str, object]] = []

    def _capture_insertion_enqueue(_task, *, args=None, **_extra):
        task_id = f"insertion-task-{uuid.uuid4().hex[:10]}"
        enqueue_calls.append({"task_id": task_id, "args": list(args or [])})
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(order_service, "enqueue_task", _capture_insertion_enqueue)

    earliest_delivery, latest_delivery = _future_delivery_window_within_shift()

    order = create_order(
        live_test_client,
        access_token=dispatcher["access_token"],
        external_ref="ondemand-order",
        order_type=OrderType.ON_DEMAND,
        notes="Auto-dispatch me",
        earliest_pickup=earliest_delivery - timedelta(minutes=30),
        latest_pickup=earliest_delivery,
        earliest_delivery=earliest_delivery,
        latest_delivery=latest_delivery,
    )

    assert enqueue_calls[0] == {
        "task_id": enqueue_calls[0]["task_id"],
        "args": [order["id"], dispatcher_user["tenant_id"]],
    }
    insertion_task_id = str(enqueue_calls[0]["task_id"])

    result = run_insertion_task_inline(
        task_id=insertion_task_id,
        order_id=order["id"],
        tenant_id=dispatcher_user["tenant_id"],
    )

    assert result["result"] == "new_route"
    assert result["courier_id"] == courier["id"]
    assert result["matrix_source"] == "test"
    assert result["traffic_profile_applied"] is False
    assert result["avg_traffic_multiplier"] == 1.0

    snapshot = get_run_snapshot(task_id=insertion_task_id)
    assert_run_snapshot(
        snapshot,
        task_id=insertion_task_id,
        tenant_id=dispatcher_user["tenant_id"],
        run_type=OptimizationRunType.INSERTION,
        status=OptimizationRunStatus.SUCCEEDED,
    )
    assert snapshot["metrics"]["result"] == "new_route"
    assert snapshot["metrics"]["route_id"] == result["route_id"]
    assert snapshot["error_message"] is None

    state = run_async(_get_insertion_state(order_id=order["id"]))
    assert state["order_status"] == OrderStatus.ASSIGNED
    assert state["order_route_id"] == result["route_id"]
    assert state["order_courier_id"] == courier["id"]
    assert state["route_status"] == RouteStatus.PLANNED
    assert state["route_stop_count"] == 2


def test_live_on_demand_insertion_worker_records_no_online_courier_noop(monkeypatch, live_test_client):
    _install_on_demand_dispatch_stubs(monkeypatch)

    owner = signup_owner(live_test_client, "ondemand-noop-owner")
    dispatcher = create_user_session(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="ondemand-noop-dispatcher",
        role=UserRole.DISPATCHER,
    )
    dispatcher_user = get_current_user(
        live_test_client,
        access_token=dispatcher["access_token"],
    )
    enqueue_calls: list[dict[str, object]] = []

    def _capture_insertion_enqueue(_task, *, args=None, **_extra):
        task_id = f"insertion-noop-{uuid.uuid4().hex[:10]}"
        enqueue_calls.append({"task_id": task_id, "args": list(args or [])})
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(order_service, "enqueue_task", _capture_insertion_enqueue)

    order = create_order(
        live_test_client,
        access_token=dispatcher["access_token"],
        external_ref="ondemand-no-courier",
        order_type=OrderType.ON_DEMAND,
        notes="Nobody is online",
    )

    assert enqueue_calls[0] == {
        "task_id": enqueue_calls[0]["task_id"],
        "args": [order["id"], dispatcher_user["tenant_id"]],
    }
    insertion_task_id = str(enqueue_calls[0]["task_id"])

    result = run_insertion_task_inline(
        task_id=insertion_task_id,
        order_id=order["id"],
        tenant_id=dispatcher_user["tenant_id"],
    )

    assert result["result"] == "NO_ONLINE_COURIERS"

    snapshot = get_run_snapshot(task_id=insertion_task_id)
    assert_run_snapshot(
        snapshot,
        task_id=insertion_task_id,
        tenant_id=dispatcher_user["tenant_id"],
        run_type=OptimizationRunType.INSERTION,
        status=OptimizationRunStatus.NO_OP,
    )
    assert snapshot["metrics"]["result"] == "NO_ONLINE_COURIERS"
    assert snapshot["error_message"] is None

    state = run_async(_get_insertion_state(order_id=order["id"]))
    assert state["order_status"] == OrderStatus.PENDING
    assert state["order_route_id"] is None
    assert state["order_courier_id"] is None
    assert state["route_status"] is None
    assert state["route_stop_count"] == 0
