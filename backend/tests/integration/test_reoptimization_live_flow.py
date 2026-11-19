from __future__ import annotations

import uuid

import pytest
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_async_session_factory
from app.models.enums import (
    OptimizationRunStatus,
    OptimizationRunType,
    OrderStatus,
    RouteStatus,
    StopStatus,
    UserRole,
)
from app.models.order import Order
from app.models.route import Route
from tests.integration.live_api_helpers import signup_owner
from tests.integration.live_dispatch_helpers import (
    assert_run_snapshot,
    assign_order,
    create_online_courier,
    create_order,
    create_user_session,
    get_current_user,
    get_run_snapshot,
    install_route_osrm_stub,
    run_reoptimization_task_inline,
)
from tests.integration.live_test_utils import run_async

pytestmark = pytest.mark.live_infra


async def _get_reoptimization_state(*, route_id: str, order_id: str) -> dict:
    async with get_async_session_factory()() as db:
        route_result = await db.execute(
            select(Route)
            .where(Route.id == uuid.UUID(route_id))
            .options(selectinload(Route.stops))
        )
        route = route_result.scalar_one()

        order_result = await db.execute(
            select(Order).where(Order.id == uuid.UUID(order_id))
        )
        order = order_result.scalar_one()
        ordered_stops = sorted(route.stops, key=lambda stop: stop.sequence_number)
        return {
            "route_status": route.status,
            "stop_statuses": [stop.status for stop in ordered_stops],
            "stop_notes": [stop.notes for stop in ordered_stops],
            "order_status": order.status,
            "order_route_id": str(order.route_id) if order.route_id else None,
            "order_courier_id": str(order.assigned_courier_id) if order.assigned_courier_id else None,
        }


def test_live_reoptimization_worker_aborts_routes_and_requeues_orders(monkeypatch, live_test_client):
    install_route_osrm_stub(monkeypatch)

    owner = signup_owner(live_test_client, "reopt-owner")
    dispatcher = create_user_session(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="reopt-dispatcher",
        role=UserRole.DISPATCHER,
    )
    dispatcher_user = get_current_user(
        live_test_client,
        access_token=dispatcher["access_token"],
    )
    courier = create_online_courier(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="reopt-courier",
    )
    order = create_order(
        live_test_client,
        access_token=dispatcher["access_token"],
        external_ref="reopt-order",
        notes="Abort this route",
    )
    assigned_order = assign_order(
        live_test_client,
        access_token=dispatcher["access_token"],
        order_id=order["id"],
        courier_id=courier["id"],
    )
    route_id = assigned_order["route_id"]
    task_id = f"reopt-task-{uuid.uuid4().hex[:10]}"

    result = run_reoptimization_task_inline(
        task_id=task_id,
        courier_id=courier["id"],
        tenant_id=dispatcher_user["tenant_id"],
    )

    assert result == {"routes_aborted": 1, "orders_requeued": 1}

    snapshot = get_run_snapshot(task_id=task_id)
    assert_run_snapshot(
        snapshot,
        task_id=task_id,
        tenant_id=dispatcher_user["tenant_id"],
        run_type=OptimizationRunType.REOPTIMIZATION,
        status=OptimizationRunStatus.SUCCEEDED,
    )
    assert snapshot["metrics"] == result
    assert snapshot["solver_status"] is None
    assert snapshot["error_message"] is None

    state = run_async(
        _get_reoptimization_state(
            route_id=route_id,
            order_id=order["id"],
        )
    )
    assert state["route_status"] == RouteStatus.ABORTED
    assert state["stop_statuses"] == [StopStatus.FAILED, StopStatus.FAILED]
    assert state["stop_notes"] == ["Courier went offline", "Courier went offline"]
    assert state["order_status"] == OrderStatus.PENDING
    assert state["order_route_id"] is None
    assert state["order_courier_id"] is None

    redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    cooldown_key = f"optimization:reopt:{courier['id']}:cooldown"
    lock_key = f"optimization:reopt:{courier['id']}:lock"
    try:
        assert redis_client.get(cooldown_key) == "1"
        assert redis_client.get(lock_key) is None
    finally:
        redis_client.delete(cooldown_key)
        redis_client.close()


def test_live_reoptimization_worker_respects_cooldown(monkeypatch, live_test_client):
    install_route_osrm_stub(monkeypatch)

    owner = signup_owner(live_test_client, "reopt-cooldown-owner")
    dispatcher = create_user_session(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="reopt-cooldown-dispatcher",
        role=UserRole.DISPATCHER,
    )
    dispatcher_user = get_current_user(
        live_test_client,
        access_token=dispatcher["access_token"],
    )
    courier = create_online_courier(
        live_test_client,
        owner_access_token=owner["access_token"],
        label="reopt-cooldown-courier",
    )
    task_id = f"reopt-cooldown-{uuid.uuid4().hex[:10]}"
    redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    cooldown_key = f"optimization:reopt:{courier['id']}:cooldown"
    lock_key = f"optimization:reopt:{courier['id']}:lock"

    redis_client.set(cooldown_key, "1", ex=60)
    try:
        result = run_reoptimization_task_inline(
            task_id=task_id,
            courier_id=courier["id"],
            tenant_id=dispatcher_user["tenant_id"],
        )
    finally:
        redis_client.delete(cooldown_key)
        redis_client.delete(lock_key)
        redis_client.close()

    assert result == {
        "routes_aborted": 0,
        "orders_requeued": 0,
        "result": "COOLDOWN_ACTIVE",
    }

    snapshot = get_run_snapshot(task_id=task_id)
    assert_run_snapshot(
        snapshot,
        task_id=task_id,
        tenant_id=dispatcher_user["tenant_id"],
        run_type=OptimizationRunType.REOPTIMIZATION,
        status=OptimizationRunStatus.NO_OP,
    )
    assert snapshot["metrics"] == result
    assert snapshot["solver_status"] is None
    assert snapshot["error_message"] is None
