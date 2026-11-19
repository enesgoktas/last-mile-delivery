"""Boundary converters between VRPInput/VRPSolution and the internal domain model."""
from __future__ import annotations

from app.algorithms.vrp_solver import (
    EconomicCostBreakdown,
    OrderData,
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VehicleData,
    VRPInput,
    VRPSolution,
)
from app.models.enums import OrderPriority

from .models import (
    CostBreakdown,
    MatrixView,
    Problem,
    Request,
    Route,
    Solution,
    Vehicle,
    VehicleCost,
    Visit,
    VisitKind,
)


def build_problem(vrp_input: VRPInput, *, spread_fleet: bool = True) -> Problem:
    """Convert a VRPInput into the solver-internal Problem representation."""
    matrix = MatrixView(
        duration_matrix=vrp_input.duration_matrix,
        distance_matrix=vrp_input.distance_matrix,
        duration_matrices=vrp_input.duration_matrices,
        distance_matrices=vrp_input.distance_matrices,
    )

    requests = [_build_request(o) for o in vrp_input.orders]
    vehicles = [_build_vehicle(v, idx) for idx, v in enumerate(vrp_input.vehicles)]

    return Problem(
        requests=requests,
        vehicles=vehicles,
        matrix=matrix,
        locations=list(vrp_input.locations),
        spread_fleet=spread_fleet,
    )


def _build_request(order: OrderData) -> Request:
    pickup_window = (
        order.pickup_earliest_time_s or 0,
        order.pickup_latest_time_s or order.latest_time_s,
    )
    delivery_window = (order.earliest_time_s, order.latest_time_s)

    return Request(
        id=order.order_id,
        pickup_node=order.pickup_index,
        delivery_node=order.delivery_index,
        pickup_window=pickup_window,
        delivery_window=delivery_window,
        hard_delivery_deadline=order.hard_latest_time_s,
        service_time_pickup_s=0,
        service_time_delivery_s=order.service_time_s,
        load={
            "weight_g": order.weight_grams,
            "volume_cm3": order.volume_cm3,
            "package_count": order.package_count,
            # Passengers ride alongside packages on the same load
            # dimension framework. For non-RIDE orders this is 0
            # so it never affects capacity checks.
            "passenger_count": getattr(order, "passenger_count", 0),
        },
        priority=order.priority,
        unserved_penalty=order.unserved_penalty,
        lateness_penalty_per_min=order.lateness_penalty_per_minute,
        priority_multiplier=order.priority_penalty_multiplier,
        required_tags=order.required_vehicle_tags,
        allowed_vehicle_indices=(
            frozenset(order.allowed_vehicle_indices)
            if order.allowed_vehicle_indices is not None
            else None
        ),
        optional=order.priority != OrderPriority.URGENT,
        is_fragile=order.is_fragile,
        is_temp_sensitive=order.is_temp_sensitive,
        is_charging=getattr(order, "is_charging", False),
    )


