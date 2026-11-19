from __future__ import annotations

import uuid
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.courier import Courier
from app.models.enums import (
    OptimizationRunStatus,
    OptimizationRunType,
    RouteStatus,
    StopType,
    VehicleType,
)
from app.models.optimization_run import OptimizationRun
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.schemas.route import (
    EconomicCostBreakdownResponse,
    EconomicCostDeltaResponse,
    PreviewChangedOrder,
    PreviewETASummary,
    PreviewExplainabilityChangeType,
    PreviewExplainabilityItem,
    PreviewExplainabilityTone,
    PreviewOrderSummary,
    PreviewRouteChangeType,
    PreviewRouteExplainability,
    PreviewRouteResponse,
    PreviewStopResponse,
    PreviewSummary,
    PreviewViolation,
    RunPreviewExplainability,
    RunPreviewResponse,
)
from app.services import operations_policy_service
from app.services.vehicle_spec_service import (
    derive_default_vehicle_type_fuel_cost_per_km,
    derive_fuel_cost_per_km,
    get_default_vehicle_type_fuel_profile,
)
from app.utils.compat import UTC
from app.utils.tenant_context import get_current_tenant_id, tenant_filter


async def create_run(
    db: AsyncSession,
    *,
    tenant_id,
    run_type: OptimizationRunType,
    task_id: str | None,
) -> OptimizationRun:
    run = OptimizationRun(
        tenant_id=get_current_tenant_id(tenant_id),
        run_type=run_type,
        task_id=task_id,
        status=OptimizationRunStatus.STARTED,
        started_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def complete_run(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    status_value: OptimizationRunStatus,
    result: dict | None = None,
    error_message: str | None = None,
) -> OptimizationRun:
    result_q = await db.execute(select(OptimizationRun).where(OptimizationRun.id == run_id))
    run = result_q.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Optimization run not found")

    run.status = status_value
    run.finished_at = datetime.now(UTC).replace(tzinfo=None)
    run.metrics = result
    run.error_message = error_message
    if result:
        run.routes_created = result.get("routes_created")
        run.unassigned_count = result.get("unassigned_count")
        run.solver_status = result.get("solver_status")
    await db.flush()
    await db.refresh(run)
    return run


async def update_run_status(
    db: AsyncSession,
    run_id: uuid.UUID,
    new_status: OptimizationRunStatus,
    error_message: str | None = None,
) -> None:
    result_q = await db.execute(select(OptimizationRun).where(OptimizationRun.id == run_id))
    run = result_q.scalar_one_or_none()
    if run:
        run.status = new_status
        run.finished_at = datetime.now(UTC).replace(tzinfo=None)
        if error_message:
            run.error_message = error_message
        await db.flush()


async def list_runs(
    db: AsyncSession,
    principal,
    *,
    limit: int = 50,
) -> list[OptimizationRun]:
    result = await db.execute(
        select(OptimizationRun)
        .where(tenant_filter(OptimizationRun, principal))
        .order_by(OptimizationRun.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_run(db: AsyncSession, run_id: uuid.UUID, principal=None) -> OptimizationRun:
    query = select(OptimizationRun).where(OptimizationRun.id == run_id)
    if principal is not None:
        query = query.where(tenant_filter(OptimizationRun, principal))
    result = await db.execute(query)
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Optimization run not found")
    return run


# ---------------------------------------------------------------------------
# Run preview
# ---------------------------------------------------------------------------

CURRENT_PREVIEW_BASELINE_STATUSES = (RouteStatus.PLANNED, RouteStatus.ACTIVE)


@dataclass(frozen=True)
class _RouteSnapshot:
    route_id: uuid.UUID
    courier_id: uuid.UUID
    ordered_order_ids: tuple[uuid.UUID, ...]
    order_ids: frozenset[uuid.UUID]


def _parse_unassigned_order_ids(run: OptimizationRun) -> list[uuid.UUID]:
    unassigned_ids: list[uuid.UUID] = []
    if run.metrics and run.metrics.get("unassigned_order_ids"):
        for oid_str in run.metrics["unassigned_order_ids"]:
            with suppress(ValueError, AttributeError, TypeError):
                unassigned_ids.append(uuid.UUID(str(oid_str)))
    return unassigned_ids


def _ordered_order_ids(stops: list[RouteStop]) -> tuple[uuid.UUID, ...]:
    seen: set[uuid.UUID] = set()
    ordered_ids: list[uuid.UUID] = []
    for stop in sorted(stops, key=lambda item: item.sequence_number):
        if stop.order_id is None or stop.order_id in seen:
            continue
        seen.add(stop.order_id)
        ordered_ids.append(stop.order_id)
    return tuple(ordered_ids)


def _route_snapshot(route: Route) -> _RouteSnapshot:
    ordered_ids = _ordered_order_ids(route.stops)
    return _RouteSnapshot(
        route_id=route.id,
        courier_id=route.courier_id,
        ordered_order_ids=ordered_ids,
        order_ids=frozenset(ordered_ids),
    )


def _build_assignment_index(
    snapshots: list[_RouteSnapshot],
) -> dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]]:
    assignments: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {}
    for snapshot in snapshots:
        for order_id in snapshot.ordered_order_ids:
            assignments[order_id] = (snapshot.route_id, snapshot.courier_id)
    return assignments


def _match_current_routes(
    draft_snapshots: list[_RouteSnapshot],
    current_snapshots: list[_RouteSnapshot],
) -> tuple[dict[uuid.UUID, _RouteSnapshot], list[_RouteSnapshot]]:
    remaining = {snapshot.route_id: snapshot for snapshot in current_snapshots}
    matches: dict[uuid.UUID, _RouteSnapshot] = {}

    for draft_snapshot in draft_snapshots:
        candidates = [
            snapshot
            for snapshot in remaining.values()
            if draft_snapshot.order_ids & snapshot.order_ids
        ]
        if not candidates:
            continue

        matched = max(
            candidates,
            key=lambda snapshot: (
                len(draft_snapshot.order_ids & snapshot.order_ids),
                len(snapshot.order_ids),
            ),
        )
        matches[draft_snapshot.route_id] = matched
        remaining.pop(matched.route_id, None)

    return matches, list(remaining.values())


def _build_changed_order(
    *,
    order: Order,
    change_type: PreviewExplainabilityChangeType,
    current_assignment: tuple[uuid.UUID, uuid.UUID] | None,
    draft_assignment: tuple[uuid.UUID, uuid.UUID] | None,
) -> PreviewChangedOrder:
    return PreviewChangedOrder(
        order_id=order.id,
        delivery_address=order.delivery_address,
        priority=order.priority,
        change_type=change_type,
        from_route_id=current_assignment[0] if current_assignment else None,
        from_courier_id=current_assignment[1] if current_assignment else None,
        to_route_id=draft_assignment[0] if draft_assignment else None,
        to_courier_id=draft_assignment[1] if draft_assignment else None,
    )


def _build_run_summary_from_counts(
    *,
    assigned_count: int,
    moved_count: int,
    unassigned_count: int,
    reordered_count: int,
    created_routes: int,
    removed_routes: int,
) -> str:
    parts: list[str] = []
    if created_routes:
        parts.append(f"{created_routes} new route{'s' if created_routes != 1 else ''}")
    if assigned_count:
        parts.append(
            f"{assigned_count} newly assigned order{'s' if assigned_count != 1 else ''}"
        )
    if moved_count:
        parts.append(
            f"{moved_count} order{'s' if moved_count != 1 else ''} moved between couriers"
        )
    if reordered_count:
        parts.append(
            f"{reordered_count} order{'s' if reordered_count != 1 else ''} reordered on the same courier"
        )
    if unassigned_count:
        parts.append(
            f"{unassigned_count} order{'s' if unassigned_count != 1 else ''} left unassigned"
        )
    if removed_routes:
        parts.append(
            f"{removed_routes} current route{'s' if removed_routes != 1 else ''} would be replaced"
        )
    if not parts:
        return "No significant changes compared with the current committed plan."
    return ". ".join(parts).rstrip(".") + "."


def _build_route_summary(
    *,
    change_type: PreviewRouteChangeType,
    added_from_unassigned: int,
    moved_in: int,
    moved_out: int,
    unassigned_from_route: int,
    reordered: int,
) -> str:
    if change_type == PreviewRouteChangeType.NEW_ROUTE:
        details: list[str] = []
        if added_from_unassigned:
            details.append(
                f"{added_from_unassigned} previously unassigned order{'s' if added_from_unassigned != 1 else ''}"
            )
        if moved_in:
            details.append(
                f"{moved_in} moved from another courier"
            )
        if not details:
            return "New draft route created by this preview."
        return f"New draft route with {' and '.join(details)}."

    details = []
    if moved_in:
        details.append(f"{moved_in} moved in")
    if moved_out:
        details.append(f"{moved_out} moved out")
    if added_from_unassigned:
        details.append(f"{added_from_unassigned} newly assigned")
    if unassigned_from_route:
        details.append(f"{unassigned_from_route} now unassigned")
    if reordered:
        details.append(f"{reordered} reordered")
    if not details:
        return "No significant changes compared with the current committed route."
    return ", ".join(details).capitalize() + "."


def _known_run_reason_message(code: str) -> str | None:
    messages = {
        "guardrail_cooldown_active": "Reoptimization guardrails recently fired, so the system is trying to avoid churn.",
        "COOLDOWN_ACTIVE": "Reoptimization cooldown is active, so the system is limiting repeated route changes.",
        "LOCKED": "Another optimization run already owns this work, so the system avoided a conflicting change.",
    }
    return messages.get(code)


def _zero_cost_breakdown() -> EconomicCostBreakdownResponse:
    return EconomicCostBreakdownResponse()


def _serialize_cost_breakdown(raw: dict | None) -> EconomicCostBreakdownResponse:
    if not raw:
        return _zero_cost_breakdown()
    return EconomicCostBreakdownResponse.model_validate(raw)


def _deserialize_route_cost_breakdowns(raw: dict | None) -> dict[uuid.UUID, EconomicCostBreakdownResponse]:
    if not isinstance(raw, dict):
        return {}

    parsed: dict[uuid.UUID, EconomicCostBreakdownResponse] = {}
    for route_id, breakdown in raw.items():
        with suppress(ValueError, TypeError):
            parsed[uuid.UUID(str(route_id))] = _serialize_cost_breakdown(breakdown)
    return parsed


def _cost_delta(
    proposed: EconomicCostBreakdownResponse | None,
    baseline: EconomicCostBreakdownResponse | None,
) -> EconomicCostDeltaResponse:
    proposed = proposed or _zero_cost_breakdown()
    baseline = baseline or _zero_cost_breakdown()
    return EconomicCostDeltaResponse(
        total_generalized_cost=proposed.total_generalized_cost - baseline.total_generalized_cost,
        distance_cost=proposed.distance_cost - baseline.distance_cost,
        labor_cost=proposed.labor_cost - baseline.labor_cost,
        fuel_cost=proposed.fuel_cost - baseline.fuel_cost,
        fixed_route_cost=proposed.fixed_route_cost - baseline.fixed_route_cost,
        lateness_penalty=proposed.lateness_penalty - baseline.lateness_penalty,
        overtime_penalty=proposed.overtime_penalty - baseline.overtime_penalty,
        unserved_penalty=proposed.unserved_penalty - baseline.unserved_penalty,
        late_minutes=proposed.late_minutes - baseline.late_minutes,
        overtime_minutes=proposed.overtime_minutes - baseline.overtime_minutes,
        active_vehicle_count=proposed.active_vehicle_count - baseline.active_vehicle_count,
    )


def _sum_cost_breakdowns(
    breakdowns: list[EconomicCostBreakdownResponse | None],
) -> EconomicCostBreakdownResponse:
    normalized = [item for item in breakdowns if item is not None]
    if not normalized:
        return _zero_cost_breakdown()
    fuel_liters_values = [item.estimated_fuel_liters for item in normalized if item.estimated_fuel_liters is not None]
    co2_values = [item.estimated_co2_g for item in normalized if item.estimated_co2_g is not None]
    return EconomicCostBreakdownResponse(
        total_generalized_cost=sum(item.total_generalized_cost for item in normalized),
        distance_cost=sum(item.distance_cost for item in normalized),
        labor_cost=sum(item.labor_cost for item in normalized),
        fuel_cost=sum(item.fuel_cost for item in normalized),
        fixed_route_cost=sum(item.fixed_route_cost for item in normalized),
        lateness_penalty=sum(item.lateness_penalty for item in normalized),
        overtime_penalty=sum(item.overtime_penalty for item in normalized),
        unserved_penalty=sum(item.unserved_penalty for item in normalized),
        late_minutes=sum(item.late_minutes for item in normalized),
        overtime_minutes=sum(item.overtime_minutes for item in normalized),
        active_vehicle_count=sum(item.active_vehicle_count for item in normalized),
        estimated_fuel_liters=round(sum(fuel_liters_values), 3) if fuel_liters_values else None,
        estimated_co2_g=round(sum(co2_values), 1) if co2_values else None,
    )


def _add_unserved_penalty(
    *,
    breakdown: EconomicCostBreakdownResponse,
    unassigned_order_ids: list[uuid.UUID],
    orders_by_id: dict[uuid.UUID, Order],
    optimization_settings,
) -> EconomicCostBreakdownResponse:
    unserved_penalty = 0
    for order_id in unassigned_order_ids:
        order = orders_by_id.get(order_id)
        if order is None:
            continue
        multiplier = optimization_settings.priority_penalty_multipliers.get(order.priority, 1.0)
        unserved_penalty += int(round(optimization_settings.unserved_penalty * multiplier))

    return EconomicCostBreakdownResponse(
        total_generalized_cost=breakdown.total_generalized_cost + unserved_penalty,
        distance_cost=breakdown.distance_cost,
        labor_cost=breakdown.labor_cost,
        fuel_cost=breakdown.fuel_cost,
        fixed_route_cost=breakdown.fixed_route_cost,
        lateness_penalty=breakdown.lateness_penalty,
        overtime_penalty=breakdown.overtime_penalty,
        unserved_penalty=breakdown.unserved_penalty + unserved_penalty,
        late_minutes=breakdown.late_minutes,
        overtime_minutes=breakdown.overtime_minutes,
        active_vehicle_count=breakdown.active_vehicle_count,
        estimated_fuel_liters=breakdown.estimated_fuel_liters,
        estimated_co2_g=breakdown.estimated_co2_g,
    )


def _seconds_to_day(value: datetime) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def _seconds_to_billed_minutes(seconds: int) -> int:
    if seconds <= 0:
        return 0
    return (seconds + 59) // 60


def _calculate_route_cost_breakdown(
    *,
    route: Route,
    courier: Courier | None,
    orders_by_id: dict[uuid.UUID, Order],
    optimization_settings,
) -> EconomicCostBreakdownResponse:
    if courier is None:
        return _zero_cost_breakdown()

    vehicle_costs = optimization_settings.vehicle_class_costs.get(courier.vehicle_type)
    if vehicle_costs is None:
        return _zero_cost_breakdown()

    sorted_stops = sorted(route.stops, key=lambda item: item.sequence_number)
    actionable_stops = [
        stop
        for stop in sorted_stops
        if stop.stop_type not in (StopType.DEPOT_RETURN, StopType.DEPOT_RELOAD)
    ]
    if not actionable_stops:
        return _zero_cost_breakdown()

    route_distance_m = round(route.planned_distance_km * 1000)
    route_duration_s = 0
    if len(sorted_stops) >= 2:
        route_duration_s = max(
            0,
            int(
                (sorted_stops[-1].planned_arrival - actionable_stops[0].planned_arrival).total_seconds()
            ),
        )

    spec = getattr(courier, "vehicle_spec", None)
    if spec is not None:
        fuel_price = optimization_settings.fuel_prices.get(spec.fuel_type, 35.0)
        fuel_cost_per_km = derive_fuel_cost_per_km(
            spec.fuel_efficiency_km_per_liter,
            fuel_price,
        )
        fuel_efficiency_km_per_liter = spec.fuel_efficiency_km_per_liter
        co2_g_per_km = spec.co2_emissions_g_per_km
    else:
        default_fuel_cost = derive_default_vehicle_type_fuel_cost_per_km(
            courier.vehicle_type,
            optimization_settings.fuel_prices,
        )
        fuel_cost_per_km = (
            default_fuel_cost
            if default_fuel_cost is not None
            else vehicle_costs.fuel_cost_per_km
        )
        default_profile = get_default_vehicle_type_fuel_profile(courier.vehicle_type)
        fuel_efficiency_km_per_liter = (
            default_profile.fuel_efficiency_km_per_liter
            if default_profile is not None
            else None
        )
        co2_g_per_km = None
    fuel_cost_per_km = round(
        fuel_cost_per_km * optimization_settings.fuel_priority_multiplier
    )

    distance_cost = int(round((route_distance_m * vehicle_costs.distance_cost_per_km) / 1000.0))
    fuel_cost = int(round((route_distance_m * fuel_cost_per_km) / 1000.0))
    labor_cost = int(round((route_duration_s * vehicle_costs.labor_cost_per_minute) / 60.0))
    fixed_route_cost = vehicle_costs.fixed_route_cost

    late_minutes = 0
    lateness_penalty = 0
    for stop in actionable_stops:
        if stop.stop_type != StopType.DELIVERY or stop.order_id is None:
            continue
        order = orders_by_id.get(stop.order_id)
        if order is None:
            continue
        late_seconds = max(int((stop.planned_arrival - order.latest_delivery).total_seconds()), 0)
        late_minutes += _seconds_to_billed_minutes(late_seconds)
        lateness_penalty += int(
            round(
                (
                    late_seconds
                    * optimization_settings.lateness_penalty_per_minute
                    * optimization_settings.priority_penalty_multipliers.get(order.priority, 1.0)
                )
                / 60.0
            )
        )

    end_seconds = _seconds_to_day(sorted_stops[-1].planned_arrival)
    shift_end_seconds = (
        courier.working_hours_end.hour * 3600
        + courier.working_hours_end.minute * 60
        + courier.working_hours_end.second
    )
    overtime_seconds = max(end_seconds - shift_end_seconds, 0)
    overtime_minutes = _seconds_to_billed_minutes(overtime_seconds)
    overtime_penalty = int(
        round(
            (
                overtime_seconds
                * (optimization_settings.overtime_penalty_per_minute + vehicle_costs.overtime_cost_per_minute)
            )
            / 60.0
        )
    )

    total_generalized_cost = (
        distance_cost
        + fuel_cost
        + labor_cost
        + fixed_route_cost
        + lateness_penalty
        + overtime_penalty
    )
    route_distance_km = route_distance_m / 1000.0
    estimated_fuel_liters: float | None = None
    estimated_co2_g: float | None = None
    if fuel_efficiency_km_per_liter is not None and fuel_efficiency_km_per_liter > 0:
        estimated_fuel_liters = round(route_distance_km / fuel_efficiency_km_per_liter, 3)
    if co2_g_per_km is not None and co2_g_per_km > 0:
        estimated_co2_g = round(route_distance_km * co2_g_per_km, 1)

    return EconomicCostBreakdownResponse(
        total_generalized_cost=total_generalized_cost,
        distance_cost=distance_cost,
        labor_cost=labor_cost,
        fuel_cost=fuel_cost,
        fixed_route_cost=fixed_route_cost,
        lateness_penalty=lateness_penalty,
        overtime_penalty=overtime_penalty,
        late_minutes=late_minutes,
        overtime_minutes=overtime_minutes,
        active_vehicle_count=1,
        estimated_fuel_liters=estimated_fuel_liters,
        estimated_co2_g=estimated_co2_g,
    )

async def get_run_preview(db: AsyncSession, run_id: uuid.UUID, principal) -> RunPreviewResponse:
    """Build a rich preview of an optimization run's draft routes.

    Loads draft routes, related orders, and the current committed routes for
    the same affected orders, then computes per-route preview data together
    with an explainability diff against the current plan.
    """
    run = await get_run(db, run_id, principal)

    # Load draft routes with stops eagerly loaded
    route_result = await db.execute(
        select(Route)
        .where(
            Route.optimization_run_id == run_id,
            Route.status == RouteStatus.DRAFT,
            tenant_filter(Route, run.tenant_id),
        )
        .options(selectinload(Route.stops))
        .order_by(Route.created_at)
    )
    draft_routes = list(route_result.scalars().unique().all())

    unassigned_ids = _parse_unassigned_order_ids(run)

    # Collect all referenced order and courier IDs
    draft_order_ids: set[uuid.UUID] = set()
    courier_ids: set[uuid.UUID] = set()
    for route in draft_routes:
        courier_ids.add(route.courier_id)
        for stop in route.stops:
            if stop.order_id is not None:
                draft_order_ids.add(stop.order_id)

    relevant_order_ids = set(draft_order_ids) | set(unassigned_ids)

    current_routes: list[Route] = []
    if relevant_order_ids:
        current_route_result = await db.execute(
            select(Route)
            .join(RouteStop)
            .where(
                Route.status.in_(CURRENT_PREVIEW_BASELINE_STATUSES),
                tenant_filter(Route, run.tenant_id),
                RouteStop.order_id.in_(relevant_order_ids),
            )
            .options(selectinload(Route.stops))
            .order_by(Route.created_at)
        )
        current_routes = list(current_route_result.scalars().unique().all())
        for route in current_routes:
            courier_ids.add(route.courier_id)

    current_order_ids = {
        stop.order_id
        for route in current_routes
        for stop in route.stops
        if stop.order_id is not None
    }

    # Batch-load orders and couriers
    orders_by_id: dict[uuid.UUID, Order] = {}
    all_order_ids = relevant_order_ids | current_order_ids
    if all_order_ids:
        order_result = await db.execute(
            select(Order).where(
                Order.id.in_(all_order_ids),
                tenant_filter(Order, run.tenant_id),
            )
        )
        orders_by_id = {o.id: o for o in order_result.scalars().all()}

    couriers_by_id: dict[uuid.UUID, Courier] = {}
    if courier_ids:
        courier_result = await db.execute(
            select(Courier)
            .where(
                Courier.id.in_(courier_ids),
                tenant_filter(Courier, run.tenant_id),
            )
            .options(selectinload(Courier.vehicle_spec))
        )
        couriers_by_id = {c.id: c for c in courier_result.scalars().all()}
    operations_policy = await operations_policy_service.get_operations_policy(db, run.tenant_id)
    optimization_settings = operations_policy.optimization
    stored_draft_route_costs = _deserialize_route_cost_breakdowns(
        run.metrics.get("route_cost_breakdowns") if isinstance(run.metrics, dict) else None
    )
    current_route_costs = {
        route.id: _calculate_route_cost_breakdown(
            route=route,
            courier=couriers_by_id.get(route.courier_id),
            orders_by_id=orders_by_id,
            optimization_settings=optimization_settings,
        )
        for route in current_routes
    }
    draft_route_costs: dict[uuid.UUID, EconomicCostBreakdownResponse] = {}

    # Build per-route preview
    all_violations: list[PreviewViolation] = []
    preview_routes: list[PreviewRouteResponse] = []
    route_violations_by_route_id: dict[uuid.UUID, list[PreviewViolation]] = {}
    total_on_time = 0
    total_deliveries = 0

    for route in draft_routes:
        courier = couriers_by_id.get(route.courier_id)
        sorted_stops = sorted(route.stops, key=lambda s: s.sequence_number)

        stops_out: list[PreviewStopResponse] = []
        route_violations: list[PreviewViolation] = []
        route_order_ids: set[uuid.UUID] = set()
        route_weight_kg = 0.0
        running_load_g = 0
        peak_load_g = 0

        for stop in sorted_stops:
            order = orders_by_id.get(stop.order_id) if stop.order_id else None
            order_summary = None

            if order:
                route_order_ids.add(order.id)
                on_time = (
                    stop.planned_arrival <= order.latest_delivery
                    if stop.stop_type == StopType.DELIVERY
                    else True
                )
                order_summary = PreviewOrderSummary(
                    order_id=order.id,
                    pickup_address=order.pickup_address,
                    delivery_address=order.delivery_address,
                    weight_kg=order.weight_kg,
                    is_fragile=order.is_fragile,
                    is_temp_sensitive=order.is_temp_sensitive,
                    priority=order.priority,
                    earliest_delivery=order.earliest_delivery,
                    latest_delivery=order.latest_delivery,
                    on_time=on_time,
                )

                if stop.stop_type == StopType.DELIVERY:
                    total_deliveries += 1
                    if on_time:
                        total_on_time += 1
                    else:
                        delay_s = int(
                            (stop.planned_arrival - order.latest_delivery).total_seconds()
                        )
                        severity = (
                            "error"
                            if delay_s > (optimization_settings.max_lateness_minutes * 60)
                            else "warning"
                        )
                        route_violations.append(PreviewViolation(
                            type="TIME_WINDOW_VIOLATED",
                            severity=severity,
                            message=(
                                f"Delivery arrives {delay_s}s after "
                                f"window closes at {order.latest_delivery:%H:%M}"
                            ),
                            order_id=order.id,
                        ))

                weight_g = round(order.weight_kg * 1000)
                if stop.stop_type == StopType.PICKUP:
                    running_load_g += weight_g
                    route_weight_kg += order.weight_kg
                elif stop.stop_type == StopType.DELIVERY:
                    running_load_g -= weight_g

            if stop.stop_type in (StopType.DEPOT_RETURN, StopType.DEPOT_RELOAD):
                running_load_g = 0

            peak_load_g = max(peak_load_g, running_load_g)

            stops_out.append(PreviewStopResponse(
                stop_id=stop.id,
                sequence_number=stop.sequence_number,
                stop_type=stop.stop_type,
                lat=stop.lat,
                lon=stop.lon,
                address=stop.address,
                planned_arrival=stop.planned_arrival,
                # ``planned_departure`` is a new column (migration
                # 20260512_0036). ``getattr`` keeps unit tests that mock
                # RouteStop with ``SimpleNamespace`` running and lets us
                # tolerate pre-migration rows in mixed-version deploys.
                planned_departure=getattr(stop, "planned_departure", None),
                order=order_summary,
            ))

        # Capacity violation
        if courier and courier.max_weight_kg and peak_load_g > round(courier.max_weight_kg * 1000):
            over_g = peak_load_g - round(courier.max_weight_kg * 1000)
            route_violations.append(PreviewViolation(
                type="CAPACITY_EXCEEDED",
                severity="error",
                message=(
                    f"Peak load {peak_load_g}g exceeds "
                    f"capacity {round(courier.max_weight_kg * 1000)}g "
                    f"(over by {over_g}g)"
                ),
            ))

        # Shift violation
        if courier and sorted_stops:
            wh = courier.working_hours_start
            shift_start_s = wh.hour * 3600 + wh.minute * 60 + wh.second
            wh = courier.working_hours_end
            shift_end_s = wh.hour * 3600 + wh.minute * 60 + wh.second
            first_arrival_s = (
                sorted_stops[0].planned_arrival.hour * 3600
                + sorted_stops[0].planned_arrival.minute * 60
                + sorted_stops[0].planned_arrival.second
            )
            last_arrival_s = (
                sorted_stops[-1].planned_arrival.hour * 3600
                + sorted_stops[-1].planned_arrival.minute * 60
                + sorted_stops[-1].planned_arrival.second
            )
            if last_arrival_s > shift_end_s:
                overtime_s = last_arrival_s - shift_end_s
                route_violations.append(PreviewViolation(
                    type="SHIFT_EXCEEDED",
                    severity=(
                        "error"
                        if overtime_s > (optimization_settings.max_overtime_minutes * 60)
                        else "warning"
                    ),
                    message=(
                        f"Route ends at {sorted_stops[-1].planned_arrival:%H:%M}, "
                        f"after shift end {courier.working_hours_end}"
                    ),
                ))
            if first_arrival_s < shift_start_s:
                route_violations.append(PreviewViolation(
                    type="SHIFT_EXCEEDED",
                    severity="warning",
                    message=(
                        f"Route starts at {sorted_stops[0].planned_arrival:%H:%M}, "
                        f"before shift start {courier.working_hours_start}"
                    ),
                ))

        # ETA summary
        arrivals = [s.planned_arrival for s in sorted_stops]
        eta = PreviewETASummary()
        if arrivals:
            eta = PreviewETASummary(
                earliest_departure=arrivals[0],
                latest_arrival=arrivals[-1],
                total_duration_minutes=round(
                    (arrivals[-1] - arrivals[0]).total_seconds() / 60, 1
                ),
            )

        non_depot_count = sum(
            1 for s in sorted_stops
            if s.stop_type not in (StopType.DEPOT_RETURN, StopType.DEPOT_RELOAD)
        )
        route_cost_breakdown = stored_draft_route_costs.get(route.id) or _calculate_route_cost_breakdown(
            route=route,
            courier=courier,
            orders_by_id=orders_by_id,
            optimization_settings=optimization_settings,
        )
        draft_route_costs[route.id] = route_cost_breakdown

        all_violations.extend(route_violations)
        route_violations_by_route_id[route.id] = route_violations
        preview_routes.append(PreviewRouteResponse(
            route_id=route.id,
            courier_id=route.courier_id,
            vehicle_type=courier.vehicle_type if courier else VehicleType.CAR_VAN,
            planned_distance_km=route.planned_distance_km,
            stop_count=non_depot_count,
            order_count=len(route_order_ids),
            total_weight_kg=round(route_weight_kg, 2),
            eta=eta,
            cost_breakdown=route_cost_breakdown,
            violations=route_violations,
            stops=stops_out,
        ))

    draft_snapshots = [_route_snapshot(route) for route in draft_routes]
    current_snapshots = [_route_snapshot(route) for route in current_routes]
    matched_current_routes, removed_current_routes = _match_current_routes(
        draft_snapshots,
        current_snapshots,
    )
    current_assignments = _build_assignment_index(current_snapshots)
    draft_assignments = _build_assignment_index(draft_snapshots)

    changed_orders: list[PreviewChangedOrder] = []
    newly_assigned_count = 0
    moved_between_couriers_count = 0
    now_unassigned_count = 0
    reordered_count = 0
    current_snapshots_by_route_id = {
        snapshot.route_id: snapshot for snapshot in current_snapshots
    }
    draft_snapshots_by_route_id = {
        snapshot.route_id: snapshot for snapshot in draft_snapshots
    }

    for order_id in sorted(relevant_order_ids):
        order = orders_by_id.get(order_id)
        if order is None:
            continue
        current_assignment = current_assignments.get(order_id)
        draft_assignment = draft_assignments.get(order_id)

        if current_assignment is None and draft_assignment is not None:
            newly_assigned_count += 1
            changed_orders.append(
                _build_changed_order(
                    order=order,
                    change_type=PreviewExplainabilityChangeType.ASSIGNED,
                    current_assignment=None,
                    draft_assignment=draft_assignment,
                )
            )
            continue

        if current_assignment is not None and draft_assignment is None:
            now_unassigned_count += 1
            changed_orders.append(
                _build_changed_order(
                    order=order,
                    change_type=PreviewExplainabilityChangeType.UNASSIGNED,
                    current_assignment=current_assignment,
                    draft_assignment=None,
                )
            )
            continue

        if current_assignment is None or draft_assignment is None:
            continue

        if current_assignment[1] != draft_assignment[1]:
            moved_between_couriers_count += 1
            changed_orders.append(
                _build_changed_order(
                    order=order,
                    change_type=PreviewExplainabilityChangeType.MOVED,
                    current_assignment=current_assignment,
                    draft_assignment=draft_assignment,
                )
            )
            continue

        current_snapshot = current_snapshots_by_route_id[current_assignment[0]]
        draft_snapshot = draft_snapshots_by_route_id[draft_assignment[0]]
        current_index = current_snapshot.ordered_order_ids.index(order_id)
        draft_index = draft_snapshot.ordered_order_ids.index(order_id)
        if current_index != draft_index:
            reordered_count += 1
            changed_orders.append(
                _build_changed_order(
                    order=order,
                    change_type=PreviewExplainabilityChangeType.REORDERED,
                    current_assignment=current_assignment,
                    draft_assignment=draft_assignment,
                )
            )

    changed_orders_by_route_id: dict[uuid.UUID, list[PreviewChangedOrder]] = defaultdict(list)
    for changed_order in changed_orders:
        if changed_order.to_route_id:
            changed_orders_by_route_id[changed_order.to_route_id].append(changed_order)
        if (
            changed_order.from_route_id
            and changed_order.from_route_id != changed_order.to_route_id
            and changed_order.change_type in {
                PreviewExplainabilityChangeType.MOVED,
                PreviewExplainabilityChangeType.UNASSIGNED,
            }
        ):
            changed_orders_by_route_id[changed_order.from_route_id].append(changed_order)

    preview_routes_by_id = {preview_route.route_id: preview_route for preview_route in preview_routes}
    new_route_count = 0

    for draft_snapshot in draft_snapshots:
        preview_route = preview_routes_by_id[draft_snapshot.route_id]
        matched_current = matched_current_routes.get(draft_snapshot.route_id)
        route_changed_orders = changed_orders_by_route_id.get(draft_snapshot.route_id, [])

        added_from_unassigned = sum(
            1
            for item in route_changed_orders
            if item.change_type == PreviewExplainabilityChangeType.ASSIGNED
            and item.to_route_id == draft_snapshot.route_id
        )
        moved_in = sum(
            1
            for item in route_changed_orders
            if item.change_type == PreviewExplainabilityChangeType.MOVED
            and item.to_route_id == draft_snapshot.route_id
        )
        moved_out = sum(
            1
            for item in route_changed_orders
            if item.change_type == PreviewExplainabilityChangeType.MOVED
            and item.from_route_id == draft_snapshot.route_id
        )
        unassigned_from_route = sum(
            1
            for item in route_changed_orders
            if item.change_type == PreviewExplainabilityChangeType.UNASSIGNED
            and item.from_route_id == draft_snapshot.route_id
        )
        route_reordered = sum(
            1
            for item in route_changed_orders
            if item.change_type == PreviewExplainabilityChangeType.REORDERED
            and item.to_route_id == draft_snapshot.route_id
        )

        route_reasons: list[PreviewExplainabilityItem] = []
        pinned_count = sum(
            1
            for order_id in draft_snapshot.order_ids
            if (
                (order := orders_by_id.get(order_id)) is not None
                and order.preferred_courier_id == draft_snapshot.courier_id
            )
        )
        if pinned_count:
            route_reasons.append(
                PreviewExplainabilityItem(
                    code="PREFERRED_COURIER",
                    message=(
                        f"{pinned_count} order{'s' if pinned_count != 1 else ''} "
                        "are pinned to this courier."
                    ),
                )
            )
        if moved_in:
            route_reasons.append(
                PreviewExplainabilityItem(
                    code="REBALANCED_WORK",
                    message=(
                        f"{moved_in} order{'s' if moved_in != 1 else ''} "
                        "shifted onto this courier from another route."
                    ),
                )
            )
        if added_from_unassigned:
            route_reasons.append(
                PreviewExplainabilityItem(
                    code="ASSIGNED_PENDING_WORK",
                    message=(
                        f"{added_from_unassigned} pending order{'s' if added_from_unassigned != 1 else ''} "
                        "would be assigned on this route."
                    ),
                )
            )

        attention_items = [
            PreviewExplainabilityItem(
                code=violation.type,
                tone=PreviewExplainabilityTone.WARNING if violation.severity != "info" else PreviewExplainabilityTone.INFO,
                message=violation.message,
            )
            for violation in route_violations_by_route_id.get(draft_snapshot.route_id, [])
        ]

        change_type = (
            PreviewRouteChangeType.NEW_ROUTE
            if matched_current is None
            else PreviewRouteChangeType.MODIFIED_ROUTE
            if route_changed_orders
            else PreviewRouteChangeType.UNCHANGED
        )
        if change_type == PreviewRouteChangeType.NEW_ROUTE:
            new_route_count += 1

        preview_route.cost_delta_vs_current = _cost_delta(
            draft_route_costs.get(draft_snapshot.route_id),
            current_route_costs.get(matched_current.route_id) if matched_current else None,
        )

        preview_route.explainability = PreviewRouteExplainability(
            change_type=change_type,
            summary=_build_route_summary(
                change_type=change_type,
                added_from_unassigned=added_from_unassigned,
                moved_in=moved_in,
                moved_out=moved_out,
                unassigned_from_route=unassigned_from_route,
                reordered=route_reordered,
            ),
            previous_route_id=matched_current.route_id if matched_current else None,
            previous_courier_id=matched_current.courier_id if matched_current else None,
            changed_orders=route_changed_orders,
            reasons=route_reasons,
            attention_items=attention_items,
        )

    # Violation breakdown
    breakdown: dict[str, int] = defaultdict(int)
    for v in all_violations:
        breakdown[v.type] += 1

    total_orders = len(draft_order_ids)
    on_time_rate = round(total_on_time / total_deliveries, 4) if total_deliveries else None
    raw_run_cost_breakdown = run.metrics.get("cost_breakdown") if isinstance(run.metrics, dict) else None
    draft_cost_breakdown = (
        _serialize_cost_breakdown(raw_run_cost_breakdown)
        if raw_run_cost_breakdown
        else None
    )
    if draft_cost_breakdown is None:
        computed_draft = _sum_cost_breakdowns(list(draft_route_costs.values()))
        draft_unserved_penalty = sum(
            int(
                round(
                    optimization_settings.unserved_penalty
                    * optimization_settings.priority_penalty_multipliers.get(
                        orders_by_id[order_id].priority,
                        1.0,
                    )
                )
            )
            for order_id in unassigned_ids
            if order_id in orders_by_id
        )
        draft_cost_breakdown = EconomicCostBreakdownResponse(
            total_generalized_cost=computed_draft.total_generalized_cost + draft_unserved_penalty,
            distance_cost=computed_draft.distance_cost,
            labor_cost=computed_draft.labor_cost,
            fuel_cost=computed_draft.fuel_cost,
            fixed_route_cost=computed_draft.fixed_route_cost,
            lateness_penalty=computed_draft.lateness_penalty,
            overtime_penalty=computed_draft.overtime_penalty,
            unserved_penalty=draft_unserved_penalty,
            late_minutes=computed_draft.late_minutes,
            overtime_minutes=computed_draft.overtime_minutes,
            active_vehicle_count=computed_draft.active_vehicle_count,
        )
    assert draft_cost_breakdown is not None

    current_base_cost_breakdown = _sum_cost_breakdowns(list(current_route_costs.values()))
    current_unserved_penalty = sum(
        int(
            round(
                optimization_settings.unserved_penalty
                * optimization_settings.priority_penalty_multipliers.get(
                    orders_by_id[order_id].priority,
                    1.0,
                )
            )
        )
        for order_id in relevant_order_ids
        if order_id in orders_by_id and order_id not in current_assignments
    )
    current_cost_breakdown = EconomicCostBreakdownResponse(
        total_generalized_cost=current_base_cost_breakdown.total_generalized_cost + current_unserved_penalty,
        distance_cost=current_base_cost_breakdown.distance_cost,
        labor_cost=current_base_cost_breakdown.labor_cost,
        fuel_cost=current_base_cost_breakdown.fuel_cost,
        fixed_route_cost=current_base_cost_breakdown.fixed_route_cost,
        lateness_penalty=current_base_cost_breakdown.lateness_penalty,
        overtime_penalty=current_base_cost_breakdown.overtime_penalty,
        unserved_penalty=current_unserved_penalty,
        late_minutes=current_base_cost_breakdown.late_minutes,
        overtime_minutes=current_base_cost_breakdown.overtime_minutes,
        active_vehicle_count=current_base_cost_breakdown.active_vehicle_count,
    )

    summary = PreviewSummary(
        total_routes=len(preview_routes),
        total_orders=total_orders,
        total_unassigned=len(unassigned_ids),
        total_distance_km=round(
            sum(r.planned_distance_km for r in preview_routes), 2
        ),
        total_violations=len(all_violations),
        violation_breakdown=dict(breakdown),
        on_time_rate=on_time_rate,
        cost_breakdown=draft_cost_breakdown,
        cost_delta_vs_current=_cost_delta(draft_cost_breakdown, current_cost_breakdown),
    )

    run_reasons: list[PreviewExplainabilityItem] = []
    if run.run_type == OptimizationRunType.REOPTIMIZATION:
        run_reasons.append(
            PreviewExplainabilityItem(
                code="REOPTIMIZATION",
                message="This preview came from a reoptimization run against the current plan.",
            )
        )
    if moved_between_couriers_count:
        run_reasons.append(
            PreviewExplainabilityItem(
                code="WORK_REBALANCED",
                message=(
                    f"{moved_between_couriers_count} order{'s' if moved_between_couriers_count != 1 else ''} "
                    "would move between couriers."
                ),
            )
        )
    if any(
        order.preferred_courier_id is not None
        and draft_assignments.get(order.id, (None, None))[1] == order.preferred_courier_id
        for order in orders_by_id.values()
    ):
        run_reasons.append(
            PreviewExplainabilityItem(
                code="PREFERRED_COURIER",
                message="Preferred courier pins were honored where the draft plan could fit them.",
            )
        )
    if run.metrics and isinstance(run.metrics, dict):
        raw_reason = run.metrics.get("reason")
        if isinstance(raw_reason, str):
            friendly_message = _known_run_reason_message(raw_reason)
            if friendly_message:
                run_reasons.append(
                    PreviewExplainabilityItem(code=raw_reason, message=friendly_message)
                )

    attention_items: list[PreviewExplainabilityItem] = []
    if unassigned_ids:
        attention_items.append(
            PreviewExplainabilityItem(
                code="UNASSIGNED_ORDERS",
                tone=PreviewExplainabilityTone.WARNING,
                message=(
                    f"{len(unassigned_ids)} order{'s' if len(unassigned_ids) != 1 else ''} "
                    "remain unassigned in this preview."
                ),
            )
        )
    if all_violations:
        attention_items.append(
            PreviewExplainabilityItem(
                code="ROUTE_VIOLATIONS",
                tone=PreviewExplainabilityTone.WARNING,
                message=(
                    f"{len(all_violations)} route issue{'s' if len(all_violations) != 1 else ''} "
                    "need review before approval."
                ),
            )
        )

    explainability = RunPreviewExplainability(
        no_significant_changes=not changed_orders and not removed_current_routes,
        summary=_build_run_summary_from_counts(
            assigned_count=newly_assigned_count,
            moved_count=moved_between_couriers_count,
            unassigned_count=now_unassigned_count,
            reordered_count=reordered_count,
            created_routes=new_route_count,
            removed_routes=len(removed_current_routes),
        ),
        orders_newly_assigned=newly_assigned_count,
        orders_moved_between_couriers=moved_between_couriers_count,
        orders_now_unassigned=now_unassigned_count,
        orders_reordered=reordered_count,
        routes_created=new_route_count,
        routes_removed=len(removed_current_routes),
        changed_orders=changed_orders,
        reasons=run_reasons,
        attention_items=attention_items,
    )

    return RunPreviewResponse(
        optimization_run_id=run.id,
        run_status=run.status,
        solver_status=run.solver_status,
        routes=preview_routes,
        unassigned_order_ids=unassigned_ids,
        summary=summary,
        explainability=explainability,
    )
