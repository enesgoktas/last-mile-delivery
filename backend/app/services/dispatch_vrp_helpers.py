"""Shared VRP data-shaping helpers for dispatch flows."""
from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from datetime import time as dt_time

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.algorithms.solution_validator import RouteConstraints, validate_solution
from app.algorithms.vrp_solver import (
    EconomicCostBreakdown,
    OrderData,
    PlannedRoute,
    PlannedStop,
    VehicleData,
    VRPSolution,
)
from app.models.courier import Courier
from app.models.enums import OrderPriority, OrderType, StopStatus, StopType
from app.models.order import Order
from app.models.package import Package
from app.models.route import RouteStop
from app.schemas.operations_policy import OperationsPolicy, OptimizationSettings
from app.services.vehicle_routing_profile import resolve_osrm_profile_from_values
from app.services.vehicle_spec_service import (
    derive_default_vehicle_type_fuel_cost_per_km,
    derive_fuel_cost_per_km,
    get_default_vehicle_type_fuel_profile,
)
from app.utils.compat import utcnow_naive
from app.utils.tenant_context import tenant_filter

logger = logging.getLogger(__name__)

TERMINAL_STOP_STATUSES = (StopStatus.COMPLETED, StopStatus.FAILED)


def should_skip_multi_trip(
    orders: Sequence[Order],
    global_constraints: dict,
    operations_policy=None,
) -> bool:
    """Return whether dispatch should bypass virtual multi-trip expansion.

    Three independent gates collapse multi-trip:
      * Tenant policy explicitly disabled it
        (``feature_flags.multi_trip_enabled = False``).
      * Constraint catalogue says ``max_trips_per_day <= 1``.
      * The orders list is RIDE-only — passenger transport doesn't
        match the "courier returns to depot for more orders" semantic,
        so virtual-trip expansion would just produce reload stops with
        no operational meaning.

    Order mix beyond the RIDE-only case must NOT disable multi-trip
    globally: OR-Tools keeps each pickup/delivery pair on one virtual
    trip; an order that cannot fit any trip slice is correctly
    diagnosed as infeasible rather than capping the whole fleet to one
    trip.
    """
    if operations_policy is not None and not operations_policy.feature_flags.multi_trip_enabled:
        return True
    if int(global_constraints.get("max_trips_per_day", 3) or 0) <= 1:
        return True
    # RIDE-only gate: skip if every order is RIDE.
    if orders and all(getattr(o, "type", None) == OrderType.RIDE for o in orders):
        return True
    return False


def _combine_cost_breakdowns(
    breakdowns: list[EconomicCostBreakdown | None],
    *,
    fixed_route_cost: int | None = None,
    unserved_penalty: int | None = None,
    active_vehicle_count: int | None = None,
) -> EconomicCostBreakdown | None:
    realized = [item for item in breakdowns if item is not None]
    if not realized and fixed_route_cost is None and unserved_penalty is None and active_vehicle_count is None:
        return None
    distance_cost = sum(item.distance_cost for item in realized)
    labor_cost = sum(item.labor_cost for item in realized)
    fuel_cost = sum(item.fuel_cost for item in realized)
    fixed_cost = (
        fixed_route_cost
        if fixed_route_cost is not None
        else sum(item.fixed_route_cost for item in realized)
    )
    lateness_penalty = sum(item.lateness_penalty for item in realized)
    overtime_penalty = sum(item.overtime_penalty for item in realized)
    dropped_penalty = (
        unserved_penalty
        if unserved_penalty is not None
        else sum(item.unserved_penalty for item in realized)
    )
    route_count = (
        active_vehicle_count
        if active_vehicle_count is not None
        else sum(item.active_vehicle_count for item in realized)
    )
    return EconomicCostBreakdown(
        total_generalized_cost=(
            distance_cost
            + labor_cost
            + fuel_cost
            + fixed_cost
            + lateness_penalty
            + overtime_penalty
            + dropped_penalty
        ),
        distance_cost=distance_cost,
        labor_cost=labor_cost,
        fuel_cost=fuel_cost,
        fixed_route_cost=fixed_cost,
        lateness_penalty=lateness_penalty,
        overtime_penalty=overtime_penalty,
        unserved_penalty=dropped_penalty,
        late_minutes=sum(item.late_minutes for item in realized),
        overtime_minutes=sum(item.overtime_minutes for item in realized),
        active_vehicle_count=route_count,
    )


def time_to_seconds(value: dt_time) -> int:
    """Convert a datetime.time to seconds from midnight."""
    return value.hour * 3600 + value.minute * 60 + value.second


def datetime_to_day_seconds(value: datetime) -> int:
    """Convert a datetime to seconds from midnight on the same day."""
    return value.hour * 3600 + value.minute * 60 + value.second


def resolve_pickup_window_seconds(order: Order) -> tuple[int, int]:
    """Resolve pickup windows with a permissive fallback for missing pickup times."""
    if order.earliest_pickup and order.latest_pickup:
        return (
            datetime_to_day_seconds(order.earliest_pickup),
            datetime_to_day_seconds(order.latest_pickup),
        )
    return (0, datetime_to_day_seconds(order.latest_delivery))


def remap_order(order: OrderData, index_map: dict[int, int]) -> OrderData:
    """Return a copy of *order* with pickup and delivery indices remapped."""
    return replace(
        order,
        pickup_index=index_map[order.pickup_index],
        delivery_index=index_map[order.delivery_index],
    )


def remap_vehicle(vehicle: VehicleData, index_map: dict[int, int]) -> VehicleData:
    """Return a copy of *vehicle* with every depot location-index remapped.

    Multi-depot (Phase 2): `allowed_depot_indices` and `end_depot_index`
    are also remapped through `index_map`. When the source vehicle was
    single-depot (empty tuple / None), the result is bit-identical to
    pre-Phase-2 behavior.
    """
    remapped_allowed = (
        tuple(index_map[d] for d in vehicle.allowed_depot_indices)
        if vehicle.allowed_depot_indices
        else ()
    )
    remapped_end = (
        index_map[vehicle.end_depot_index]
        if vehicle.end_depot_index is not None
        else None
    )
    return replace(
        vehicle,
        depot_index=index_map[vehicle.depot_index],
        allowed_depot_indices=remapped_allowed,
        end_depot_index=remapped_end,
    )


async def gather_candidate_chargers_for_couriers(
    db: AsyncSession,
    couriers: list[Courier],
    operations_policy,
    *,
    principal=None,
) -> dict[str, list]:
    """Per-courier shortlist of compatible chargers for Phase 2.3.

    Returns `dict[courier_id, list[ChargingStation]]` (top 3 nearest per
    courier within the configured radius). Empty when the tenant has
    auto-charging disabled or none of the couriers have a vehicle_spec
    with `supported_connector_types` populated.

    Caller is responsible for adding the chargers' (lat, lon) coords to
    `build_location_index(extra_coords=...)` BEFORE the OSRM matrix is
    built — `build_vrp_data_structures` reads positions out of
    `loc_index` only.
    """
    if not couriers or operations_policy is None:
        return {}
    if not operations_policy.feature_flags.ev_auto_charging_enabled:
        return {}
    if not operations_policy.feature_flags.ev_battery_awareness_enabled:
        return {}

    from app.services.charging_station_service import (
        candidate_chargers_near,
        filter_compatible_chargers,
        list_stations_for_tenant,
    )

    ev_settings = operations_policy.ev_settings
    # Use any one courier as the principal to read tenant-scoped + platform
    # rows; tenant_id comes from the courier itself.
    sample_principal = principal or couriers[0]
    all_stations = await list_stations_for_tenant(db, sample_principal)
    if not all_stations:
        return {}

    out: dict[str, list] = {}
    for courier in couriers:
        spec = getattr(courier, "vehicle_spec", None)
        if spec is None:
            continue
        connectors = getattr(spec, "supported_connector_types", None) or []
        if not connectors:
            continue
        # Identify EVs by `max_battery_range_km` alone — start-of-day
        # charge is assumed full, so current_battery_pct isn't a
        # prerequisite for inserting candidates.
        if not getattr(courier, "max_battery_range_km", None):
            continue
        compatible = filter_compatible_chargers(
            all_stations,
            supported_connectors=connectors,
            allowed_networks=ev_settings.allowed_charger_networks,
            min_kw=int(ev_settings.min_charger_kw),
        )
        if not compatible:
            continue
        nearest = candidate_chargers_near(
            compatible,
            points=[(courier.depot_lat, courier.depot_lon)],
            radius_km=float(ev_settings.max_charger_search_radius_km),
            top_n=3,
        )
        if nearest:
            out[str(courier.id)] = nearest
    return out


