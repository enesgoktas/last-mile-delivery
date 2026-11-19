"""Helpers that write append-only history rows alongside state-machine
mutations on `Route`, `RouteStop`, and `Courier.availability_status`.

The pattern mirrors `apply_order_status_transition` in `order_service.py`:
each helper takes the model + target status, validates that the change
is real (a no-op transition is silently skipped), writes the history
row, and then mutates the model. Callers flush at their own convenience.

These helpers are the only intended write path for route/stop/courier
status changes after Layer 0 lands. Old code that mutates `.status`
directly is being progressively migrated; greppable invariant tests in
`tests/unit/test_lifecycle_history_guards.py` (added at the same time)
will fail CI if a new bypass slips in.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courier import Courier
from app.models.courier_history import CourierAvailabilityHistory
from app.models.driver_event import DriverEvent, DriverEventType
from app.models.enums import AvailabilityStatus, RouteStatus, StopStatus
from app.models.route import Route, RouteStop
from app.models.route_history import RouteStatusHistory
from app.models.stop_history import StopStatusHistory
from app.utils.compat import utcnow_naive


async def apply_route_status_transition(
    db: AsyncSession,
    route: Route,
    target: RouteStatus,
    *,
    actor_user_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> bool:
    """Mutate `route.status` and append a history row.

    Returns True if the transition was applied, False if it was a no-op
    (target equals current status). The caller is responsible for
    flushing the session.
    """
    if route.status == target:
        return False
    previous = route.status
    route.status = target
    db.add(
        RouteStatusHistory(
            route_id=route.id,
            from_status=previous,
            to_status=target,
            actor_user_id=actor_user_id,
            reason=reason,
        )
    )
    return True


async def apply_stop_status_transition(
    db: AsyncSession,
    stop: RouteStop,
    target: StopStatus,
    *,
    actor_user_id: uuid.UUID | None = None,
    reason: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> bool:
    """Mutate `stop.status` and append a history row.

    `lat`/`lon` are the courier's coordinates at transition time, captured
    for forensic replay (e.g. "where was the truck when this stop was
    marked arrived?"). NULL when not known.

    Side-effects:
      * When the stop transitions to ARRIVED / COMPLETED with a fresh
        `actual_arrival`, reconcile any pending `ETAPredictionLog` rows
        so the ETA-accuracy dataset stays current. Non-fatal: a
        reconciliation failure must never block the real transition.
    """
    if stop.status == target:
        return False
    previous = stop.status
    stop.status = target
    db.add(
        StopStatusHistory(
            stop_id=stop.id,
            from_status=previous,
            to_status=target,
            actor_user_id=actor_user_id,
            reason=reason,
            lat=lat,
            lon=lon,
        )
    )
    # Layer 2b — ETA accuracy log reconciliation.
    if target in (StopStatus.ARRIVED, StopStatus.COMPLETED):
        actual_arrival = getattr(stop, "actual_arrival", None)
        if actual_arrival is not None:
            try:
                from app.services.eta_prediction_log_service import (
                    reconcile_arrival,
                )

                await reconcile_arrival(
                    db, stop_id=stop.id, actual_arrival=actual_arrival
                )
            except Exception:
                # Logged at the caller site; we don't import the logger
                # here just for this best-effort branch.
                pass
    return True


async def record_courier_availability_change(
    db: AsyncSession,
    courier: Courier,
    target: AvailabilityStatus,
    *,
    actor_user_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> bool:
    """Mutate `courier.availability_status` and append a history row.

    Distinct from the route/stop helpers because the courier model carries
    side effects (re-optimization enqueue on ONLINE→OFFLINE) that the
    caller — `courier_service.update_status` — still owns.  This helper
    only handles the audit-trail piece, plus a Layer 3 driver-event
    row for shift / break transitions so HoS compliance + fairness
    queries don't have to parse the raw availability_history.
    """
    if courier.availability_status == target:
        return False
    previous = courier.availability_status
    courier.availability_status = target
    db.add(
        CourierAvailabilityHistory(
            courier_id=courier.id,
            from_status=previous,
            to_status=target,
            actor_user_id=actor_user_id,
            reason=reason,
        )
    )
    # Layer 3 — emit a typed DriverEvent for the semantic transition.
    # OFFLINE→ONLINE = shift start, ONLINE→OFFLINE = shift end,
    # ONLINE→ON_BREAK = break start, ON_BREAK→ONLINE = break end.
    event_type = _driver_event_type_for_transition(previous, target)
    if event_type is not None:
        db.add(
            DriverEvent(
                tenant_id=courier.tenant_id,
                courier_id=courier.id,
                event_type=event_type,
                occurred_at=utcnow_naive(),
                metadata_payload={"reason": reason} if reason else None,
            )
        )
    return True


def _driver_event_type_for_transition(
    previous: AvailabilityStatus,
    target: AvailabilityStatus,
) -> DriverEventType | None:
    """Map a (prev, next) availability transition to the corresponding
    DriverEvent. Returns None for transitions that don't carry semantic
    meaning beyond raw availability (e.g. OFFLINE→ON_BREAK)."""
    if previous == AvailabilityStatus.OFFLINE and target == AvailabilityStatus.ONLINE:
        return DriverEventType.SHIFT_START
    if previous == AvailabilityStatus.ONLINE and target == AvailabilityStatus.OFFLINE:
        return DriverEventType.SHIFT_END
    if previous == AvailabilityStatus.ONLINE and target == AvailabilityStatus.ON_BREAK:
        return DriverEventType.BREAK_START
    if previous == AvailabilityStatus.ON_BREAK and target == AvailabilityStatus.ONLINE:
        return DriverEventType.BREAK_END
    return None