def _build_vehicle(v: VehicleData, idx: int) -> Vehicle:
    break_window: tuple[int, int] | None = None
    if v.break_duration_s > 0:
        break_window = (v.break_earliest_s, v.break_latest_s)

    # Phase 2 multi-depot — translate VehicleData's allowed_depot_indices
    # tuple onto the internal Vehicle. `end_depot_index=None` means
    # "end at start," matching the pre-multi-depot path. `()` for the
    # allowed set means "only start_node" downstream.
    end_node = v.end_depot_index if v.end_depot_index is not None else v.depot_index
    allowed_depot_nodes = (
        v.allowed_depot_indices if v.allowed_depot_indices else ()
    )

    return Vehicle(
        id=v.courier_id,
        physical_id=v.physical_courier_id or v.courier_id,
        index=idx,
        trip_index=v.trip_index,
        start_node=v.depot_index,
        end_node=end_node,
        shift_window=(v.start_time_s, v.end_time_s),
        hard_shift_end=v.hard_end_time_s,
        capacity={
            "weight_g": v.capacity_grams,
            "volume_cm3": v.capacity_volume_cm3,
            "package_count": v.max_packages,
            # Passenger capacity. 0 = unconstrained (kernel's
            # `cap > 0` check skips the dimension entirely for
            # vehicles that don't carry passengers).
            "passenger_count": getattr(v, "max_passengers", 0),
        },
        tags=v.vehicle_tags,
        cost=VehicleCost(
            fixed_route=v.fixed_route_cost,
            distance_per_km=v.distance_cost_per_km,
            labor_per_min=v.labor_cost_per_minute,
            fuel_per_km=v.fuel_cost_per_km,
            overtime_per_min=v.overtime_penalty_per_minute + v.overtime_cost_per_minute,
        ),
        profile=v.osrm_profile or "driving",
        max_distance_m=v.max_distance_m,
        full_range_m=getattr(v, "full_range_m", 0),
        max_stops=v.max_stops,
        break_window=break_window,
        break_duration_s=v.break_duration_s,
        allow_fragile=v.allow_fragile,
        allow_temp_sensitive=v.allow_temp_sensitive,
        fuel_type=v.fuel_type,
        fuel_efficiency_km_per_liter=v.fuel_efficiency_km_per_liter,
        co2_g_per_km=v.co2_g_per_km,
        allowed_depot_nodes=allowed_depot_nodes,
    )


def _build_route_from_segment(
    vehicle: Vehicle,
    stops,
    problem: Problem,
    served_request_ids: set[str],
) -> Route | None:
    """Build a single internal Route from a segment of external PICKUP/DELIVERY stops.

    Returns None if the segment is empty.
    """
    from app.algorithms.rich_vrp.feasibility.kernel import evaluate_route  # noqa: PLC0415

    visits: list[Visit] = [
        Visit(
            request_id=None,
            kind=VisitKind.DEPOT_START,
            node=vehicle.start_node,
            arrival_s=vehicle.shift_window[0],
            departure_s=vehicle.shift_window[0],
        ),
    ]
    last_departure = vehicle.shift_window[0]
    for stop in stops:
        if stop.stop_type == "PICKUP":
            kind = VisitKind.PICKUP
        elif stop.stop_type == "DELIVERY":
            kind = VisitKind.DELIVERY
        else:
            continue
        visits.append(
            Visit(
                request_id=stop.order_id or None,
                kind=kind,
                node=stop.location_index,
                arrival_s=stop.arrival_time_s,
                departure_s=stop.departure_time_s,
            )
        )
        last_departure = stop.departure_time_s
        if stop.order_id and kind == VisitKind.DELIVERY:
            served_request_ids.add(stop.order_id)
    if len(visits) == 1:
        return None  # only DEPOT_START — no real stops
    visits.append(
        Visit(
            request_id=None,
            kind=VisitKind.DEPOT_END,
            node=vehicle.end_node,
            arrival_s=last_departure,
            departure_s=last_departure,
        )
    )
    route = Route(vehicle=vehicle, visits=visits)
    evaluate_route(route, problem)
    return route