def build_location_index(
    orders: list[Order],
    couriers: list[Courier],
    extra_coords: list[tuple[float, float]] | None = None,
    *,
    multi_depot_enabled: bool = False,
) -> tuple[list[tuple[float, float]], dict[tuple[float, float], int]]:
    """Build a deduplicated list of coordinates and a lookup dict.

    `extra_coords` is for Phase 2.3 charger candidates that need to be in
    the OSRM matrix even though they're not order pickups/deliveries.
    Caller is responsible for passing them BEFORE the matrix is built.

    When `multi_depot_enabled` is True, each courier's
    `additional_depots` (the M2M relation) also contributes coordinates so
    that mid-route reload at those depots is matrix-covered. Default-off
    keeps single-depot behavior bit-identical.
    """
    seen: dict[tuple[float, float], int] = {}
    locations: list[tuple[float, float]] = []

    def _add(lat: float, lon: float) -> int:
        key = (round(lat, 5), round(lon, 5))
        if key not in seen:
            seen[key] = len(locations)
            locations.append(key)
        return seen[key]

    for courier in couriers:
        _add(courier.depot_lat, courier.depot_lon)
        if multi_depot_enabled:
            for depot in getattr(courier, "additional_depots", ()) or ():
                _add(depot.lat, depot.lon)

    for order in orders:
        _add(order.pickup_lat, order.pickup_lon)
        _add(order.delivery_lat, order.delivery_lon)

    for lat, lon in (extra_coords or ()):
        _add(lat, lon)

    return locations, seen


async def resolve_vehicle_positions(
    couriers: list[Courier],
    locations: list[tuple[float, float]],
    vrp_vehicles: list[VehicleData],
    db: AsyncSession,
) -> list[tuple[float, float]]:
    """Resolve vehicle positions: real-time if available, depot otherwise.

    Returns a list of location tuples corresponding 1:1 with vrp_vehicles.
    """
    from app.services.courier_service import get_courier_location_from_cache

    courier_by_id = {str(c.id): c for c in couriers}
    vehicle_depot_locs = []

    for v in vrp_vehicles:
        courier = courier_by_id.get(v.courier_id)
        cached_loc = None
        if courier:
            cached_loc = await get_courier_location_from_cache(courier.id, db=db)

        if cached_loc and cached_loc.get("lat") is not None and cached_loc.get("lon") is not None:
            vehicle_depot_locs.append((float(cached_loc["lat"]), float(cached_loc["lon"])))
        else:
            vehicle_depot_locs.append(locations[v.depot_index])

    return vehicle_depot_locs

async def load_order_package_summaries(
    db: AsyncSession,
    order_ids: list[uuid.UUID],
    *,
    tenant_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, dict]:
    """Load per-order package counts and total volume for VRP input building."""
    if not order_ids:
        return {}

    query = (
        select(
            Package.order_id,
            sa_func.count(Package.id).label("count"),
            sa_func.coalesce(sa_func.sum(Package.volume_cm3), 0).label("total_volume_cm3"),
        )
        .where(Package.order_id.in_(order_ids))
        .group_by(Package.order_id)
    )
    if tenant_id is not None:
        query = query.where(tenant_filter(Package, tenant_id))

    result = await db.execute(query)
    return {
        row.order_id: {
            "count": row.count,
            "total_volume_cm3": float(row.total_volume_cm3),
        }
        for row in result.all()
    }


