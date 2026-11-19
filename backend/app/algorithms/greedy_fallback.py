"""
Greedy fallback solver — used when OR-Tools returns no solution.

Assigns orders to vehicles using nearest-neighbor insertion.
Not optimal, but guarantees some assignments when the full solver fails.
"""
from __future__ import annotations

import logging

from app.algorithms.vrp_solver import (
    OrderData,
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VehicleData,
    VRPInput,
    VRPSolution,
)
from app.models.enums import VehicleType as _VehicleType

logger = logging.getLogger(__name__)


def greedy_assign(vrp_input: VRPInput) -> VRPSolution:
    """Assign orders to vehicles using a greedy nearest-insertion approach.

    For each vehicle, repeatedly pick the nearest unassigned order whose
    constraints are satisfied (capacity, time windows, fragile/temp) and
    append pickup → delivery to the route.

    This is O(V * O²) in the worst case but runs in milliseconds for
    typical problem sizes (< 1000 orders).
    """
    orders = vrp_input.orders
    vehicles = vrp_input.vehicles
    locations = vrp_input.locations
    duration_matrix = vrp_input.duration_matrix
    distance_matrix = vrp_input.distance_matrix

    if not orders or not vehicles:
        return VRPSolution(
            routes=[],
            unassigned_orders=[o.order_id for o in orders],
            total_distance_m=0,
            total_duration_s=0,
            solver_status=SolverStatus.NOT_FOUND,
        )

    assigned: set[str] = set()
    planned_routes: list[PlannedRoute] = []
    grand_distance = 0
    grand_duration = 0

    # Sort vehicles by capacity descending — bigger vehicles first
    sorted_vehicles = sorted(vehicles, key=lambda v: v.capacity_grams, reverse=True)

    for vehicle in sorted_vehicles:
        route_stops: list[PlannedStop] = []
        current_load = 0
        current_time = vehicle.start_time_s
        current_loc = vehicle.depot_index
        route_distance = 0
        # Phase 5 multi-depot — the return leg targets `end_depot_index`
        # when the courier has a distinct shift-end depot configured.
        return_depot = (
            vehicle.end_depot_index
            if vehicle.end_depot_index is not None
            else vehicle.depot_index
        )

        # Keep inserting orders until no more fit
        while True:
            best_order: OrderData | None = None
            best_cost = float("inf")

            for order in orders:
                if order.order_id in assigned:
                    continue

                # Check basic eligibility
                if not _is_eligible(order, vehicle, current_load):
                    continue

                # Cost = travel time to pickup + pickup-to-delivery
                pickup_travel = duration_matrix[current_loc][order.pickup_index]
                pickup_to_delivery = duration_matrix[order.pickup_index][order.delivery_index]

                # Check time feasibility
                arrival_pickup = current_time + pickup_travel
                if arrival_pickup < (order.pickup_earliest_time_s or 0):
                    arrival_pickup = order.pickup_earliest_time_s or 0
                pickup_latest = order.pickup_latest_time_s if order.pickup_latest_time_s is not None else order.latest_time_s
                if arrival_pickup > pickup_latest:
                    continue

                departure_pickup = arrival_pickup + order.service_time_s
                arrival_delivery = departure_pickup + pickup_to_delivery
                if arrival_delivery < order.earliest_time_s:
                    arrival_delivery = order.earliest_time_s
                if arrival_delivery > order.latest_time_s:
                    continue

                departure_delivery = arrival_delivery + order.service_time_s

                # Check return to depot within shift. Multi-depot
                # vehicles return to `end_depot_index` instead of home.
                return_travel = duration_matrix[order.delivery_index][return_depot]
                if departure_delivery + return_travel > vehicle.end_time_s:
                    continue

                cost = pickup_travel + pickup_to_delivery
                if cost < best_cost:
                    best_cost = cost
                    best_order = order

            if best_order is None:
                break

            # Assign the order
            order = best_order
            pickup_travel = duration_matrix[current_loc][order.pickup_index]
            pickup_to_delivery = duration_matrix[order.pickup_index][order.delivery_index]

            arrival_pickup = current_time + pickup_travel
            if arrival_pickup < (order.pickup_earliest_time_s or 0):
                arrival_pickup = order.pickup_earliest_time_s or 0
            departure_pickup = arrival_pickup + order.service_time_s

            arrival_delivery = departure_pickup + pickup_to_delivery
            if arrival_delivery < order.earliest_time_s:
                arrival_delivery = order.earliest_time_s
            departure_delivery = arrival_delivery + order.service_time_s

            pickup_lat, pickup_lon = locations[order.pickup_index]
            delivery_lat, delivery_lon = locations[order.delivery_index]

            route_stops.append(PlannedStop(
                order_id=order.order_id,
                stop_type="PICKUP",
                location_index=order.pickup_index,
                lat=pickup_lat,
                lon=pickup_lon,
                arrival_time_s=arrival_pickup,
                departure_time_s=departure_pickup,
            ))
            route_stops.append(PlannedStop(
                order_id=order.order_id,
                stop_type="DELIVERY",
                location_index=order.delivery_index,
                lat=delivery_lat,
                lon=delivery_lon,
                arrival_time_s=arrival_delivery,
                departure_time_s=departure_delivery,
            ))

            route_distance += (
                distance_matrix[current_loc][order.pickup_index]
                + distance_matrix[order.pickup_index][order.delivery_index]
            )
            current_load += order.weight_grams
            current_time = departure_delivery
            current_loc = order.delivery_index
            assigned.add(order.order_id)

            # Check max stops
            if vehicle.max_stops > 0 and len(route_stops) >= vehicle.max_stops:
                break

            # Check max distance
            if vehicle.max_distance_m > 0 and route_distance >= vehicle.max_distance_m:
                break

        if route_stops:
            route_duration = route_stops[-1].departure_time_s - route_stops[0].arrival_time_s
            planned_routes.append(PlannedRoute(
                courier_id=vehicle.courier_id,
                stops=route_stops,
                total_distance_m=route_distance,
                total_duration_s=max(route_duration, 0),
            ))
            grand_distance += route_distance
            grand_duration += max(route_duration, 0)

    unassigned = [o.order_id for o in orders if o.order_id not in assigned]

    logger.info(
        "Greedy fallback: assigned %d/%d orders across %d routes "
        "(total_distance=%dm, total_duration=%ds)",
        len(assigned), len(orders), len(planned_routes),
        grand_distance, grand_duration,
    )

    return VRPSolution(
        routes=planned_routes,
        unassigned_orders=unassigned,
        total_distance_m=grand_distance,
        total_duration_s=grand_duration,
        solver_status=SolverStatus.GREEDY_FALLBACK,
    )


def _is_eligible(order: OrderData, vehicle: VehicleData, current_load: int) -> bool:
    """Check if an order can be assigned to a vehicle."""
    if current_load + order.weight_grams > vehicle.capacity_grams:
        return False
    if order.is_fragile and not vehicle.allow_fragile:
        return False
    if order.is_temp_sensitive and not vehicle.allow_temp_sensitive:
        return False
    is_fragile_motorcycle_order = (
        order.is_fragile and vehicle.vehicle_type == _VehicleType.MOTORCYCLE
    )
    return not is_fragile_motorcycle_order
