"""
Constraint Diagnostics — explains why orders couldn't be assigned.

For each unassigned order, tests it against every vehicle to identify
which constraints block assignment. Reports the blocking reasons and
suggests which constraints to relax.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.algorithms.utils import fmt_time_short as _fmt_time
from app.algorithms.vrp_solver import OrderData, VehicleData, VRPInput
from app.models.enums import VehicleType

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BlockingReason:
    """A single reason why an order can't be assigned to a vehicle."""
    constraint: str
    vehicle_courier_id: str
    message: str


@dataclass
class OrderDiagnostic:
    """Diagnostic for a single unassigned order."""
    order_id: str
    blocking_reasons: list[BlockingReason] = field(default_factory=list)
    suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "blocking_reasons": [
                {
                    "constraint": r.constraint,
                    "vehicle_courier_id": r.vehicle_courier_id[:8],
                    "message": r.message,
                }
                for r in self.blocking_reasons
            ],
            "suggestion": self.suggestion,
        }


def diagnose_unassigned(
    vrp_input: VRPInput,
    unassigned_order_ids: list[str],
) -> list[OrderDiagnostic]:
    """Diagnose why each unassigned order couldn't be placed.

    For each unassigned order, check it against every vehicle and record
    which constraints block assignment. Then suggest the most impactful
    relaxation.
    """
    if not unassigned_order_ids:
        return []

    order_by_id = {o.order_id: o for o in vrp_input.orders}
    vehicles = vrp_input.vehicles
    duration_matrix = vrp_input.duration_matrix

    diagnostics: list[OrderDiagnostic] = []

    for order_id in unassigned_order_ids:
        order = order_by_id.get(order_id)
        if not order:
            diagnostics.append(OrderDiagnostic(
                order_id=order_id,
                suggestion="Order not found in solver input",
            ))
            continue

        all_reasons: list[BlockingReason] = []
        constraint_counts: dict[str, int] = {}

        for vehicle_index, vehicle in enumerate(vehicles):
            reasons = _check_order_vs_vehicle(order, vehicle, duration_matrix, vehicle_index)
            all_reasons.extend(reasons)
            for r in reasons:
                constraint_counts[r.constraint] = constraint_counts.get(r.constraint, 0) + 1

        # Find the most common blocking constraint
        if constraint_counts:
            most_common = max(constraint_counts, key=constraint_counts.get)
            total_vehicles = len(vehicles)
            count = constraint_counts[most_common]
            suggestion = _suggest_relaxation(most_common, order, count, total_vehicles)
        else:
            suggestion = (
                "No specific constraint identified — may be blocked by "
                "inter-order interactions or solver timeout"
            )

        # Deduplicate reasons — keep unique constraint types
        seen_constraints: set[str] = set()
        unique_reasons: list[BlockingReason] = []
        for r in all_reasons:
            key = f"{r.constraint}:{r.vehicle_courier_id}"
            if key not in seen_constraints:
                seen_constraints.add(key)
                unique_reasons.append(r)

        diagnostics.append(OrderDiagnostic(
            order_id=order_id,
            blocking_reasons=unique_reasons,
            suggestion=suggestion,
        ))

    logger.info(
        "Constraint diagnostics: analyzed %d unassigned orders",
        len(diagnostics),
    )

    return diagnostics