def build_vrp_data_structures(
    orders: list[Order],
    couriers: list[Courier],
    loc_index: dict[tuple[float, float], int],
    global_constraints: dict,
    order_packages: dict,
    optimization_settings: OptimizationSettings | None = None,
    operations_policy: "OperationsPolicy | None" = None,
    candidate_chargers_by_courier: "dict[str, list] | None" = None,
) -> tuple[list[OrderData], list[VehicleData]]:
    """Build VRP order and vehicle models from ORM objects.

    `operations_policy` is optional; when provided and the relevant feature
    flags are on, this helper:
      * Tightens RIDE pickup_latest_time_s by the buffer minutes so the
        driver-arrives-before-passenger constraint is enforced as hard
        (Phase 1.4 — gated by features.ride_pickup_buffer_enabled).
      * Caps each EV vehicle's max_distance_m by current_battery_pct
        × max_battery_range_km (Phase 1.5 — gated by
        features.ev_battery_awareness_enabled).
    Without a policy, falls back to current behaviour for backward compat.
    """
    from app.services.constraint_service import resolve_courier_constraints

    # Pull the relevant gates / numeric defaults once.
    ride_pickup_buffer_on = False
    ev_battery_on = False
    auto_charging_on = False
    multi_depot_on = False
    pickup_buffer_minutes = 5
    if operations_policy is not None:
        ride_pickup_buffer_on = bool(
            operations_policy.feature_flags.ride_pickup_buffer_enabled
        )
        ev_battery_on = bool(operations_policy.feature_flags.ev_battery_awareness_enabled)
        auto_charging_on = bool(operations_policy.feature_flags.ev_auto_charging_enabled)
        multi_depot_on = bool(
            operations_policy.feature_flags.multi_depot_vehicles_enabled
        )
        pickup_buffer_minutes = int(operations_policy.ride_settings.pickup_buffer_minutes)

    vrp_orders: list[OrderData] = []
    for order in orders:
        pickup_key = (round(order.pickup_lat, 5), round(order.pickup_lon, 5))
        delivery_key = (round(order.delivery_lat, 5), round(order.delivery_lon, 5))
        pickup_earliest_s, pickup_latest_s = resolve_pickup_window_seconds(order)
        service_time_s = global_constraints["service_time_minutes"] * 60
        pkg_summary = order_packages.get(order.id, {})

        priority_multiplier = (
            optimization_settings.priority_penalty_multipliers.get(order.priority, 1.0)
            if optimization_settings is not None
            else 1.0
        )
        lateness_penalty_per_minute = (
            optimization_settings.lateness_penalty_per_minute
            if optimization_settings is not None
            else 0
        )
        unserved_penalty = (
            optimization_settings.unserved_penalty
            if optimization_settings is not None
            else 0
        )
        hard_latest_time_s = (
            datetime_to_day_seconds(order.latest_delivery)
            + (optimization_settings.max_lateness_minutes * 60)
            if optimization_settings is not None
            else None
        )

        # ── Phase 1.4: hard pickup buffer for RIDE orders ──
        # Driver must arrive at least `buffer_minutes` BEFORE the passenger's
        # ready time. We achieve this purely by tightening pickup_latest_time_s
        # upstream — the kernel already treats it as hard. Per-order overrides
        # win over the tenant default; if neither is set, fall back to 5 min.
        if (
            ride_pickup_buffer_on
            and order.type == OrderType.RIDE
            and pickup_latest_s is not None
        ):
            buffer_min = (
                order.pickup_buffer_minutes_override
                if order.pickup_buffer_minutes_override is not None
                else pickup_buffer_minutes
            )
            pickup_latest_s = max(0, pickup_latest_s - buffer_min * 60)
            if pickup_earliest_s is not None and pickup_earliest_s > pickup_latest_s:
                # Buffer pushed the window inside-out; mark unservable cleanly
                # by collapsing the window so the kernel hard-rejects the
                # vehicle on this stop.
                pickup_earliest_s = pickup_latest_s

        # RIDE orders carry passengers on the load dimension; non-RIDE
        # orders contribute 0 so the capacity check is a no-op for them.
        passenger_count_for_order = (
            int(order.passenger_count or 0)
            if order.type == OrderType.RIDE
            else 0
        )

        vrp_orders.append(
            OrderData(
                order_id=str(order.id),
                pickup_index=loc_index[pickup_key],
                delivery_index=loc_index[delivery_key],
                pickup_is_depot=_order_pickup_is_depot(order),
                weight_grams=round(order.weight_kg * 1000),
                volume_cm3=round(pkg_summary.get("total_volume_cm3", 0)),
                package_count=pkg_summary.get("count", 1),
                is_fragile=order.is_fragile,
                is_temp_sensitive=order.is_temp_sensitive,
                required_vehicle_tags=frozenset(
                    str(t.id) for t in (order.required_vehicle_tags or [])
                ),
                earliest_time_s=datetime_to_day_seconds(order.earliest_delivery),
                latest_time_s=datetime_to_day_seconds(order.latest_delivery),
                hard_latest_time_s=hard_latest_time_s,
                pickup_earliest_time_s=pickup_earliest_s,
                pickup_latest_time_s=pickup_latest_s,
                service_time_s=service_time_s,
                priority=order.priority,
                priority_penalty_multiplier=priority_multiplier,
                passenger_count=passenger_count_for_order,
                lateness_penalty_per_minute=lateness_penalty_per_minute,
                unserved_penalty=unserved_penalty,
            )
        )

    vrp_vehicles: list[VehicleData] = []
    courier_shift_windows: dict[str, tuple[int, int]] = {}
    for courier in couriers:
        courier_constraints = resolve_courier_constraints(global_constraints, courier)
        depot_key = (round(courier.depot_lat, 5), round(courier.depot_lon, 5))
        start_s = time_to_seconds(courier.working_hours_start)
        end_s = time_to_seconds(courier.working_hours_end)
        courier_shift_windows[str(courier.id)] = (start_s, end_s)
        shift_hours = (end_s - start_s) / 3600

        if (
            courier_constraints.get("max_shift_hours")
            and courier_constraints["max_shift_hours"] * 3600 < (end_s - start_s)
        ):
            end_s = start_s + int(courier_constraints["max_shift_hours"] * 3600)

        break_duration_s = 0
        break_earliest_s = 0
        break_latest_s = 0
        if courier_constraints.get("break_required", True) and shift_hours >= 5.0:
            break_duration_s = courier_constraints["break_duration_minutes"] * 60
            break_earliest_s = start_s + int(
                courier_constraints["break_earliest_offset_hours"] * 3600
            )
            break_latest_s = start_s + int(
                courier_constraints["break_latest_offset_hours"] * 3600
            )

        max_dist_km = courier_constraints.get("max_route_distance_km")
        max_distance_m = int(max_dist_km * 1000) if max_dist_km else 0

        # ── Phase 1.5 / 2.3: EV battery range as constraint ──
        # The pre-charge budget is `current_battery_pct × full_range`. With
        # auto-charging on, dispatch can refuel mid-route, so we give the
        # solver `pre_charge + N × full_range` worth of total distance where
        # N is the number of mid-route charges the policy permits. The
        # charging post-processor walks the returned routes and injects
        # actual CHARGING stops at the points where the budget is consumed.
        # Without auto-charging the cap stays at `current_battery_pct ×
        # full_range` — under-charged EVs simply can't service long routes
        # in that mode.
        full_range_m_for_courier = 0
        if ev_battery_on and getattr(courier, "max_battery_range_km", None):
            full_range_m_for_courier = int(courier.max_battery_range_km * 1000)
            pct = int(getattr(courier, "current_battery_pct", None) or 100)
            pre_charge_m = max(0, int(full_range_m_for_courier * pct / 100))
            if auto_charging_on:
                # Number of mid-route refuels the solver may rely on. 2 is
                # plenty for a normal shift: starting charge + 2 fast-charges
                # = ~1000-1500 km of usable range on a typical EV taxi.
                refuels_allowed = 2
                ev_budget_m = pre_charge_m + refuels_allowed * full_range_m_for_courier
            else:
                ev_budget_m = pre_charge_m
            max_distance_m = (
                min(max_distance_m, ev_budget_m)
                if max_distance_m
                else ev_budget_m
            )
        max_vol = courier_constraints.get("max_volume_cm3")
        capacity_volume_cm3 = round(max_vol) if max_vol else 0
        max_packages = courier_constraints.get("max_packages") or 0
        # Passenger capacity: pulled directly from the courier record.
        # 0 means unconstrained — gas/delivery vehicles with no
        # `max_passengers` set never block RIDE orders by capacity.
        max_passengers_for_courier = int(getattr(courier, "max_passengers", 0) or 0)
        vehicle_costs = (
            optimization_settings.vehicle_class_costs.get(courier.vehicle_type)
            if optimization_settings is not None
            else None
        )
        hard_end_s = (
            end_s + (optimization_settings.max_overtime_minutes * 60)
            if optimization_settings is not None
            else None
        )

        # Derive fuel_cost_per_km from the courier's exact vehicle spec when
        # available; otherwise use delivery-fleet defaults by vehicle class.
        spec = getattr(courier, "vehicle_spec", None)
        if spec is not None and optimization_settings is not None:
            fuel_price = optimization_settings.fuel_prices.get(spec.fuel_type, 35.0)
            computed_fuel_cost_per_km = derive_fuel_cost_per_km(
                spec.fuel_efficiency_km_per_liter, fuel_price
            )
            fuel_type = spec.fuel_type
            fuel_efficiency_km_per_liter = spec.fuel_efficiency_km_per_liter
            co2_g_per_km = spec.co2_emissions_g_per_km
        elif optimization_settings is not None:
            default_fuel_cost = derive_default_vehicle_type_fuel_cost_per_km(
                courier.vehicle_type,
                optimization_settings.fuel_prices,
            )
            computed_fuel_cost_per_km = (
                default_fuel_cost
                if default_fuel_cost is not None
                else (vehicle_costs.fuel_cost_per_km if vehicle_costs else 0)
            )
            default_profile = get_default_vehicle_type_fuel_profile(courier.vehicle_type)
            fuel_efficiency_km_per_liter = (
                default_profile.fuel_efficiency_km_per_liter
                if default_profile is not None
                else None
            )
            fuel_type = default_profile.fuel_type if default_profile is not None else None
            co2_g_per_km = None
        else:
            computed_fuel_cost_per_km = vehicle_costs.fuel_cost_per_km if vehicle_costs else 0
            fuel_type = None
            fuel_efficiency_km_per_liter = None
            co2_g_per_km = None
        osrm_profile = resolve_osrm_profile_from_values(
            courier.vehicle_type,
            spec=spec,
            gross_weight_kg=round(courier_constraints["max_weight_kg"]),
        )

        if optimization_settings is not None:
            computed_fuel_cost_per_km = round(
                computed_fuel_cost_per_km * optimization_settings.fuel_priority_multiplier
            )

        # ── Phase 2 multi-depot resolution ──
        # Gated by `features.multi_depot_vehicles_enabled`; when off, the
        # tuple stays empty / end_depot_index stays None and downstream
        # code follows the single-depot path exactly. When on, resolve
        # the courier's additional_depots and end_depot_id into the
        # location-index coordinate system the solver uses.
        allowed_depot_indices: tuple[int, ...] = ()
        end_depot_index_val: int | None = None
        if multi_depot_on:
            home_idx = loc_index[depot_key]
            extra_indices: list[int] = []
            extras_by_id: dict[str, int] = {}
            for depot in getattr(courier, "additional_depots", ()) or ():
                extra_key = (round(depot.lat, 5), round(depot.lon, 5))
                if extra_key in loc_index:
                    idx = loc_index[extra_key]
                    extra_indices.append(idx)
                    extras_by_id[str(depot.id)] = idx
            if extra_indices:
                # `depot_index` (home) is always the first element by
                # convention. The kernel will resolve via
                # `allowed_depot_indices or (depot_index,)` so the order
                # doesn't actually matter for correctness, but keeping
                # home first makes diagnostics readable.
                allowed_depot_indices = (home_idx, *extra_indices)
            end_depot_id_raw = getattr(courier, "end_depot_id", None)
            if end_depot_id_raw is not None:
                end_depot_id_str = str(end_depot_id_raw)
                if end_depot_id_str == str(courier.depot_id):
                    end_depot_index_val = home_idx
                elif end_depot_id_str in extras_by_id:
                    end_depot_index_val = extras_by_id[end_depot_id_str]
                # If the FK points outside the loaded set (data drift),
                # silently fall back to start. Validation at write time
                # would have rejected this, so reaching here means the
                # tenant raced a config change with a solve.

        vrp_vehicles.append(
            VehicleData(
                courier_id=str(courier.id),
                vehicle_type=courier.vehicle_type,
                capacity_grams=round(courier_constraints["max_weight_kg"] * 1000),
                capacity_volume_cm3=capacity_volume_cm3,
                max_packages=max_packages,
                max_passengers=max_passengers_for_courier,
                depot_index=loc_index[depot_key],
                allowed_depot_indices=allowed_depot_indices,
                end_depot_index=end_depot_index_val,
                start_time_s=start_s,
                end_time_s=end_s,
                hard_end_time_s=hard_end_s,
                break_duration_s=break_duration_s,
                break_earliest_s=break_earliest_s,
                break_latest_s=break_latest_s,
                max_distance_m=max_distance_m,
                # full_range_m is non-zero only when ev_auto_charging_enabled
                # is also on. The kernel uses this to know that a charging
                # visit should reset the remaining-distance budget.
                full_range_m=full_range_m_for_courier if auto_charging_on else 0,
                max_stops=courier_constraints.get("max_stops_per_route") or 0,
                allow_fragile=courier_constraints.get("allow_fragile", True),
                allow_temp_sensitive=courier_constraints.get("allow_temp_sensitive", True),
                vehicle_tags=frozenset(
                    str(t.id) for t in (getattr(courier, "vehicle_tags", None) or [])
                ),
                physical_courier_id=str(courier.id),
                fixed_route_cost=vehicle_costs.fixed_route_cost if vehicle_costs else 0,
                distance_cost_per_km=vehicle_costs.distance_cost_per_km if vehicle_costs else 0,
                labor_cost_per_minute=vehicle_costs.labor_cost_per_minute if vehicle_costs else 0,
                fuel_cost_per_km=computed_fuel_cost_per_km,
                overtime_cost_per_minute=vehicle_costs.overtime_cost_per_minute if vehicle_costs else 0,
                overtime_penalty_per_minute=(
                    optimization_settings.overtime_penalty_per_minute
                    if optimization_settings is not None
                    else 0
                ),
                fuel_type=fuel_type,
                fuel_efficiency_km_per_liter=fuel_efficiency_km_per_liter,
                co2_g_per_km=co2_g_per_km,
                osrm_profile=osrm_profile,
            )
        )

    # ── Phase 2.3: synthetic charging-stop OrderData (DISABLED) ──
    # Historically we injected one synthetic ``OrderData`` per (courier ×
    # charger) pair with ``is_charging=True``, hoping the OR-Tools kernel
    # would treat them as refuel requests. In practice the kernel never
    # implemented the battery-reset semantic: the synthetic orders are
    # plain pickup+delivery rows at a single node, weight=0, volume=0,
    # ``unserved_penalty=0``. The solver finds them "cheap" to insert
    # (visiting one only costs ``service_time_s``) and packs them at the
    # start of routes where there's slack, producing pathological output
    # like "two consecutive chargers then idle for four hours then start
    # delivering". The charging post-processor (``charging_post_processor.
    # inject_charging_stops_into_solution``) handles all charging insertion
    # correctly with reachability, operating-hours, contention, and RIDE
    # interlock guards. Leave the synthetic-injection branch disabled.
    if False and auto_charging_on and candidate_chargers_by_courier:
        ride_settings = operations_policy.ride_settings if operations_policy else None
        ev_settings = operations_policy.ev_settings if operations_policy else None
        if ev_settings is not None:
            from app.services.charging_station_service import (
                estimate_charge_time_seconds,
            )

            for vehicle_idx, courier in enumerate(couriers):
                stations = candidate_chargers_by_courier.get(str(courier.id)) or []
                if not stations:
                    continue
                spec = getattr(courier, "vehicle_spec", None)
                battery_kwh = getattr(spec, "battery_kwh", None) if spec else None
                if not battery_kwh:
                    # Without a battery_kwh on the spec we can't estimate
                    # charge time. Skip this courier rather than guess.
                    continue
                target_pct = int(ev_settings.charge_target_pct_for_planning)
                planning_kw = int(ev_settings.estimated_kw_used_for_planning)
                # Flat overhead per charging stop — covers parking,
                # plug-in handshake, payment, and the typical wait when
                # stalls are occupied. Added on top of the energy-delta
                # charge time. Tune down once we have real telemetry
                # ('arrived' → 'plugged_in' deltas from the driver app).
                setup_overhead_s = (
                    int(ev_settings.charging_setup_overhead_minutes) * 60
                )
                # Charging stops happen mid-shift after the courier has
                # used some of the day's charge. We estimate the planned
                # delta from a worst-case low SoC (0%) up to the target
                # — the actual % drawn varies per route, but planning
                # for the longest-likely charge is the conservative
                # choice that keeps schedules realistic.
                current_pct_for_planning = 0
                shift_start, shift_end = courier_shift_windows.get(
                    str(courier.id), (0, 86400)
                )

                for station in stations:
                    station_key = (round(station.lat, 5), round(station.lon, 5))
                    if station_key not in loc_index:
                        # Should never happen — caller pre-included these
                        # coords. Skip rather than crash.
                        continue
                    charger_node = loc_index[station_key]
                    charge_time_s = estimate_charge_time_seconds(
                        current_pct=current_pct_for_planning,
                        target_pct=target_pct,
                        battery_kwh=float(battery_kwh),
                        charger_kw=min(int(station.max_kw or 50), planning_kw),
                    ) + setup_overhead_s
                    vrp_orders.append(
                        OrderData(
                            order_id=f"__CHARGER_{station.id}_{courier.id}__",
                            pickup_index=charger_node,
                            delivery_index=charger_node,
                            weight_grams=0,
                            volume_cm3=0,
                            package_count=0,
                            is_fragile=False,
                            is_temp_sensitive=False,
                            required_vehicle_tags=frozenset(),
                            earliest_time_s=shift_start,
                            latest_time_s=shift_end,
                            hard_latest_time_s=shift_end,
                            service_time_s=charge_time_s,
                            pickup_earliest_time_s=shift_start,
                            pickup_latest_time_s=shift_end,
                            priority=OrderPriority.LOW,
                            priority_penalty_multiplier=1.0,
                            lateness_penalty_per_minute=0,
                            # Truly optional: solver only inserts when
                            # the route would otherwise fail the per-
                            # segment battery check.
                            unserved_penalty=0,
                            allowed_vehicle_indices=(vehicle_idx,),
                            is_charging=True,
                        )
                    )

    return vrp_orders, vrp_vehicles