def import_solution(external: VRPSolution, problem: Problem) -> Solution:
    """Convert an external `VRPSolution` into the solver-internal `Solution`.

    Used to warm-start the rich ALNS engine from an OR-Tools (or any other
    solver's) output. Each external `PlannedRoute` becomes one or more
    internal `Route`s — one per multi-trip segment when the external route
    is a stitched multi-trip route (split at DEPOT_RETURN markers).

    Vehicle matching is by physical_id, which makes this work for both
    single-trip cases (where `physical_id == id`) and multi-trip-stitched
    cases (where the stitched route's courier_id is the physical id and the
    internal problem holds the per-trip virtual vehicles). For multi-trip,
    segments are mapped to virtual vehicles in trip_index order.

    Routes whose physical id doesn't match any vehicle in `problem` are
    dropped. Orders without a Route landing them stay unserved so the lex
    objective reflects what the engine has to recover.
    """
    from app.algorithms.rich_vrp.objective.lexicographic import objective_for_solution  # noqa: PLC0415

    # Group internal vehicles by physical_id (sort by trip_index so the
    # earliest trip gets the first split segment).
    vehicles_by_physical: dict[str, list[Vehicle]] = {}
    for v in problem.vehicles:
        vehicles_by_physical.setdefault(v.physical_id, []).append(v)
    for v_list in vehicles_by_physical.values():
        v_list.sort(key=lambda v: v.trip_index)

    routes: list[Route] = []
    served_request_ids: set[str] = set()
    used_vehicle_ids: set[str] = set()

    for ext_route in external.routes:
        # Match ext_route.courier_id against either a virtual courier_id (e.g.
        # "X__trip0", happens when skip_multi_trip=True so the OR-Tools output
        # wasn't stitched) or against a physical courier_id (e.g. "X", the
        # post-stitch case).
        physical_match = vehicles_by_physical.get(ext_route.courier_id)
        if physical_match is None:
            # Not a physical id — try treating courier_id as a virtual id.
            virtual_match = next(
                (v for v in problem.vehicles if v.id == ext_route.courier_id),
                None,
            )
            if virtual_match is None:
                continue
            physical_match = [virtual_match]

        # Split stitched stops into trip segments at DEPOT_RETURN/DEPOT_RELOAD
        # markers. For non-multi-trip output, the route has zero markers and
        # this just yields one segment with all the PICKUP/DELIVERY stops.
        segments: list[list] = [[]]
        for stop in ext_route.stops:
            if stop.stop_type in ("DEPOT_RETURN", "DEPOT_RELOAD"):
                if segments[-1]:  # close the current segment
                    segments.append([])
                continue
            segments[-1].append(stop)
        if not segments[-1]:
            segments.pop()

        # Map segments to virtual vehicles by trip_index, in order. Drop excess
        # segments that exceed the available virtual vehicles for this physical
        # courier; their orders will fall into `unserved` and the engine
        # decides what to do with them.
        for segment, vehicle in zip(segments, physical_match):
            if vehicle.id in used_vehicle_ids:
                continue
            route = _build_route_from_segment(
                vehicle, segment, problem, served_request_ids
            )
            if route is not None:
                routes.append(route)
                used_vehicle_ids.add(vehicle.id)

    all_request_ids = {r.id for r in problem.requests}
    unserved = all_request_ids - served_request_ids
    objective = objective_for_solution(routes, unserved, problem)
    return Solution(routes=routes, unserved=unserved, objective=objective)


def export_solution(internal: Solution, vrp_input: VRPInput) -> VRPSolution:
    """Convert an internal Solution back to the VRPSolution shape callers expect."""
    planned_routes: list[PlannedRoute] = []
    total_distance_m = 0
    total_duration_s = 0

    for route in internal.routes:
        if not route.visits:
            continue
        planned = _export_route(route, vrp_input)
        if planned is not None:
            planned_routes.append(planned)
            total_distance_m += planned.total_distance_m
            total_duration_s += planned.total_duration_s

    return VRPSolution(
        routes=planned_routes,
        unassigned_orders=sorted(internal.unserved),
        total_distance_m=total_distance_m,
        total_duration_s=total_duration_s,
        solver_status=SolverStatus.FEASIBLE if not internal.unserved else SolverStatus.FEASIBLE,
        cost_breakdown=_sum_cost_breakdown(planned_routes, internal.unserved, vrp_input),
    )


