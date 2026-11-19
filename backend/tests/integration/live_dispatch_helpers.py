from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.algorithms.osrm_client import MatrixResult, RouteResult
from app.database import get_async_session_factory
from app.models.enums import (
    AvailabilityStatus,
    OptimizationRunStatus,
    OptimizationRunType,
    OrderType,
    UserRole,
)
from app.models.optimization_run import OptimizationRun
from app.services.traffic_profile_service import DurationAdjustmentResult
from app.workers import optimization_tasks
from tests.integration.live_api_helpers import (
    auth_headers,
    build_order_payload,
    create_courier_profile,
    login,
    register_user,
    set_courier_status,
)
from tests.integration.live_test_utils import run_async


def build_linear_matrix(*, size: int, distance_step_m: int = 1_000, duration_step_s: int = 600) -> MatrixResult:
    distances = [
        [0 if row == col else abs(row - col) * distance_step_m for col in range(size)]
        for row in range(size)
    ]
    durations = [
        [0 if row == col else abs(row - col) * duration_step_s for col in range(size)]
        for row in range(size)
    ]
    return MatrixResult(distances=distances, durations=durations, source="test")


def get_current_user(client, *, access_token: str) -> dict:
    response = client.get(
        "/api/v1/auth/me",
        headers=auth_headers(access_token),
    )
    assert response.status_code == 200
    return response.json()


def create_order(
    client,
    *,
    access_token: str,
    external_ref: str,
    order_type: OrderType = OrderType.SCHEDULED,
    notes: str | None = None,
    earliest_pickup: datetime | None = None,
    latest_pickup: datetime | None = None,
    earliest_delivery: datetime | None = None,
    latest_delivery: datetime | None = None,
) -> dict:
    response = client.post(
        "/api/v1/orders",
        headers=auth_headers(access_token),
        json=build_order_payload(
            external_ref=external_ref,
            order_type=order_type,
            notes=notes,
            earliest_pickup=earliest_pickup,
            latest_pickup=latest_pickup,
            earliest_delivery=earliest_delivery,
            latest_delivery=latest_delivery,
        ),
    )
    assert response.status_code == 201
    return response.json()


def create_online_courier(
    client,
    *,
    owner_access_token: str,
    label: str,
) -> dict:
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


def create_user_session(
    client,
    *,
    owner_access_token: str,
    label: str,
    role: UserRole,
    password: str = "Password1!",
) -> dict:
    email = f"{label}-{uuid.uuid4().hex[:10]}@example.com"
    user = register_user(
        client,
        access_token=owner_access_token,
        email=email,
        password=password,
        role=role,
    )
    tokens = {
        **login(client, email=email, password=password),
        "email": email,
        "user_id": user["id"],
    }
    return tokens


def assign_order(
    client,
    *,
    access_token: str,
    order_id: str,
    courier_id: str,
) -> dict:
    response = client.post(
        f"/api/v1/orders/{order_id}/assign",
        headers=auth_headers(access_token),
        json={"courier_id": courier_id},
    )
    assert response.status_code == 200
    return response.json()


def install_route_osrm_stub(monkeypatch) -> None:
    async def _fake_get_route(origin, destination):
        del origin, destination
        return RouteResult(distance_m=1_000, duration_s=600, geometry="")

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

    monkeypatch.setattr("app.services.route_service.osrm_client.get_route", _fake_get_route)
    monkeypatch.setattr(
        "app.services.route_service.osrm_client.get_distance_matrix",
        _fake_get_distance_matrix,
    )
    monkeypatch.setattr(
        "app.services.route_service.traffic_profile_service.adjust_duration_matrix",
        _fake_adjust_duration_matrix,
    )


def run_bound_task_inline(task, *, task_id: str, **kwargs) -> dict:
    task.push_request(id=task_id, retries=0)
    try:
        return task.run(**kwargs)
    finally:
        task.pop_request()


def run_batch_task_inline(*, task_id: str, draft: bool, tenant_id: str) -> dict:
    return run_bound_task_inline(
        optimization_tasks.run_batch_optimization,
        task_id=task_id,
        draft=draft,
        tenant_id=tenant_id,
    )


def run_reoptimization_task_inline(*, task_id: str, courier_id: str, tenant_id: str) -> dict:
    return run_bound_task_inline(
        optimization_tasks.run_reoptimization,
        task_id=task_id,
        courier_id=courier_id,
        tenant_id=tenant_id,
    )


def run_insertion_task_inline(*, task_id: str, order_id: str, tenant_id: str) -> dict:
    return run_bound_task_inline(
        optimization_tasks.run_insertion_dispatch,
        task_id=task_id,
        order_id=order_id,
        tenant_id=tenant_id,
    )


async def get_optimization_run_snapshot(*, task_id: str) -> dict:
    async with get_async_session_factory()() as db:
        result = await db.execute(
            select(OptimizationRun).where(OptimizationRun.task_id == task_id)
        )
        run = result.scalar_one()
        return {
            "id": str(run.id),
            "tenant_id": str(run.tenant_id),
            "task_id": run.task_id,
            "run_type": run.run_type,
            "status": run.status,
            "routes_created": run.routes_created,
            "unassigned_count": run.unassigned_count,
            "solver_status": run.solver_status,
            "metrics": run.metrics,
            "error_message": run.error_message,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        }


def get_run_snapshot(*, task_id: str) -> dict:
    return run_async(get_optimization_run_snapshot(task_id=task_id))


def assert_run_snapshot(
    snapshot: dict,
    *,
    task_id: str,
    tenant_id: str,
    run_type: OptimizationRunType,
    status: OptimizationRunStatus,
) -> None:
    assert snapshot["task_id"] == task_id
    assert snapshot["tenant_id"] == tenant_id
    assert snapshot["run_type"] == run_type
    assert snapshot["status"] == status
    assert snapshot["finished_at"] is not None
