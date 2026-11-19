from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.algorithms.osrm_client import MatrixResult, RouteResult
from app.database import get_async_session_factory
from app.models.enums import (
    AvailabilityStatus,
    FailedOrderCustomerResponseStatus,
    OrderStatus,
    RouteStatus,
    StopType,
    UserRole,
)
from app.models.order import Order
from app.models.route import Route
from app.services import order_service
from app.utils.compat import utcnow_naive
from tests.integration.live_api_helpers import (
    auth_headers,
    build_order_payload,
    create_courier_profile,
    login,
    register_user,
    set_courier_status,
    signup_owner,
)
from tests.integration.live_test_utils import run_async

pytestmark = pytest.mark.live_infra


async def _get_order_snapshot(order_id: str) -> dict:
    async with get_async_session_factory()() as db:
        result = await db.execute(
            select(Order)
            .where(Order.id == uuid.UUID(order_id))
            .options(
                selectinload(Order.packages),
                selectinload(Order.status_history),
            )
        )
        order = result.scalar_one()
        return {
            "status": order.status,
            "notes": order.notes,
            "tracking_token": order.tracking_token,
            "earliest_delivery": order.earliest_delivery,
            "latest_delivery": order.latest_delivery,
            "assigned_courier_id": order.assigned_courier_id,
            "route_id": order.route_id,
            "customer_response_status": order.customer_response_status,
            "package_count": len(order.packages),
            "history_statuses": [entry.to_status for entry in order.status_history],
    }


def _install_fake_osrm(monkeypatch) -> None:
    async def _fake_get_route(origin, destination):
        del origin, destination
        return RouteResult(distance_m=1_000, duration_s=600, geometry="")

    async def _fake_get_distance_matrix(coords, *, use_cache=True, profile=None):
        del use_cache, profile
        size = len(coords)
        distances = [
            [0 if row == col else abs(row - col) * 1_000 for col in range(size)]
            for row in range(size)
        ]
        durations = [
            [0 if row == col else abs(row - col) * 600 for col in range(size)]
            for row in range(size)
        ]
        return MatrixResult(distances=distances, durations=durations, source="test")

    monkeypatch.setattr("app.services.route_service.osrm_client.get_route", _fake_get_route)
    monkeypatch.setattr(
        "app.services.route_service.osrm_client.get_distance_matrix",
        _fake_get_distance_matrix,
    )


async def _get_route_snapshot(route_id: str) -> dict:
    async with get_async_session_factory()() as db:
        result = await db.execute(
            select(Route)
            .where(Route.id == uuid.UUID(route_id))
            .options(selectinload(Route.stops))
        )
        route = result.scalar_one()
        ordered_stops = sorted(route.stops, key=lambda stop: stop.sequence_number)
        return {
            "status": route.status,
            "courier_id": route.courier_id,
            "started_at": route.started_at,
            "stop_count": len(ordered_stops),
            "stop_types": [stop.stop_type for stop in ordered_stops],
            "stop_order_ids": [str(stop.order_id) if stop.order_id else None for stop in ordered_stops],
        }


async def _transition_order_to_failed(order_id: str) -> None:
    async with get_async_session_factory()() as db:
        order = await order_service.get_order(db, uuid.UUID(order_id))
        for target_status in (
            OrderStatus.ASSIGNED,
            OrderStatus.PICKED_UP,
            OrderStatus.IN_TRANSIT,
            OrderStatus.FAILED,
        ):
            await order_service.apply_order_status_transition(
                db,
                order,
                target_status,
                reason=f"Live integration transition to {target_status.value}",
            )
        order.failed_at = utcnow_naive()
        order.customer_response_status = FailedOrderCustomerResponseStatus.PENDING
        order.customer_response_due_at = utcnow_naive() + timedelta(hours=24)
        await db.commit()