def _order_pickup_is_depot(order: Order) -> bool:
    """Return whether an order pickup should be modeled as depot loading.

    Seeded and normal outbound orders carry a depot_id and start from a
    warehouse/loading-bay coordinate that may be slightly different from every
    courier's home depot coordinate.  Reverse/customer-return jobs travel the
    opposite direction, so their customer pickup must remain a visible serviced
    pickup.
    """
    return getattr(order, "depot_id", None) is not None and order.type not in {
        OrderType.REVERSE,
        OrderType.RETURN,
    }


def validate_batch_solution(
    solution: VRPSolution,
    orders: list[Order],
    vrp_vehicles: list[VehicleData],
    multi_trip_vehicles: list[VehicleData],
    order_packages: dict,
    optimization_settings: OptimizationSettings | None = None,
    operations_policy: OperationsPolicy | None = None,
):
    """Build the validator inputs and validate a batch VRP solution."""
    order_weight_map = {str(order.id): round(order.weight_kg * 1000) for order in orders}
    order_volume_map = {
        str(order.id): round(order_packages.get(order.id, {}).get("total_volume_cm3", 0))
        for order in orders
    }
    order_pkg_count_map = {
        str(order.id): order_packages.get(order.id, {}).get("count", 1)
        for order in orders
    }
    order_tw_map = {
        str(order.id): (
            datetime_to_day_seconds(order.earliest_delivery),
            datetime_to_day_seconds(order.latest_delivery)
            + (
                optimization_settings.max_lateness_minutes * 60
                if optimization_settings is not None
                else 0
            ),
        )
        for order in orders
    }
    order_pickup_window_map = {
        str(order.id): resolve_pickup_window_seconds(order)
        for order in orders
    }
    depot_pickup_order_ids = {
        str(order.id)
        for order in orders
        if _order_pickup_is_depot(order)
    }

    constraint_map = {
        vehicle.courier_id: RouteConstraints(
            capacity_grams=vehicle.capacity_grams,
            capacity_volume_cm3=vehicle.capacity_volume_cm3,
            max_packages=vehicle.max_packages,
            start_time_s=vehicle.start_time_s,
            end_time_s=vehicle.hard_end_time_s or vehicle.end_time_s,
            max_stops=vehicle.max_stops,
            max_distance_m=vehicle.max_distance_m,
            allowed_depot_indices=vehicle.allowed_depot_indices,
            end_depot_index=vehicle.end_depot_index,
            depot_index=vehicle.depot_index,
        )
        for vehicle in multi_trip_vehicles
    }
    for vehicle in vrp_vehicles:
        constraint_map[vehicle.courier_id] = RouteConstraints(
            capacity_grams=vehicle.capacity_grams,
            capacity_volume_cm3=vehicle.capacity_volume_cm3,
            max_packages=vehicle.max_packages,
            start_time_s=vehicle.start_time_s,
            end_time_s=vehicle.hard_end_time_s or vehicle.end_time_s,
            max_stops=vehicle.max_stops,
            max_distance_m=vehicle.max_distance_m,
            allowed_depot_indices=vehicle.allowed_depot_indices,
            end_depot_index=vehicle.end_depot_index,
            depot_index=vehicle.depot_index,
        )

    # Phase 2.4: per-type lateness grace. When the tenant has
    # `ride_strict_lateness_enabled`, RIDE orders get the configured
    # ride_settings.lateness_grace_seconds (default 0) instead of the
    # validator's default 300s. Deliveries are unaffected.
    lateness_grace_by_order_id: dict[str, int] = {}
    if (
        operations_policy is not None
        and operations_policy.feature_flags.ride_strict_lateness_enabled
    ):
        ride_grace_s = int(operations_policy.ride_settings.lateness_grace_seconds)
        for order in orders:
            if order.type == OrderType.RIDE:
                lateness_grace_by_order_id[str(order.id)] = ride_grace_s

    return validate_solution(
        solution,
        order_weights=order_weight_map,
        order_volumes=order_volume_map,
        order_package_counts=order_pkg_count_map,
        route_constraints=constraint_map,
        order_time_windows=order_tw_map,
        order_pickup_windows=order_pickup_window_map,
        depot_pickup_order_ids=depot_pickup_order_ids,
        lateness_grace_by_order_id=lateness_grace_by_order_id,
    )


