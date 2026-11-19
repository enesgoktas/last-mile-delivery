from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest

from app.models.enums import (
    OptimizationRunStatus,
    OptimizationRunType,
    OrderPriority,
    StopType,
    VehicleType,
)
from app.schemas.operations_policy import default_operations_policy
from app.services import optimization_run_service
from app.utils.compat import UTC


class _ScalarRows:
    def __init__(self, rows):
        self._rows = list(rows)

    def unique(self):
        return self

    def all(self):
        return list(self._rows)


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return _ScalarRows(self._rows)


class _FakeDB:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        if not self._results:
            raise AssertionError("Unexpected execute call")
        return self._results.pop(0)


def _build_order(
    order_id: uuid.UUID,
    *,
    delivery_suffix: str,
    priority: OrderPriority = OrderPriority.NORMAL,
    preferred_courier_id: uuid.UUID | None = None,
):
    now = datetime.now(UTC).replace(tzinfo=None)
    return SimpleNamespace(
        id=order_id,
        pickup_address=f"Warehouse {delivery_suffix}",
        delivery_address=f"Customer {delivery_suffix}",
        weight_kg=2.5,
        is_fragile=False,
        is_temp_sensitive=False,
        priority=priority,
        earliest_delivery=now + timedelta(hours=1),
        latest_delivery=now + timedelta(hours=4),
        preferred_courier_id=preferred_courier_id,
    )


def _build_stop(
    *,
    order_id: uuid.UUID | None,
    sequence_number: int,
    stop_type: StopType,
    delivery_offset_minutes: int,
):
    now = datetime.now(UTC).replace(tzinfo=None)
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_id=order_id,
        sequence_number=sequence_number,
        stop_type=stop_type,
        lat=41.0 + (sequence_number * 0.001),
        lon=29.0 + (sequence_number * 0.001),
        address=f"Stop {sequence_number}",
        planned_arrival=now + timedelta(minutes=delivery_offset_minutes),
    )


def _build_route(
    *,
    courier_id: uuid.UUID,
    order_ids: list[uuid.UUID],
):
    stops = []
    minute = 0
    for index, order_id in enumerate(order_ids, start=1):
        stops.append(
            _build_stop(
                order_id=order_id,
                sequence_number=(index * 2) - 1,
                stop_type=StopType.PICKUP,
                delivery_offset_minutes=minute,
            )
        )
        minute += 10
        stops.append(
            _build_stop(
                order_id=order_id,
                sequence_number=index * 2,
                stop_type=StopType.DELIVERY,
                delivery_offset_minutes=minute,
            )
        )
        minute += 20

    return SimpleNamespace(
        id=uuid.uuid4(),
        courier_id=courier_id,
        planned_distance_km=12.5,
        stops=stops,
    )


def _build_courier(courier_id: uuid.UUID):
    return SimpleNamespace(
        id=courier_id,
        vehicle_type=VehicleType.CAR_VAN,
        max_weight_kg=25.0,
        working_hours_start=time(8, 0),
        working_hours_end=time(20, 0),
    )


def _build_run(
    *,
    metrics: dict | None = None,
    run_type: OptimizationRunType = OptimizationRunType.BATCH,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        status=OptimizationRunStatus.PENDING_APPROVAL,
        solver_status="FEASIBLE",
        run_type=run_type,
        metrics=metrics,
    )


async def _get_preview(
    monkeypatch,
    *,
    run,
    draft_routes,
    current_routes,
    orders,
    couriers,
):
    async def _mock_get_run(_db, _run_id, _principal):
        return run

    monkeypatch.setattr(optimization_run_service, "get_run", _mock_get_run)
    async def _mock_get_operations_policy(_db, _tenant_id):
        return default_operations_policy()

    monkeypatch.setattr(
        optimization_run_service.operations_policy_service,
        "get_operations_policy",
        _mock_get_operations_policy,
    )
    db = _FakeDB(
        [
            _ScalarsResult(draft_routes),
            _ScalarsResult(current_routes),
            _ScalarsResult(orders),
            _ScalarsResult(couriers),
        ]
    )
    return await optimization_run_service.get_run_preview(
        db,
        run.id,
        SimpleNamespace(tenant_id=run.tenant_id),
    )


@pytest.mark.asyncio
async def test_preview_explainability_reports_no_significant_changes(monkeypatch):
    courier_id = uuid.uuid4()
    order_id = uuid.uuid4()
    order = _build_order(order_id, delivery_suffix="A")
    current_route = _build_route(courier_id=courier_id, order_ids=[order_id])
    draft_route = _build_route(courier_id=courier_id, order_ids=[order_id])
    run = _build_run(metrics={"unassigned_order_ids": []})

    preview = await _get_preview(
        monkeypatch,
        run=run,
        draft_routes=[draft_route],
        current_routes=[current_route],
        orders=[order],
        couriers=[_build_courier(courier_id)],
    )

    assert preview.explainability.no_significant_changes is True
    assert preview.explainability.routes_created == 0
    assert preview.explainability.routes_removed == 0
    assert preview.routes[0].explainability is not None
    assert preview.routes[0].explainability.change_type == "UNCHANGED"
    assert "No significant changes" in preview.routes[0].explainability.summary