def test_live_order_endpoints_create_package_and_enforce_tenant_boundaries(live_test_client):
    first_owner = signup_owner(live_test_client, "orders-a")
    second_owner = signup_owner(live_test_client, "orders-b")

    first_order_response = live_test_client.post(
        "/api/v1/orders",
        headers=auth_headers(first_owner["access_token"]),
        json=build_order_payload(external_ref="order-a", notes="Desk delivery"),
    )
    assert first_order_response.status_code == 201
    first_order = first_order_response.json()

    second_order_response = live_test_client.post(
        "/api/v1/orders",
        headers=auth_headers(second_owner["access_token"]),
        json=build_order_payload(external_ref="order-b", notes="Reception"),
    )
    assert second_order_response.status_code == 201
    second_order = second_order_response.json()

    list_response = live_test_client.get(
        "/api/v1/orders",
        headers=auth_headers(first_owner["access_token"]),
    )
    assert list_response.status_code == 200
    assert list_response.headers["X-Total-Count"] == "1"
    assert [item["id"] for item in list_response.json()] == [first_order["id"]]

    get_own_order_response = live_test_client.get(
        f"/api/v1/orders/{first_order['id']}",
        headers=auth_headers(first_owner["access_token"]),
    )
    assert get_own_order_response.status_code == 200
    assert get_own_order_response.json()["external_ref"] == "order-a"

    get_other_tenant_order_response = live_test_client.get(
        f"/api/v1/orders/{second_order['id']}",
        headers=auth_headers(first_owner["access_token"]),
    )
    assert get_other_tenant_order_response.status_code == 404
    assert get_other_tenant_order_response.json()["detail"] == "Order not found"

    order_snapshot = run_async(_get_order_snapshot(first_order["id"]))
    assert order_snapshot["status"] == OrderStatus.PENDING
    assert order_snapshot["package_count"] == 1
    assert order_snapshot["history_statuses"] == [OrderStatus.PENDING]


def test_live_tracking_flow_reads_real_order_and_reschedules_failed_order(live_test_client):
    owner = signup_owner(live_test_client, "tracking")

    create_order_response = live_test_client.post(
        "/api/v1/orders",
        headers=auth_headers(owner["access_token"]),
        json=build_order_payload(external_ref="tracking-order", notes="Leave at front desk"),
    )
    assert create_order_response.status_code == 201
    order = create_order_response.json()
    tracking_token = order["tracking_token"]

    initial_tracking_response = live_test_client.get(f"/api/v1/tracking/{tracking_token}")
    assert initial_tracking_response.status_code == 200
    assert initial_tracking_response.json()["status"] == OrderStatus.PENDING
    assert initial_tracking_response.json()["can_reschedule"] is False
    assert initial_tracking_response.json()["can_update_instructions"] is False
    assert initial_tracking_response.json()["customer_instructions"] == "Leave at front desk"

    run_async(_transition_order_to_failed(order["id"]))

    failed_tracking_response = live_test_client.get(f"/api/v1/tracking/{tracking_token}")
    assert failed_tracking_response.status_code == 200
    assert failed_tracking_response.json()["status"] == OrderStatus.FAILED
    assert failed_tracking_response.json()["can_reschedule"] is True

    new_earliest_delivery = utcnow_naive() + timedelta(hours=6)
    new_latest_delivery = new_earliest_delivery + timedelta(hours=2)
    reschedule_response = live_test_client.post(
        f"/api/v1/tracking/{tracking_token}/reschedule",
        json={
            "earliest_delivery": new_earliest_delivery.isoformat(),
            "latest_delivery": new_latest_delivery.isoformat(),
            "customer_instructions": "Please call on arrival",
        },
    )
    assert reschedule_response.status_code == 200
    assert reschedule_response.json()["status"] == OrderStatus.PENDING
    assert reschedule_response.json()["can_reschedule"] is False
    assert reschedule_response.json()["customer_instructions"] == "Leave at front desk"

    order_snapshot = run_async(_get_order_snapshot(order["id"]))
    assert order_snapshot["status"] == OrderStatus.PENDING
    assert order_snapshot["assigned_courier_id"] is None
    assert order_snapshot["route_id"] is None
    assert order_snapshot["customer_response_status"] == FailedOrderCustomerResponseStatus.RESPONDED
    assert order_snapshot["notes"] == "Leave at front desk"
    assert order_snapshot["earliest_delivery"] == new_earliest_delivery
    assert order_snapshot["latest_delivery"] == new_latest_delivery
    assert order_snapshot["history_statuses"] == [
        OrderStatus.PENDING,
        OrderStatus.ASSIGNED,
        OrderStatus.PICKED_UP,
        OrderStatus.IN_TRANSIT,
        OrderStatus.FAILED,
        OrderStatus.RESCHEDULED,
        OrderStatus.PENDING,
    ]