def merge_cluster_solutions(solutions: list[VRPSolution]) -> VRPSolution:
    """Merge independently solved cluster solutions into one combined solution."""
    from app.algorithms.vrp_solver import SolverStatus

    all_routes: list[PlannedRoute] = []
    all_unassigned: list[str] = []
    total_dist = 0
    total_dur = 0
    all_cost_breakdowns: list[EconomicCostBreakdown | None] = []

    status_priority = {
        SolverStatus.OPTIMAL: 0,
        SolverStatus.FEASIBLE: 1,
        SolverStatus.GREEDY_FALLBACK: 2,
        SolverStatus.NOT_FOUND: 3,
        SolverStatus.ERROR: 4,
    }
    worst_status = SolverStatus.OPTIMAL

    seen_couriers: set[str] = set()
    for solution in solutions:
        for route in solution.routes:
            if route.courier_id in seen_couriers:
                logger.warning(
                    "Duplicate courier %s across clusters — dropping extra route",
                    route.courier_id[:8],
                )
                all_unassigned.extend(
                    stop.order_id
                    for stop in route.stops
                    if stop.stop_type not in ("DEPOT_RETURN", "DEPOT_RELOAD")
                )
            else:
                seen_couriers.add(route.courier_id)
                all_routes.append(route)

        all_unassigned.extend(solution.unassigned_orders)
        total_dist += solution.total_distance_m
        total_dur += solution.total_duration_s
        all_cost_breakdowns.append(solution.cost_breakdown)
        if status_priority.get(solution.solver_status, 4) > status_priority.get(
            worst_status, 0
        ):
            worst_status = solution.solver_status

    return VRPSolution(
        routes=all_routes,
        unassigned_orders=all_unassigned,
        total_distance_m=total_dist,
        total_duration_s=total_dur,
        solver_status=worst_status,
        cost_breakdown=_combine_cost_breakdowns(all_cost_breakdowns),
    )


def _unmap_route_indices(route: PlannedRoute, sorted_loc_indices: list[int]) -> PlannedRoute:
    """Return *route* with each stop's location_index converted from cluster-local to global."""
    new_stops = [
        replace(stop, location_index=sorted_loc_indices[stop.location_index])
        for stop in route.stops
    ]
    return replace(route, stops=new_stops)


# Validator's ERROR-severity grace on top of the (already lateness-shifted)
# soft-latest cutoff — see `solution_validator._check_time_windows`. The
# validator's `latest_s` already includes max_lateness_minutes; this constant
# is the additional 300s buffer the validator allows before flagging ERROR.
_RESCUE_DELIVERY_GRACE_S = 300


def _rescue_route_lateness_minutes(planned_route: PlannedRoute, orders_by_id) -> int:
    """Sum of soft-window lateness (minutes) across all deliveries in *planned_route*.

    Used as a tie-breaker in the rescue's insertion selection. Mirrors the
    objective the validator's warning/error tiers are based on, so picking
    insertions with low summed-lateness keeps the on-time rate up while the
    rescue is filling capacity.
    """
    total_late_s = 0
    for stop in planned_route.stops:
        if stop.stop_type != "DELIVERY":
            continue
        order = orders_by_id.get(stop.order_id)
        if order is None:
            continue
        late = stop.arrival_time_s - order.latest_time_s
        if late > 0:
            total_late_s += late
    return (total_late_s + 59) // 60  # ceil to minutes