def _check_order_vs_vehicle(
    order: OrderData,
    vehicle: VehicleData,
    duration_matrix: list[list[int]],
    vehicle_index: int,
) -> list[BlockingReason]:
    """Check all constraints between an order and a vehicle."""
    reasons: list[BlockingReason] = []
    courier_id = vehicle.courier_id

    if order.allowed_vehicle_indices is not None and vehicle_index not in order.allowed_vehicle_indices:
        reasons.append(BlockingReason(
            constraint="vehicle_not_eligible",
            vehicle_courier_id=courier_id,
            message="Vehicle is not in the allowed set for this order",
        ))

    # Capacity
    if order.weight_grams > vehicle.capacity_grams:
        reasons.append(BlockingReason(
            constraint="capacity_weight",
            vehicle_courier_id=courier_id,
            message=(
                f"Order weighs {order.weight_grams}g, vehicle capacity "
                f"{vehicle.capacity_grams}g"
            ),
        ))

    # Volume
    if vehicle.capacity_volume_cm3 > 0 and order.volume_cm3 > vehicle.capacity_volume_cm3:
        reasons.append(BlockingReason(
            constraint="capacity_volume",
            vehicle_courier_id=courier_id,
            message=(
                f"Order volume {order.volume_cm3}cm³ exceeds vehicle "
                f"capacity {vehicle.capacity_volume_cm3}cm³"
            ),
        ))

    # Fragile
    if order.is_fragile and (
        not vehicle.allow_fragile or vehicle.vehicle_type == VehicleType.MOTORCYCLE
    ):
        reasons.append(BlockingReason(
            constraint="fragile_restriction",
            vehicle_courier_id=courier_id,
            message="Fragile order cannot be assigned to this vehicle",
        ))

    # Temperature-sensitive
    if order.is_temp_sensitive and not vehicle.allow_temp_sensitive:
        reasons.append(BlockingReason(
            constraint="temp_sensitive_restriction",
            vehicle_courier_id=courier_id,
            message="Temperature-sensitive order cannot be assigned to this vehicle",
        ))

    # Time window feasibility — for multi-depot vehicles, use the
    # closest allowed depot as the optimistic "best case." Single-depot
    # vehicles have allowed_depot_indices=() and fall back to depot_index.
    pickup_to_delivery = duration_matrix[order.pickup_index][order.delivery_index]
    allowed_starts = vehicle.allowed_depot_indices or (vehicle.depot_index,)
    depot_to_pickup = min(
        duration_matrix[d][order.pickup_index] for d in allowed_starts
    )

    earliest_pickup_arrival = vehicle.start_time_s + depot_to_pickup
    pickup_earliest = order.pickup_earliest_time_s if order.pickup_earliest_time_s is not None else 0
    pickup_latest = order.pickup_latest_time_s if order.pickup_latest_time_s is not None else order.latest_time_s

    if earliest_pickup_arrival > pickup_latest:
        reasons.append(BlockingReason(
            constraint="time_window_pickup",
            vehicle_courier_id=courier_id,
            message=(
                f"Vehicle can't reach pickup before window closes "
                f"(earliest arrival {_fmt_time(earliest_pickup_arrival)}, "
                f"window closes {_fmt_time(pickup_latest)})"
            ),
        ))

    earliest_delivery = max(earliest_pickup_arrival, pickup_earliest) + order.service_time_s + pickup_to_delivery
    # Account for waiting until delivery window opens
    actual_delivery_arrival = max(earliest_delivery, order.earliest_time_s)
    if earliest_delivery > order.latest_time_s:
        reasons.append(BlockingReason(
            constraint="time_window_delivery",
            vehicle_courier_id=courier_id,
            message=(
                f"Vehicle can't deliver before window closes "
                f"(earliest delivery {_fmt_time(earliest_delivery)}, "
                f"deadline {_fmt_time(order.latest_time_s)})"
            ),
        ))

    # Shift hours — check delivery + return trip (most restrictive wins).
    # For multi-depot vehicles, the return target is `end_depot_index`
    # when set, otherwise the closest allowed depot to the delivery.
    if vehicle.end_depot_index is not None:
        return_targets: tuple[int, ...] = (vehicle.end_depot_index,)
    else:
        return_targets = vehicle.allowed_depot_indices or (vehicle.depot_index,)
    return_to_depot = min(
        duration_matrix[order.delivery_index][d] for d in return_targets
    )
    earliest_return = actual_delivery_arrival + order.service_time_s + return_to_depot
    if earliest_return > vehicle.end_time_s:
        reasons.append(BlockingReason(
            constraint="shift_hours",
            vehicle_courier_id=courier_id,
            message=(
                f"Vehicle shift ends at {_fmt_time(vehicle.end_time_s)}, "
                f"earliest return to depot at {_fmt_time(earliest_return)}"
            ),
        ))
    elif actual_delivery_arrival + order.service_time_s > vehicle.end_time_s:
        reasons.append(BlockingReason(
            constraint="shift_hours",
            vehicle_courier_id=courier_id,
            message=(
                f"Delivery + service ends at "
                f"{_fmt_time(actual_delivery_arrival + order.service_time_s)}, "
                f"vehicle shift ends at {_fmt_time(vehicle.end_time_s)}"
            ),
        ))

    return reasons


def _suggest_relaxation(
    constraint: str,
    order: OrderData,
    blocked_count: int,
    total_vehicles: int,
) -> str:
    """Generate a human-readable relaxation suggestion."""
    pct = round(blocked_count / total_vehicles * 100) if total_vehicles > 0 else 0
    prefix = f"Blocked by '{constraint}' on {pct}% of vehicles."

    suggestions = {
        "capacity_weight": f" Consider increasing vehicle weight limits or splitting the order ({order.weight_grams}g).",
        "capacity_volume": f" Consider increasing vehicle volume limits or splitting the order ({order.volume_cm3}cm³).",
        "fragile_restriction": " Assign a non-motorcycle vehicle or enable fragile handling.",
        "temp_sensitive_restriction": " Enable temperature-sensitive transport on more vehicles.",
        "time_window_pickup": " Widen the pickup time window or assign closer vehicles.",
        "time_window_delivery": " Widen the delivery time window or assign closer vehicles.",
        "shift_hours": " Extend vehicle shift hours or assign to a later shift.",
        "vehicle_not_eligible": " Check vehicle eligibility constraints for this order.",
    }

    return prefix + suggestions.get(constraint, " Review constraint configuration.")