def test_live_manual_assignment_creates_reusable_route_for_courier(
    live_test_client,
    monkeypatch,
):
    _install_fake_osrm(monkeypatch)

    owner = signup_owner(live_test_client, "routes-owner")
    courier_password = "Password1!"
    courier_email = f"courier-{uuid.uuid4().hex[:10]}@example.com"

    courier_user = register_user(
        live_test_client,
        access_token=owner["access_token"],
        email=courier_email,
        password=courier_password,
        role=UserRole.COURIER,
    )
    courier = create_courier_profile(
        live_test_client,
        access_token=owner["access_token"],
        user_id=courier_user["id"],
    )
    updated_courier = set_courier_status(
        live_test_client,
        access_token=owner["access_token"],
        courier_id=courier["id"],
        status=AvailabilityStatus.ONLINE,
    )
    assert updated_courier["availability_status"] == AvailabilityStatus.ONLINE

    courier_tokens = login(
        live_test_client,
        email=courier_email,
        password=courier_password,
    )

    first_order = live_test_client.post(
        "/api/v1/orders",
        headers=auth_headers(owner["access_token"]),
        json=build_order_payload(external_ref="route-order-a", notes="Call on arrival"),
    )
    assert first_order.status_code == 201
    first_order_body = first_order.json()

    second_order = live_test_client.post(
        "/api/v1/orders",
        headers=auth_headers(owner["access_token"]),
        json=build_order_payload(external_ref="route-order-b", notes="Side entrance"),
    )
    assert second_order.status_code == 201
    second_order_body = second_order.json()

    first_assignment = live_test_client.post(
        f"/api/v1/orders/{first_order_body['id']}/assign",
        headers=auth_headers(owner["access_token"]),
        json={"courier_id": courier["id"]},
    )
    assert first_assignment.status_code == 200
    first_assignment_body = first_assignment.json()

    second_assignment = live_test_client.post(
        f"/api/v1/orders/{second_order_body['id']}/assign",
        headers=auth_headers(owner["access_token"]),
        json={"courier_id": courier["id"]},
    )
    assert second_assignment.status_code == 200
    second_assignment_body = second_assignment.json()

    assert first_assignment_body["status"] == OrderStatus.ASSIGNED
    assert second_assignment_body["status"] == OrderStatus.ASSIGNED
    assert first_assignment_body["route_id"] == second_assignment_body["route_id"]
    assert first_assignment_body["assigned_courier_id"] == courier["id"]
    assert second_assignment_body["assigned_courier_id"] == courier["id"]

    route_id = first_assignment_body["route_id"]
    active_route_response = live_test_client.get(
        f"/api/v1/couriers/{courier['id']}/active-route",
        headers=auth_headers(courier_tokens["access_token"]),
    )
    assert active_route_response.status_code == 200
    active_route = active_route_response.json()
    assert active_route["id"] == route_id
    assert active_route["status"] == RouteStatus.PLANNED
    assert [stop["stop_type"] for stop in active_route["stops"]] == [
        StopType.PICKUP,
        StopType.DELIVERY,
        StopType.PICKUP,
        StopType.DELIVERY,
    ]

    next_stop_response = live_test_client.get(
        f"/api/v1/couriers/{courier['id']}/next-stop",
        headers=auth_headers(courier_tokens["access_token"]),
    )
    assert next_stop_response.status_code == 200
    assert next_stop_response.json()["stop_type"] == StopType.PICKUP
    assert next_stop_response.json()["order_id"] == first_order_body["id"]

    route_detail_response = live_test_client.get(
        f"/api/v1/routes/{route_id}",
        headers=auth_headers(courier_tokens["access_token"]),
    )
    assert route_detail_response.status_code == 200
    assert route_detail_response.json()["id"] == route_id

    start_route_response = live_test_client.post(
        f"/api/v1/routes/{route_id}/start",
        headers=auth_headers(courier_tokens["access_token"]),
    )
    assert start_route_response.status_code == 200
    assert start_route_response.json()["status"] == RouteStatus.ACTIVE

    route_snapshot = run_async(_get_route_snapshot(route_id))
    assert route_snapshot["status"] == RouteStatus.ACTIVE
    assert route_snapshot["courier_id"] == uuid.UUID(courier["id"])
    assert route_snapshot["started_at"] is not None
    assert route_snapshot["stop_count"] == 4
    assert route_snapshot["stop_types"] == [
        StopType.PICKUP,
        StopType.DELIVERY,
        StopType.PICKUP,
        StopType.DELIVERY,
    ]
    assert route_snapshot["stop_order_ids"] == [
        first_order_body["id"],
        first_order_body["id"],
        second_order_body["id"],
        second_order_body["id"],
    ]

    first_order_snapshot = run_async(_get_order_snapshot(first_order_body["id"]))
    second_order_snapshot = run_async(_get_order_snapshot(second_order_body["id"]))
    assert first_order_snapshot["status"] == OrderStatus.ASSIGNED
    assert second_order_snapshot["status"] == OrderStatus.ASSIGNED
    assert first_order_snapshot["assigned_courier_id"] == uuid.UUID(courier["id"])
    assert second_order_snapshot["assigned_courier_id"] == uuid.UUID(courier["id"])
    assert first_order_snapshot["route_id"] == uuid.UUID(route_id)
    assert second_order_snapshot["route_id"] == uuid.UUID(route_id)
