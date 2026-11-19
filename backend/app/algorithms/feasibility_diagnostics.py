"""Static feasibility diagnostics for batch VRP inputs.

These checks separate orders that cannot be served by any physical courier
from orders the route optimizer merely failed to place inside its search
budget.  That distinction is the first objective in the lexicographic batch
solver.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.algorithms.vrp_solver import OrderData, VehicleData, VRPInput, _duration_matrix_for_vehicle
from app.models.enums import VehicleType


@dataclass(frozen=True, slots=True)
class OrderFeasibility:
    order_id: str
    globally_feasible: bool
    eligible_vehicle_ids: list[str]
    blocking_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "globally_feasible": self.globally_feasible,
            "eligible_vehicle_ids": list(self.eligible_vehicle_ids),
            "blocking_reasons": list(self.blocking_reasons),
        }


def diagnose_global_feasibility(vrp_input: VRPInput) -> list[OrderFeasibility]:
    """Return per-order static and solo-route feasibility diagnostics."""
    results: list[OrderFeasibility] = []
    for order_idx, order in enumerate(vrp_input.orders):
        eligible: list[str] = []
        vehicle_reason_sets: list[set[str]] = []
        allowed_indices = (
            set(order.allowed_vehicle_indices)
            if order.allowed_vehicle_indices is not None
            else None
        )

        for vehicle_idx, vehicle in enumerate(vrp_input.vehicles):
            reasons = _vehicle_blocking_reasons(
                vrp_input,
                order,
                vehicle,
                vehicle_idx=vehicle_idx,
                order_idx=order_idx,
                allowed_indices=allowed_indices,
            )
            if reasons:
                vehicle_reason_sets.append(reasons)
            else:
                eligible.append(vehicle.courier_id)

        blocking_reasons = sorted(set().union(*vehicle_reason_sets)) if vehicle_reason_sets else []
        if not eligible and not blocking_reasons:
            blocking_reasons = ["no_vehicles"]
        results.append(
            OrderFeasibility(
                order_id=order.order_id,
                globally_feasible=bool(eligible),
                eligible_vehicle_ids=eligible,
                blocking_reasons=blocking_reasons,
            )
        )

    return results


def summarize_global_feasibility(diagnostics: list[OrderFeasibility]) -> dict:
    by_reason: dict[str, int] = {}
    for diag in diagnostics:
        if diag.globally_feasible:
            continue
        for reason in diag.blocking_reasons or ["unknown"]:
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "total_orders": len(diagnostics),
        "globally_feasible": sum(1 for d in diagnostics if d.globally_feasible),
        "globally_infeasible": sum(1 for d in diagnostics if not d.globally_feasible),
        "by_blocking_reason": by_reason,
    }


def summarize_cluster_feasibility(
    vrp_input: VRPInput,
    diagnostics: list[OrderFeasibility],
    *,
    clusters: list[dict] | None = None,
) -> list[dict]:
    """Build observability-only cluster summaries.

    The lexicographic path does not let clusters permanently strand orders; this
    output exists so operators can see when a proposed local neighborhood has a
    poor vehicle mix.
    """
    diag_by_order = {diag.order_id: diag for diag in diagnostics}
    if not clusters:
        return [
            {
                "cluster_id": 0,
                "orders": len(vrp_input.orders),
                "assigned_vehicle_count": len(vrp_input.vehicles),
                "globally_feasible_but_cluster_ineligible": 0,
                "vehicle_mix": _vehicle_mix(vrp_input.vehicles),
            }
        ]

    summaries: list[dict] = []
    for idx, cluster in enumerate(clusters):
        order_indices = list(cluster.get("order_indices", []))
        vehicle_indices = list(cluster.get("vehicle_indices", []))
        cluster_vehicles = [
            vrp_input.vehicles[i]
            for i in vehicle_indices
            if 0 <= i < len(vrp_input.vehicles)
        ]
        cluster_vehicle_ids = {vehicle.courier_id for vehicle in cluster_vehicles}
        stranded = 0
        for order_i in order_indices:
            if not (0 <= order_i < len(vrp_input.orders)):
                continue
            diag = diag_by_order.get(vrp_input.orders[order_i].order_id)
            if diag and diag.globally_feasible and not (
                set(diag.eligible_vehicle_ids) & cluster_vehicle_ids
            ):
                stranded += 1
        summaries.append(
            {
                "cluster_id": int(cluster.get("cluster_id", idx)),
                "orders": len(order_indices),
                "assigned_vehicle_count": len(cluster_vehicles),
                "globally_feasible_but_cluster_ineligible": stranded,
                "vehicle_mix": _vehicle_mix(cluster_vehicles),
            }
        )
    return summaries


def _vehicle_blocking_reasons(
    vrp_input: VRPInput,
    order: OrderData,
    vehicle: VehicleData,
    *,
    vehicle_idx: int,
    order_idx: int,
    allowed_indices: set[int] | None,
) -> set[str]:
    reasons: set[str] = set()
    if allowed_indices is not None and vehicle_idx not in allowed_indices:
        reasons.add("vehicle_not_allowed")
    if order.required_vehicle_tags and not order.required_vehicle_tags.issubset(vehicle.vehicle_tags):
        reasons.add("required_vehicle_tags")
    if order.is_fragile and vehicle.vehicle_type == VehicleType.MOTORCYCLE:
        reasons.add("fragile_motorcycle_restriction")
    if order.is_fragile and not vehicle.allow_fragile:
        reasons.add("fragile_not_allowed")
    if order.is_temp_sensitive and not vehicle.allow_temp_sensitive:
        reasons.add("temp_sensitive_not_allowed")
    if vehicle.capacity_grams > 0 and order.weight_grams > vehicle.capacity_grams:
        reasons.add("weight_capacity")
    if vehicle.capacity_volume_cm3 > 0 and order.volume_cm3 > vehicle.capacity_volume_cm3:
        reasons.add("volume_capacity")
    if vehicle.max_packages > 0 and order.package_count > vehicle.max_packages:
        reasons.add("package_capacity")
    if not _solo_timing_feasible(vrp_input, order, vehicle):
        reasons.add("solo_timing")
    return reasons


def _solo_timing_feasible(
    vrp_input: VRPInput,
    order: OrderData,
    vehicle: VehicleData,
) -> bool:
    matrix = _duration_matrix_for_vehicle(vrp_input, vehicle)
    # Multi-depot vehicles can pick from any allowed depot for the
    # outbound leg; single-depot vehicles have allowed=() and fall back
    # to depot_index. Empty matrix rows would propagate IndexError to the
    # outer except, same as today.
    allowed_starts = vehicle.allowed_depot_indices or (vehicle.depot_index,)
    return_targets = (
        (vehicle.end_depot_index,)
        if vehicle.end_depot_index is not None
        else allowed_starts
    )
    try:
        current = max(vehicle.start_time_s, 0)
        current += min(matrix[d][order.pickup_index] for d in allowed_starts)
        pickup_earliest = order.pickup_earliest_time_s or 0
        pickup_latest = order.pickup_latest_time_s
        if not order.pickup_is_depot and current < pickup_earliest:
            current = pickup_earliest
        if pickup_latest is not None and not order.pickup_is_depot and current > pickup_latest:
            return False
        if not (order.pickup_is_depot or order.pickup_index in allowed_starts):
            current += order.service_time_s
        current += matrix[order.pickup_index][order.delivery_index]
        if current < order.earliest_time_s:
            current = order.earliest_time_s
        hard_latest = order.hard_latest_time_s or order.latest_time_s or 24 * 3600
        if current > hard_latest:
            return False
        current += order.service_time_s
        current += min(matrix[order.delivery_index][d] for d in return_targets)
    except (IndexError, TypeError):
        return False

    hard_shift_end = vehicle.hard_end_time_s or vehicle.end_time_s
    if hard_shift_end and current > hard_shift_end:
        return False
    return True


def _vehicle_mix(vehicles: list[VehicleData]) -> dict[str, int]:
    mix: dict[str, int] = {}
    for vehicle in vehicles:
        key = vehicle.vehicle_type.value
        mix[key] = mix.get(key, 0) + 1
    return mix