def _export_route(route: Route, vrp_input: VRPInput) -> PlannedRoute | None:
    stops: list[PlannedStop] = []
    route_start_time_s = 0
    route_end_time_s = 0

    for visit in route.visits:
        if visit.kind in (VisitKind.DEPOT_START, VisitKind.DEPOT_END):
            continue
        lat, lon = vrp_input.locations[visit.node]
        stop_type = _visit_kind_to_stop_type(visit.kind)
        stop = PlannedStop(
            order_id=visit.request_id or "",
            stop_type=stop_type,
            location_index=visit.node,
            lat=lat,
            lon=lon,
            arrival_time_s=visit.arrival_s,
            departure_time_s=visit.departure_s,
        )
        stops.append(stop)

    if not stops:
        return None

    route_start_time_s = stops[0].arrival_time_s
    route_end_time_s = stops[-1].departure_time_s

    cost_bd = _internal_cost_to_economic(route.cost) if route.cost else None

    return PlannedRoute(
        courier_id=route.vehicle.id,
        stops=stops,
        total_distance_m=route.total_distance_m,
        total_duration_s=route.total_duration_s,
        route_start_time_s=route_start_time_s,
        route_end_time_s=route_end_time_s,
        cost_breakdown=cost_bd,
    )


def _visit_kind_to_stop_type(kind: VisitKind) -> str:
    if kind == VisitKind.PICKUP:
        return "PICKUP"
    if kind == VisitKind.DELIVERY:
        return "DELIVERY"
    if kind == VisitKind.RELOAD:
        return "DEPOT_RELOAD"
    return "DEPOT_RETURN"


def _internal_cost_to_economic(cost: CostBreakdown) -> EconomicCostBreakdown:
    return EconomicCostBreakdown(
        total_generalized_cost=cost.total_generalized_cost,
        distance_cost=cost.distance_cost,
        labor_cost=cost.labor_cost,
        fuel_cost=cost.fuel_cost,
        fixed_route_cost=cost.fixed_route_cost,
        lateness_penalty=cost.lateness_penalty,
        overtime_penalty=cost.overtime_penalty,
        late_minutes=cost.late_minutes,
        overtime_minutes=cost.overtime_minutes,
        active_vehicle_count=1,
        estimated_fuel_liters=cost.estimated_fuel_liters,
        estimated_co2_g=cost.estimated_co2_g,
    )


def _sum_cost_breakdown(
    planned_routes: list[PlannedRoute],
    unserved: set[str],
    vrp_input: VRPInput,
) -> EconomicCostBreakdown | None:
    if not planned_routes:
        return None
    orders_by_id = {o.order_id: o for o in vrp_input.orders}
    total = EconomicCostBreakdown(active_vehicle_count=len(planned_routes))
    for route in planned_routes:
        if route.cost_breakdown is None:
            continue
        cb = route.cost_breakdown
        total = EconomicCostBreakdown(
            total_generalized_cost=total.total_generalized_cost + cb.total_generalized_cost,
            distance_cost=total.distance_cost + cb.distance_cost,
            labor_cost=total.labor_cost + cb.labor_cost,
            fuel_cost=total.fuel_cost + cb.fuel_cost,
            fixed_route_cost=total.fixed_route_cost + cb.fixed_route_cost,
            lateness_penalty=total.lateness_penalty + cb.lateness_penalty,
            overtime_penalty=total.overtime_penalty + cb.overtime_penalty,
            unserved_penalty=sum(
                orders_by_id[oid].unserved_penalty
                for oid in unserved
                if oid in orders_by_id
            ),
            late_minutes=total.late_minutes + cb.late_minutes,
            overtime_minutes=total.overtime_minutes + cb.overtime_minutes,
            active_vehicle_count=len(planned_routes),
            estimated_fuel_liters=(
                (total.estimated_fuel_liters or 0.0) + cb.estimated_fuel_liters
                if cb.estimated_fuel_liters is not None
                else total.estimated_fuel_liters
            ),
            estimated_co2_g=(
                (total.estimated_co2_g or 0.0) + cb.estimated_co2_g
                if cb.estimated_co2_g is not None
                else total.estimated_co2_g
            ),
        )
    return total