@pytest.mark.asyncio
async def test_preview_explainability_reports_moves_unassigned_orders_and_pin_reason(monkeypatch):
    courier_a = uuid.uuid4()
    courier_b = uuid.uuid4()
    moved_order_id = uuid.uuid4()
    unassigned_order_id = uuid.uuid4()
    moved_order = _build_order(
        moved_order_id,
        delivery_suffix="Moved",
        preferred_courier_id=courier_b,
    )
    unassigned_order = _build_order(unassigned_order_id, delivery_suffix="Unassigned")
    current_route_a = _build_route(courier_id=courier_a, order_ids=[moved_order_id])
    current_route_b = _build_route(courier_id=courier_b, order_ids=[unassigned_order_id])
    draft_route = _build_route(courier_id=courier_b, order_ids=[moved_order_id])
    run = _build_run(metrics={"unassigned_order_ids": [str(unassigned_order_id)]})

    preview = await _get_preview(
        monkeypatch,
        run=run,
        draft_routes=[draft_route],
        current_routes=[current_route_a, current_route_b],
        orders=[moved_order, unassigned_order],
        couriers=[_build_courier(courier_b)],
    )

    assert preview.explainability.orders_moved_between_couriers == 1
    assert preview.explainability.orders_now_unassigned == 1
    assert any(reason.code == "PREFERRED_COURIER" for reason in preview.explainability.reasons)
    assert any(item.code == "UNASSIGNED_ORDERS" for item in preview.explainability.attention_items)
    route_explainability = preview.routes[0].explainability
    assert route_explainability is not None
    assert route_explainability.change_type == "MODIFIED_ROUTE"
    assert any(item.code == "PREFERRED_COURIER" for item in route_explainability.reasons)
    assert any(item.change_type == "MOVED" for item in route_explainability.changed_orders)


@pytest.mark.asyncio
async def test_preview_explainability_reports_reordered_orders_on_same_courier(monkeypatch):
    courier_id = uuid.uuid4()
    first_order_id = uuid.uuid4()
    second_order_id = uuid.uuid4()
    first_order = _build_order(first_order_id, delivery_suffix="First")
    second_order = _build_order(second_order_id, delivery_suffix="Second")
    current_route = _build_route(courier_id=courier_id, order_ids=[first_order_id, second_order_id])
    draft_route = _build_route(courier_id=courier_id, order_ids=[second_order_id, first_order_id])
    run = _build_run(metrics={"unassigned_order_ids": []})

    preview = await _get_preview(
        monkeypatch,
        run=run,
        draft_routes=[draft_route],
        current_routes=[current_route],
        orders=[first_order, second_order],
        couriers=[_build_courier(courier_id)],
    )

    assert preview.explainability.orders_reordered == 2
    route_explainability = preview.routes[0].explainability
    assert route_explainability is not None
    assert route_explainability.change_type == "MODIFIED_ROUTE"
    assert "reordered" in route_explainability.summary.lower()
    assert {
        item.change_type for item in route_explainability.changed_orders
    } == {"REORDERED"}


@pytest.mark.asyncio
async def test_preview_explainability_reports_new_and_removed_routes(monkeypatch):
    new_courier_id = uuid.uuid4()
    removed_courier_id = uuid.uuid4()
    new_order_id = uuid.uuid4()
    removed_order_id = uuid.uuid4()
    new_order = _build_order(new_order_id, delivery_suffix="New")
    removed_order = _build_order(removed_order_id, delivery_suffix="Removed")
    draft_route = _build_route(courier_id=new_courier_id, order_ids=[new_order_id])
    removed_current_route = _build_route(courier_id=removed_courier_id, order_ids=[removed_order_id])
    run = _build_run(metrics={"unassigned_order_ids": [str(removed_order_id)]})

    preview = await _get_preview(
        monkeypatch,
        run=run,
        draft_routes=[draft_route],
        current_routes=[removed_current_route],
        orders=[new_order, removed_order],
        couriers=[_build_courier(new_courier_id)],
    )

    assert preview.explainability.routes_created == 1
    assert preview.explainability.routes_removed == 1
    assert preview.explainability.orders_newly_assigned == 1
    assert preview.explainability.orders_now_unassigned == 1
    route_explainability = preview.routes[0].explainability
    assert route_explainability is not None
    assert route_explainability.change_type == "NEW_ROUTE"
    assert "New draft route" in route_explainability.summary


@pytest.mark.asyncio
async def test_preview_prefers_persisted_route_cost_breakdowns_for_draft_routes(monkeypatch):
    courier_id = uuid.uuid4()
    order_id = uuid.uuid4()
    order = _build_order(order_id, delivery_suffix="Persisted")
    draft_route = _build_route(courier_id=courier_id, order_ids=[order_id])
    persisted_breakdown = {
        "total_generalized_cost": 9876,
        "distance_cost": 120,
        "labor_cost": 220,
        "fuel_cost": 45,
        "fixed_route_cost": 500,
        "lateness_penalty": 0,
        "overtime_penalty": 15,
        "unserved_penalty": 0,
        "late_minutes": 0,
        "overtime_minutes": 2,
        "active_vehicle_count": 1,
    }
    run = _build_run(
        metrics={
            "unassigned_order_ids": [],
            "route_cost_breakdowns": {
                str(draft_route.id): persisted_breakdown,
            },
        }
    )

    preview = await _get_preview(
        monkeypatch,
        run=run,
        draft_routes=[draft_route],
        current_routes=[],
        orders=[order],
        couriers=[_build_courier(courier_id)],
    )

    assert preview.routes[0].cost_breakdown.total_generalized_cost == 9876
    assert preview.routes[0].cost_breakdown.fixed_route_cost == 500
