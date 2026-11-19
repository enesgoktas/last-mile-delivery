from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models.enums import (
    OptimizationRunStatus,
    OptimizationRunType,
    OrderPriority,
    OrderStatus,
    OrderType,
)
from app.services import analytics_service
from app.utils.compat import UTC


class _ScalarRows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return _ScalarRows(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _CountResult:
    def __init__(self, value: int):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeDB:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        if not self._results:
            raise AssertionError("Unexpected execute call")
        return self._results.pop(0)


def _build_order(order_id: uuid.UUID, *, status: OrderStatus, preferred_courier_id: uuid.UUID | None = None):
    now = datetime.now(UTC).replace(tzinfo=None)
    custom_fields = None
    if preferred_courier_id is not None:
        custom_fields = {"preferred_courier_id": str(preferred_courier_id)}
    return SimpleNamespace(
        id=order_id,
        type=OrderType.ON_DEMAND,
        status=status,
        priority=OrderPriority.URGENT,
        pickup_address="Warehouse A",
        pickup_lat=41.0,
        pickup_lon=29.0,
        delivery_address="Customer Address",
        delivery_lat=41.01,
        delivery_lon=29.01,
        weight_kg=1.5,
        is_fragile=False,
        is_temp_sensitive=False,
        earliest_pickup=None,
        latest_pickup=None,
        earliest_delivery=now,
        latest_delivery=now,
        depot_id=None,
        assigned_courier_id=None,
        preferred_courier_id=preferred_courier_id,
        route_id=None,
        customer_email="ops@example.com",
        customer_phone=None,
        tracking_token="tracking-token",
        delivery_otp_code=None,
        external_ref=None,
        custom_fields=custom_fields,
        attempt_count=0,
        max_attempts=3,
        failed_at=now if status == OrderStatus.FAILED else None,
        customer_response_status=None,
        customer_response_due_at=None,
        escalated_at=None,
        notes=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_exception_center_summary_groups_pending_runs_alerts_and_orders():
    pending_run = SimpleNamespace(
        id=uuid.uuid4(),
        task_id="task-1",
        run_type=OptimizationRunType.BATCH,
        status=OptimizationRunStatus.PENDING_APPROVAL,
        routes_created=2,
        unassigned_count=1,
        solver_status="ROUTING_SUCCESS",
        metrics=None,
        error_message=None,
        started_at=datetime.now(UTC).replace(tzinfo=None),
        finished_at=None,
    )
    alert = SimpleNamespace(
        id=uuid.uuid4(),
        route_id=uuid.uuid4(),
        stop_id=None,
        order_id=None,
        courier_id=uuid.uuid4(),
        severity="CRITICAL",
        status="ACTIVE",
        alert_type="LATE_DELIVERY",
        message="Courier is behind",
        estimated_delay_seconds=900,
        created_at=datetime.now(UTC).replace(tzinfo=None),
        updated_at=datetime.now(UTC).replace(tzinfo=None),
        acknowledged_at=None,
    )
    pinned_courier_id = uuid.uuid4()
    failed_order = _build_order(uuid.uuid4(), status=OrderStatus.FAILED)
    pending_order = _build_order(
        uuid.uuid4(),
        status=OrderStatus.PENDING,
        preferred_courier_id=pinned_courier_id,
    )
    db = _FakeDB(
        [
            _ScalarsResult([pending_run]),
            _ScalarsResult([alert]),
            _ScalarsResult([failed_order]),
            _ScalarsResult([pending_order]),
            _CountResult(1),
            _CountResult(2),
            _CountResult(3),
            _CountResult(4),
        ]
    )

    summary = await analytics_service.get_exception_center_summary(
        db,
        SimpleNamespace(tenant_id=uuid.uuid4()),
    )

    assert summary["counts"] == {
        "pending_approval_runs": 1,
        "critical_alerts": 2,
        "failed_orders": 3,
        "unassigned_orders": 4,
        "total_open_items": 10,
    }
    assert summary["pending_run"] is pending_run
    assert summary["critical_alerts"][0]["severity"] == "CRITICAL"
    assert summary["failed_orders"][0] is failed_order
    assert summary["unassigned_orders"][0].preferred_courier_id == pinned_courier_id