def _rescue_best_insertion(
    vrp_input,
    boards,
    order,
    orders_by_id,
    max_lateness_grace_s: int,
):
    """Cheapest-insertion that prefers on-time slots over slightly cheaper-but-late ones.

    Mirrors `human_dispatcher_baseline._best_repair_insertion` (same iteration,
    same simulator) but its scoring is `(delta_lateness_min, delta_distance_m,
    delta_duration_s, route_end_time_s, ...)` instead of distance-first. That
    flips a typical tie: if two insertions both fit, we pick the one with less
    delivery slip even when its distance delta is a few percent higher. Also
    pre-filters insertions that would trip the validator's ERROR threshold so
    the caller doesn't have to re-check.
    """
    from app.algorithms.human_dispatcher_baseline import (  # noqa: PLC0415
        _candidate_insertion_intents,
        _simulate_intents,
    )

    best: tuple | None = None
    for board in boards:
        cur_route = board.simulation.route if board.simulation else None
        cur_distance = cur_route.total_distance_m if cur_route else 0
        cur_duration = cur_route.total_duration_s if cur_route else 0
        cur_lateness = _rescue_route_lateness_minutes(cur_route, orders_by_id) if cur_route else 0

        for intents in _candidate_insertion_intents(
            vrp_input, board.intents, order, board.vehicle
        ):
            simulation = _simulate_intents(
                vrp_input, board.vehicle, intents, orders_by_id
            )
            if simulation is None:
                continue
            new_route = simulation.route
            # Skip up-front anything the validator would mark ERROR.
            if not _rescue_route_passes_validator(
                new_route, orders_by_id, max_lateness_grace_s=max_lateness_grace_s
            ):
                continue
            new_lateness = _rescue_route_lateness_minutes(new_route, orders_by_id)
            score = (
                new_lateness - cur_lateness,            # 1. minimise added lateness
                new_route.total_distance_m - cur_distance,  # 2. then cost
                new_route.total_duration_s - cur_duration,  # 3. then duration
                new_route.route_end_time_s,             # 4. earlier endings break ties
                len(board.intents),                     # 5. shorter routes preferred
                board.vehicle.courier_id,               # 6. deterministic
            )
            if best is None or score < best[0]:
                best = (score, board, intents, simulation)
    return best


def _rescue_route_passes_validator(
    planned_route: PlannedRoute,
    orders_by_id,
    *,
    max_lateness_grace_s: int,
) -> bool:
    """Return False if any DELIVERY in *planned_route* would trip the validator.

    Mirrors `solution_validator._check_time_windows` ERROR threshold for
    delivery soft windows. Caller supplies `max_lateness_grace_s`, which must
    equal `max_lateness_minutes × 60 + 300` to match the validator's
    threshold exactly. We don't re-check capacity / pickup-window / shift-end
    / max-distance because `_simulate_intents` already enforces those (and
    uses the same data).
    """
    for stop in planned_route.stops:
        if stop.stop_type != "DELIVERY":
            continue
        order = orders_by_id.get(stop.order_id)
        if order is None:
            continue
        latest = order.latest_time_s
        if stop.arrival_time_s > latest + max_lateness_grace_s:
            return False
    return True


def rescue_unassigned_pre_stitch(
    *,
    raw_routes: list[PlannedRoute],
    unassigned_order_ids: set[str],
    virtual_vehicles: list[VehicleData],
    vrp_input,
    max_lateness_minutes: int = 0,
    time_budget_s: float = 30.0,
) -> tuple[list[PlannedRoute], set[str], dict]:
    """Cheapest-insertion rescue for unassigned orders across all routes.

    Used after decomposed solves: each cluster's solver only saw its own
    fleet partition, so an order that fit no vehicle in cluster A may still
    fit one in cluster B. This pass tries every unassigned order against
    every route and every idle virtual vehicle in the global fleet.

    Operates on PRE-stitch routes (one route per virtual vehicle, no
    DEPOT_RETURN/DEPOT_RELOAD synthetic stops). Run multi-trip stitching
    afterwards on the returned routes.

    Reuses the human-baseline simulation helpers so the feasibility model
    matches the simulator the cluster solver used. After each candidate
    insertion we additionally apply `_rescue_route_passes_validator` to
    filter out any insertion that would later trip
    `_maybe_use_lexicographic_fallback` — that fallback runs another full
    solve and was the source of multi-minute hangs when several scenarios
    triggered it concurrently.

    Returns (routes, newly_assigned_ids, metrics). Metrics reports
    rebuild_failures, validator_rejections, and timed_out for diagnostics.
    """
    metrics: dict = {
        "rebuild_failures": 0,
        "validator_rejections": 0,
        "timed_out": False,
        "orders_considered": 0,
        "elapsed_s": 0.0,
    }
    if not unassigned_order_ids:
        return list(raw_routes), set(), metrics

    import time as _time  # noqa: PLC0415

    from app.algorithms.human_dispatcher_baseline import (  # noqa: PLC0415
        _RouteBoard,
        _StopIntent,
        _order_sort_key,
        _simulate_intents,
    )

    orders_by_id = {o.order_id: o for o in vrp_input.orders}
    vehicle_by_courier = {v.courier_id: v for v in virtual_vehicles}

    boards: list[_RouteBoard] = []
    untouched_routes: list[PlannedRoute] = []
    courier_in_board: set[str] = set()

    for route in raw_routes:
        vehicle = vehicle_by_courier.get(route.courier_id)
        if vehicle is None:
            untouched_routes.append(route)
            continue
        intents = [
            _StopIntent(stop.order_id, stop.stop_type)
            for stop in route.stops
            if stop.stop_type in ("PICKUP", "DELIVERY")
        ]
        if not intents:
            untouched_routes.append(route)
            continue
        sim = _simulate_intents(vrp_input, vehicle, intents, orders_by_id)
        if sim is None:
            metrics["rebuild_failures"] += 1
            untouched_routes.append(route)
            continue
        boards.append(_RouteBoard(vehicle=vehicle, intents=intents, simulation=sim))
        courier_in_board.add(vehicle.courier_id)

    for vehicle in virtual_vehicles:
        if vehicle.courier_id in courier_in_board:
            continue
        boards.append(_RouteBoard(vehicle=vehicle))

    assigned_order_ids: set[str] = set()
    for board in boards:
        for intent in board.intents:
            assigned_order_ids.add(intent.order_id)
    for route in untouched_routes:
        for stop in route.stops:
            if stop.stop_type in ("PICKUP", "DELIVERY"):
                assigned_order_ids.add(stop.order_id)

    candidates = [
        orders_by_id[oid]
        for oid in unassigned_order_ids
        if oid in orders_by_id and oid not in assigned_order_ids
    ]
    candidates.sort(key=_order_sort_key)

    # Match the validator's ERROR threshold exactly: latest_s = latest_delivery
    # + max_lateness_minutes*60, validator flags at arrival > latest_s + 300.
    max_lateness_grace_s = max(0, max_lateness_minutes) * 60 + _RESCUE_DELIVERY_GRACE_S
    metrics["max_lateness_grace_s"] = max_lateness_grace_s

    newly_assigned: set[str] = set()
    deadline = _time.perf_counter() + max(time_budget_s, 0.0)
    for order in candidates:
        if _time.perf_counter() >= deadline:
            metrics["timed_out"] = True
            break
        metrics["orders_considered"] += 1
        existing = [b for b in boards if b.simulation is not None]
        unused = [b for b in boards if b.simulation is None]
        placed = False
        for candidate_boards in (existing, unused):
            best = _rescue_best_insertion(
                vrp_input,
                candidate_boards,
                order,
                orders_by_id,
                max_lateness_grace_s=max_lateness_grace_s,
            )
            if best is None:
                continue
            _, board, intents, sim = best
            board.intents = intents
            board.simulation = sim
            newly_assigned.add(order.order_id)
            placed = True
            break
        if not placed:
            # No board in either pool had a feasible insertion within the
            # validator's grace. Counted for diagnostics — was it the simulator
            # rejecting it or our grace filter?
            metrics["validator_rejections"] += 1

    final_routes = list(untouched_routes)
    for board in boards:
        if board.simulation is not None:
            final_routes.append(board.simulation.route)

    metrics["elapsed_s"] = round(_time.perf_counter() - (deadline - time_budget_s), 3)
    if metrics["rebuild_failures"]:
        logger.warning(
            "rescue_unassigned_pre_stitch: %d existing route(s) failed re-simulation "
            "under global view; left untouched",
            metrics["rebuild_failures"],
        )
    if metrics["validator_rejections"]:
        logger.info(
            "rescue_unassigned_pre_stitch: %d candidate insertion(s) rejected by "
            "validator-grace check (kept solution clean of soft-window errors)",
            metrics["validator_rejections"],
        )
    if metrics["timed_out"]:
        logger.warning(
            "rescue_unassigned_pre_stitch: hit %.1fs time budget after considering "
            "%d/%d orders; remaining left unassigned",
            time_budget_s, metrics["orders_considered"], len(candidates),
        )

    return final_routes, newly_assigned, metrics


