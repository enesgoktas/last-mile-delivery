"""Layer 0 — verify the new lifecycle-history helpers and dispatch
decision-log capture work as advertised.

Pure-Python tests (no real DB): a stub AsyncSession records every
`db.add(...)` so we can assert the shape of the rows the helpers would
emit. The same stubbing approach the rest of the unit suite uses.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.models.courier_history import CourierAvailabilityHistory
from app.models.dispatch_decision_log import DispatchDecisionLog
from app.models.enums import (
    AvailabilityStatus,
    DispatchDecisionKind,
    RouteStatus,
    StopStatus,
)
from app.models.route_history import RouteStatusHistory
from app.models.stop_history import StopStatusHistory
from app.services.dispatch_decision_log_service import (
    record_decisions_for_solution,
)
from app.services.lifecycle_history_service import (
    apply_route_status_transition,
    apply_stop_status_transition,
    record_courier_availability_change,
)


class _StubSession:
    """Minimal AsyncSession-compatible stub.

    Records every `add` and `add_all` call so tests can introspect the
    rows the helpers tried to persist. `flush` is a no-op coroutine.
    """

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:  # noqa: D401
        self.added.append(obj)

    def add_all(self, objs) -> None:
        self.added.extend(objs)

    async def flush(self) -> None:  # noqa: D401
        return None


def _run(coro):
    return asyncio.run(coro)


# ── apply_route_status_transition ──────────────────────────────────────


def test_route_status_transition_noop_when_same():
    db = _StubSession()
    route = SimpleNamespace(id=uuid.uuid4(), status=RouteStatus.ACTIVE)
    applied = _run(apply_route_status_transition(db, route, RouteStatus.ACTIVE))
    assert applied is False
    assert db.added == []
    assert route.status == RouteStatus.ACTIVE


def test_route_status_transition_records_history():
    db = _StubSession()
    route_id = uuid.uuid4()
    actor = uuid.uuid4()
    route = SimpleNamespace(id=route_id, status=RouteStatus.PLANNED)
    applied = _run(
        apply_route_status_transition(
            db,
            route,
            RouteStatus.ACTIVE,
            actor_user_id=actor,
            reason="Courier started route",
        )
    )
    assert applied is True
    assert route.status == RouteStatus.ACTIVE
    assert len(db.added) == 1
    row = db.added[0]
    assert isinstance(row, RouteStatusHistory)
    assert row.route_id == route_id
    assert row.from_status == RouteStatus.PLANNED
    assert row.to_status == RouteStatus.ACTIVE
    assert row.actor_user_id == actor
    assert row.reason == "Courier started route"


# ── apply_stop_status_transition ────────────────────────────────────────


def test_stop_status_transition_captures_coordinates():
    db = _StubSession()
    stop_id = uuid.uuid4()
    stop = SimpleNamespace(id=stop_id, status=StopStatus.PENDING)
    applied = _run(
        apply_stop_status_transition(
            db,
            stop,
            StopStatus.ARRIVED,
            reason="Geofence auto-arrival",
            lat=41.015,
            lon=28.979,
        )
    )
    assert applied is True
    assert stop.status == StopStatus.ARRIVED
    row = db.added[0]
    assert isinstance(row, StopStatusHistory)
    assert row.from_status == StopStatus.PENDING
    assert row.to_status == StopStatus.ARRIVED
    assert row.lat == pytest.approx(41.015)
    assert row.lon == pytest.approx(28.979)
    assert row.reason == "Geofence auto-arrival"


def test_stop_status_transition_noop_when_same():
    db = _StubSession()
    stop = SimpleNamespace(id=uuid.uuid4(), status=StopStatus.COMPLETED)
    applied = _run(
        apply_stop_status_transition(db, stop, StopStatus.COMPLETED)
    )
    assert applied is False
    assert db.added == []


# ── record_courier_availability_change ──────────────────────────────────


def test_courier_availability_change_records_history():
    db = _StubSession()
    courier_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    courier = SimpleNamespace(
        id=courier_id,
        tenant_id=tenant_id,
        availability_status=AvailabilityStatus.ONLINE,
    )
    applied = _run(
        record_courier_availability_change(
            db,
            courier,
            AvailabilityStatus.OFFLINE,
            reason="End of shift",
        )
    )
    assert applied is True
    assert courier.availability_status == AvailabilityStatus.OFFLINE
    # First added row = the history audit; second = the Layer 3 driver
    # event for the SHIFT_END transition.
    history_row = db.added[0]
    assert isinstance(history_row, CourierAvailabilityHistory)
    assert history_row.courier_id == courier_id
    assert history_row.from_status == AvailabilityStatus.ONLINE
    assert history_row.to_status == AvailabilityStatus.OFFLINE
    assert history_row.reason == "End of shift"
    # Driver event for ONLINE→OFFLINE = SHIFT_END.
    from app.models.driver_event import DriverEvent, DriverEventType

    assert len(db.added) == 2
    driver_event = db.added[1]
    assert isinstance(driver_event, DriverEvent)
    assert driver_event.event_type == DriverEventType.SHIFT_END
    assert driver_event.courier_id == courier_id
    assert driver_event.tenant_id == tenant_id


def test_courier_availability_change_noop_when_same():
    db = _StubSession()
    courier = SimpleNamespace(
        id=uuid.uuid4(), availability_status=AvailabilityStatus.ON_BREAK
    )
    applied = _run(
        record_courier_availability_change(
            db, courier, AvailabilityStatus.ON_BREAK
        )
    )
    assert applied is False
    assert db.added == []


# ── record_decisions_for_solution ───────────────────────────────────────


def test_dispatch_decision_log_writes_assigned_and_unassigned_rows():
    db = _StubSession()
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    order_a = uuid.uuid4()
    order_b = uuid.uuid4()
    order_unfit = uuid.uuid4()
    courier_a = uuid.uuid4()
    courier_b = uuid.uuid4()

    count = _run(
        record_decisions_for_solution(
            db,
            tenant_id=tenant_id,
            optimization_run_id=run_id,
            kind=DispatchDecisionKind.BATCH_DISPATCH,
            assignments=[
                (order_a, courier_a, 12000.0),
                (order_b, courier_b, 8500.0),
            ],
            unassigned=[(order_unfit, "Capacity full on every day")],
        )
    )
    assert count == 3
    assert all(isinstance(r, DispatchDecisionLog) for r in db.added)
    by_order = {r.order_id: r for r in db.added}
    assert by_order[order_a].chosen_courier_id == courier_a
    assert by_order[order_a].chosen_score == pytest.approx(12000.0)
    assert by_order[order_a].kind == DispatchDecisionKind.BATCH_DISPATCH
    assert by_order[order_a].decision_reason is None
    assert by_order[order_unfit].chosen_courier_id is None
    assert by_order[order_unfit].chosen_score is None
    assert by_order[order_unfit].decision_reason == "Capacity full on every day"
    assert all(r.tenant_id == tenant_id for r in db.added)
    assert all(r.optimization_run_id == run_id for r in db.added)


def test_dispatch_decision_log_no_rows_when_solution_empty():
    db = _StubSession()
    count = _run(
        record_decisions_for_solution(
            db,
            tenant_id=uuid.uuid4(),
            optimization_run_id=None,
            kind=DispatchDecisionKind.ON_DEMAND_INSERTION,
            assignments=[],
            unassigned=[],
        )
    )
    assert count == 0
    assert db.added == []


def test_dispatch_decision_log_carries_alternatives_when_provided():
    db = _StubSession()
    order = uuid.uuid4()
    courier = uuid.uuid4()
    alt_courier_1 = uuid.uuid4()
    alt_courier_2 = uuid.uuid4()
    alternatives = {
        order: [
            {"courier_id": str(alt_courier_1), "score": 13000.0, "reason": "capacity tight"},
            {"courier_id": str(alt_courier_2), "score": 14500.0, "reason": "wrong vehicle profile"},
        ]
    }
    _run(
        record_decisions_for_solution(
            db,
            tenant_id=uuid.uuid4(),
            optimization_run_id=uuid.uuid4(),
            kind=DispatchDecisionKind.BATCH_DISPATCH,
            assignments=[(order, courier, 12000.0)],
            alternatives_by_order=alternatives,
        )
    )
    row = db.added[0]
    assert isinstance(row.alternatives, list)
    assert len(row.alternatives) == 2
    assert row.alternatives[0]["reason"] == "capacity tight"


# ── Source-level invariants — protect future bypasses ───────────────────


def test_no_direct_route_status_assignment_in_route_execution_service():
    """All Route.status mutations in route_execution_service.py must go
    through `apply_route_status_transition` — otherwise the audit trail
    silently regresses."""
    from app.services import route_execution_service
    import inspect

    src = inspect.getsource(route_execution_service)
    # The helper itself uses `route.status = target` internally; check
    # the service's body for the bypass pattern.
    assert "route.status = RouteStatus." not in src, (
        "route_execution_service.py still mutates `route.status` directly. "
        "Use `apply_route_status_transition` so the audit trail captures it."
    )


def test_no_direct_stop_status_assignment_in_route_execution_service():
    """Same invariant for stop status mutations."""
    from app.services import route_execution_service
    import inspect

    src = inspect.getsource(route_execution_service)
    assert "stop.status = StopStatus." not in src, (
        "route_execution_service.py still mutates `stop.status` directly. "
        "Use `apply_stop_status_transition` so the audit trail captures it."
    )