# Skip the time-corrected refinement pass when max relative delta between the
# baseline-adjusted matrix and the time-corrected matrix is under this fraction.
# At <5% delta the primary solution is already near-optimal under the corrected
# matrix and the refinement adds no real value while costing 30-60s per scenario.
# Used by both Compare Routes (`route_scenario_service._solve_scenario_vrp`)
# and the production "Assign Optimally" path (`dispatch_service.dispatch_batch`).
REFINEMENT_SKIP_THRESHOLD = 0.05


def matrix_max_relative_delta(
    baseline_matrices: dict[str, list[list[int]]] | None,
    corrected_matrices: dict[str, list[list[int]]] | None,
) -> float:
    """Return max(|c - b| / max(b, 1)) over every (profile, i, j) cell.

    Used to gate the refinement pass: when the time-of-day correction barely
    moves any cell vs the baseline, the primary solution is already at the
    corrected optimum and re-solving is wasted work. Cells where baseline = 0
    are skipped (they're either depot-self-loops or unused arcs).
    """
    if not baseline_matrices or not corrected_matrices:
        return 0.0
    import numpy as np  # noqa: PLC0415
    max_delta = 0.0
    for profile, corrected in corrected_matrices.items():
        baseline = baseline_matrices.get(profile)
        if baseline is None:
            continue
        b = np.asarray(baseline, dtype=np.float64)
        c = np.asarray(corrected, dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            rel = np.where(b > 0, np.abs(c - b) / b, 0.0)
        max_delta = max(max_delta, float(rel.max()) if rel.size else 0.0)
    return max_delta


def expand_virtual_vehicles(
    physical_vehicles: list[VehicleData],
    global_constraints: dict,
) -> list[VehicleData]:
    """Expand physical couriers into virtual vehicles for multi-trip solving."""
    max_trips = global_constraints.get("max_trips_per_day", 3)
    reload_time_s = global_constraints.get("depot_reload_time_minutes", 15) * 60

    if max_trips <= 1:
        return physical_vehicles

    expanded: list[VehicleData] = []
    # For EV-aware tenants the post-charge target percentage governs the
    # SoC we assume each depot reload restores the vehicle to. Mirrors the
    # default used by ``charging_post_processor``.
    depot_recharge_target_pct = int(global_constraints.get(
        "ev_depot_recharge_target_pct", 80,
    ) or 80)

    for physical_vehicle in physical_vehicles:
        total_window = physical_vehicle.end_time_s - physical_vehicle.start_time_s
        if total_window <= 0:
            expanded.append(physical_vehicle)
            continue

        usable_time = total_window - (max_trips - 1) * reload_time_s
        if usable_time <= 0:
            expanded.append(physical_vehicle)
            continue

        slice_duration = usable_time // max_trips
        if slice_duration < 1800:
            effective_trips = max(1, usable_time // 1800)
        else:
            effective_trips = max_trips

        for trip_idx in range(effective_trips):
            trip_start = physical_vehicle.start_time_s + trip_idx * (
                slice_duration + reload_time_s
            )
            trip_end = min(trip_start + slice_duration, physical_vehicle.end_time_s)
            next_trip_start = (
                physical_vehicle.start_time_s + (trip_idx + 1) * (slice_duration + reload_time_s)
                if trip_idx < effective_trips - 1
                else None
            )
            hard_end_time_s = physical_vehicle.hard_end_time_s
            if hard_end_time_s is not None and next_trip_start is not None:
                hard_end_time_s = min(hard_end_time_s, next_trip_start)

            break_dur = 0
            break_ear = 0
            break_lat = 0
            if trip_idx == 0 and physical_vehicle.break_duration_s > 0:
                raw_break_lat = min(
                    physical_vehicle.break_latest_s,
                    trip_end - physical_vehicle.break_duration_s,
                )
                if physical_vehicle.break_earliest_s < raw_break_lat:
                    break_dur = physical_vehicle.break_duration_s
                    break_ear = physical_vehicle.break_earliest_s
                    break_lat = raw_break_lat

            # Capacity-like state that must reset every trip:
            #
            #   - load dimensions (weight/volume/packages/passengers) reset
            #     automatically because OR-Tools sees each virtual vehicle
            #     as a fresh independent vehicle.
            #
            #   - ``max_distance_m`` is the *initial* battery budget for
            #     EV-aware tenants. Trip 0 keeps the physical courier's
            #     starting SoC (e.g. 30 % left from yesterday). Trip > 0
            #     happens after a depot reload — for EVs we assume the
            #     depot tops the vehicle up to ``depot_recharge_target_pct``
            #     of ``full_range_m``. For non-EVs ``full_range_m == 0`` and
            #     ``max_distance_m`` acts as a fixed route distance cap,
            #     so we keep the same value across trips.
            full_range_m = physical_vehicle.full_range_m
            if trip_idx == 0 or full_range_m <= 0:
                trip_max_distance_m = physical_vehicle.max_distance_m
            else:
                trip_max_distance_m = int(
                    full_range_m * depot_recharge_target_pct / 100
                )

            # Phase 5 — only the LAST virtual trip ends at the physical
            # vehicle's end_depot. Earlier trips end at the home depot so
            # the inter-trip DEPOT_RETURN can reload there. Single-depot
            # vehicles (`end_depot_index is None`) propagate None to every
            # trip, matching pre-Phase-5 behavior.
            is_last_trip = trip_idx == effective_trips - 1
            trip_end_depot_index = (
                physical_vehicle.end_depot_index if is_last_trip else None
            )

            expanded.append(
                VehicleData(
                    courier_id=f"{physical_vehicle.courier_id}__trip{trip_idx}",
                    vehicle_type=physical_vehicle.vehicle_type,
                    capacity_grams=physical_vehicle.capacity_grams,
                    capacity_volume_cm3=physical_vehicle.capacity_volume_cm3,
                    max_packages=physical_vehicle.max_packages,
                    max_passengers=physical_vehicle.max_passengers,
                    depot_index=physical_vehicle.depot_index,
                    allowed_depot_indices=physical_vehicle.allowed_depot_indices,
                    end_depot_index=trip_end_depot_index,
                    start_time_s=trip_start,
                    end_time_s=trip_end,
                    hard_end_time_s=hard_end_time_s,
                    break_duration_s=break_dur,
                    break_earliest_s=break_ear,
                    break_latest_s=break_lat,
                    max_distance_m=trip_max_distance_m,
                    full_range_m=full_range_m,
                    max_stops=physical_vehicle.max_stops,
                    allow_fragile=physical_vehicle.allow_fragile,
                    allow_temp_sensitive=physical_vehicle.allow_temp_sensitive,
                    vehicle_tags=physical_vehicle.vehicle_tags,
                    trip_index=trip_idx,
                    physical_courier_id=physical_vehicle.physical_courier_id
                    or physical_vehicle.courier_id,
                    fixed_route_cost=physical_vehicle.fixed_route_cost if trip_idx == 0 else 0,
                    distance_cost_per_km=physical_vehicle.distance_cost_per_km,
                    labor_cost_per_minute=physical_vehicle.labor_cost_per_minute,
                    fuel_cost_per_km=physical_vehicle.fuel_cost_per_km,
                    overtime_cost_per_minute=physical_vehicle.overtime_cost_per_minute,
                    overtime_penalty_per_minute=physical_vehicle.overtime_penalty_per_minute,
                    fuel_type=physical_vehicle.fuel_type,
                    fuel_efficiency_km_per_liter=physical_vehicle.fuel_efficiency_km_per_liter,
                    co2_g_per_km=physical_vehicle.co2_g_per_km,
                    osrm_profile=physical_vehicle.osrm_profile,
                )
            )

    return expanded


def stitch_multi_trip_routes(
    solution: VRPSolution,
    virtual_vehicles: list[VehicleData],
    locations: list[tuple[float, float]],
    duration_matrix: list[list[int]] | None = None,
    *,
    reload_time_s: int = 900,
) -> VRPSolution:
    """Merge virtual-vehicle routes back into single routes per physical courier."""
    vv_map = {vehicle.courier_id: vehicle for vehicle in virtual_vehicles}

    courier_trips: dict[str, list[tuple[int, PlannedRoute]]] = {}
    for route in solution.routes:
        vehicle = vv_map.get(route.courier_id)
        if vehicle is None:
            physical_courier_id = route.courier_id
            trip_idx = 0
        else:
            physical_courier_id = vehicle.physical_courier_id or route.courier_id
            trip_idx = vehicle.trip_index

        courier_trips.setdefault(physical_courier_id, []).append((trip_idx, route))

    stitched_routes: list[PlannedRoute] = []
    for physical_courier_id, trips in courier_trips.items():
        trips.sort(key=lambda item: item[0])

        if len(trips) == 1:
            _, route = trips[0]
            stitched_routes.append(
                PlannedRoute(
                    courier_id=physical_courier_id,
                    stops=route.stops,
                    total_distance_m=route.total_distance_m,
                    total_duration_s=route.total_duration_s,
                    route_start_time_s=route.route_start_time_s,
                    route_end_time_s=route.route_end_time_s,
                    cost_breakdown=route.cost_breakdown,
                )
            )
            continue

        all_stops: list[PlannedStop] = []
        total_dist = 0
        total_dur = 0
        trip_costs: list[EconomicCostBreakdown | None] = []
        route_start_time = min(route.route_start_time_s for _, route in trips)
        route_end_time = max(route.route_end_time_s for _, route in trips)

        for idx, (_, route) in enumerate(trips):
            all_stops.extend(route.stops)
            total_dist += route.total_distance_m
            total_dur += route.total_duration_s
            trip_costs.append(route.cost_breakdown)

            if idx < len(trips) - 1:
                vehicle = vv_map.get(route.courier_id)
                depot_idx = vehicle.depot_index if vehicle else 0
                depot_lat, depot_lon = (
                    locations[depot_idx] if depot_idx < len(locations) else (0, 0)
                )
                last_departure = route.stops[-1].departure_time_s if route.stops else 0

                # Compute actual return-to-depot travel time
                return_travel_s = 0
                if duration_matrix is not None and route.stops:
                    last_loc = route.stops[-1].location_index
                    if (
                        last_loc < len(duration_matrix)
                        and depot_idx < len(duration_matrix)
                    ):
                        return_travel_s = duration_matrix[last_loc][depot_idx]

                depot_arrival = last_departure + return_travel_s
                depot_departure = depot_arrival + reload_time_s

                all_stops.append(
                    PlannedStop(
                        order_id="__DEPOT_RETURN__",
                        stop_type="DEPOT_RETURN",
                        location_index=depot_idx,
                        lat=depot_lat,
                        lon=depot_lon,
                        arrival_time_s=depot_arrival,
                        departure_time_s=depot_arrival,
                    )
                )
                all_stops.append(
                    PlannedStop(
                        order_id="__DEPOT_RELOAD__",
                        stop_type="DEPOT_RELOAD",
                        location_index=depot_idx,
                        lat=depot_lat,
                        lon=depot_lon,
                        arrival_time_s=depot_arrival,
                        departure_time_s=depot_departure,
                    )
                )

        stitched_routes.append(
            PlannedRoute(
                courier_id=physical_courier_id,
                stops=all_stops,
                total_distance_m=total_dist,
                total_duration_s=total_dur,
                route_start_time_s=route_start_time,
                route_end_time_s=route_end_time,
                cost_breakdown=_combine_cost_breakdowns(
                    trip_costs,
                    active_vehicle_count=1,
                ),
            )
        )

    unserved_penalty = solution.cost_breakdown.unserved_penalty if solution.cost_breakdown else 0
    return VRPSolution(
        routes=stitched_routes,
        unassigned_orders=solution.unassigned_orders,
        total_distance_m=solution.total_distance_m,
        total_duration_s=solution.total_duration_s,
        solver_status=solution.solver_status,
        cost_breakdown=_combine_cost_breakdowns(
            [route.cost_breakdown for route in stitched_routes],
            unserved_penalty=unserved_penalty,
            active_vehicle_count=len(stitched_routes),
        ),
    )


def estimate_route_starting_load_grams(
    stops: list[RouteStop],
    order_weight_grams: dict[uuid.UUID, int],
) -> int:
    """Estimate the onboard load (grams) before the first actionable stop."""
    return _estimate_route_starting_dim(stops, order_weight_grams)


def estimate_route_starting_load_volume_cm3(
    stops: list[RouteStop],
    order_volume_cm3: dict[uuid.UUID, int],
) -> int:
    """Estimate the onboard volume (cm³) before the first actionable stop."""
    return _estimate_route_starting_dim(stops, order_volume_cm3)


def estimate_route_starting_load_packages(
    stops: list[RouteStop],
    order_package_counts: dict[uuid.UUID, int],
) -> int:
    """Estimate the onboard package count before the first actionable stop."""
    return _estimate_route_starting_dim(stops, order_package_counts)


def estimate_route_starting_load_passengers(
    stops: list[RouteStop],
    order_passenger_counts: dict[uuid.UUID, int],
) -> int:
    """Estimate the onboard passenger count before the first actionable stop."""
    return _estimate_route_starting_dim(stops, order_passenger_counts)


def _estimate_route_starting_dim(
    stops: list[RouteStop],
    per_order_dim: dict[uuid.UUID, int],
) -> int:
    """Sum a per-order quantity for orders whose delivery is still actionable
    but pickup is already complete — i.e., they're already on the truck."""
    per_order_flags: dict[uuid.UUID, tuple[bool, bool]] = {}
    for stop in stops:
        if stop.status in TERMINAL_STOP_STATUSES or stop.order_id is None:
            continue

        has_pickup, has_delivery = per_order_flags.get(stop.order_id, (False, False))
        if stop.stop_type == StopType.PICKUP:
            has_pickup = True
        else:
            has_delivery = True
        per_order_flags[stop.order_id] = (has_pickup, has_delivery)

    total = 0
    for order_id, (has_pickup, has_delivery) in per_order_flags.items():
        if has_delivery and not has_pickup:
            total += per_order_dim.get(order_id, 0)
    return total


def seconds_to_datetime(seconds_from_midnight: int) -> datetime:
    """Convert seconds-from-midnight to a datetime on the current UTC day."""
    base = utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(seconds=seconds_from_midnight)
