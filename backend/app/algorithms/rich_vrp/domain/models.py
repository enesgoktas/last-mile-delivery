"""Solver-internal domain model for the rich VRP engine.

These types are used exclusively inside the rich_vrp package.  The boundary
to the rest of the codebase is VRPInput → Problem (via builders.build_problem)
and Solution → VRPSolution (via builders.export_solution).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models.enums import OrderPriority
from app.utils.compat import StrEnum

if TYPE_CHECKING:
    from app.algorithms.rich_vrp.objective.lexicographic import LexicographicObjective
    from app.models.enums import FuelType


class VisitKind(StrEnum):
    PICKUP = "PICKUP"
    DELIVERY = "DELIVERY"
    RELOAD = "RELOAD"
    BREAK = "BREAK"
    DEPOT_START = "DEPOT_START"
    DEPOT_END = "DEPOT_END"


@dataclass(slots=True)
class VehicleCost:
    fixed_route: int = 0
    distance_per_km: int = 0
    labor_per_min: int = 0
    fuel_per_km: int = 0
    overtime_per_min: int = 0


@dataclass(slots=True)
class CostBreakdown:
    """Mutable cost summary for a route, mirroring EconomicCostBreakdown."""
    total_generalized_cost: int = 0
    distance_cost: int = 0
    labor_cost: int = 0
    fuel_cost: int = 0
    fixed_route_cost: int = 0
    lateness_penalty: int = 0
    overtime_penalty: int = 0
    late_minutes: int = 0
    overtime_minutes: int = 0
    estimated_fuel_liters: float | None = None
    estimated_co2_g: float | None = None


@dataclass(slots=True)
class Request:
    """Solver-internal representation of one pickup-delivery request."""
    id: str
    pickup_node: int
    delivery_node: int
    pickup_window: tuple[int, int]
    delivery_window: tuple[int, int]
    hard_delivery_deadline: int | None
    service_time_pickup_s: int
    service_time_delivery_s: int
    load: dict[str, int]
    priority: OrderPriority
    unserved_penalty: int
    lateness_penalty_per_min: int
    priority_multiplier: float
    required_tags: frozenset[str]
    allowed_vehicle_indices: frozenset[int] | None
    optional: bool
    is_fragile: bool = False
    is_temp_sensitive: bool = False
    # Phase 2.3: synthetic "go to charger and charge" request. The
    # feasibility kernel resets the vehicle's remaining battery range
    # after the pickup visit of a charging request.
    is_charging: bool = False


@dataclass(slots=True)
class Vehicle:
    """Solver-internal representation of one vehicle (may be virtual for multi-trip)."""
    id: str
    physical_id: str
    index: int
    trip_index: int
    start_node: int
    end_node: int
    shift_window: tuple[int, int]
    hard_shift_end: int | None
    capacity: dict[str, int]
    tags: frozenset[str]
    cost: VehicleCost
    profile: str
    # `max_distance_m` is the *initial* distance budget at route start
    # (= current_battery_pct × full_range_m for EVs). When the route
    # visits a charging request, the kernel refills the remaining
    # budget back to `full_range_m`. Non-EV vehicles set
    # `full_range_m = 0` and the per-segment reset machinery becomes
    # a no-op (the kernel falls back to the route-wide check).
    max_distance_m: int
    max_stops: int
    break_window: tuple[int, int] | None
    break_duration_s: int
    allow_fragile: bool
    allow_temp_sensitive: bool
    fuel_type: FuelType | None = None
    fuel_efficiency_km_per_liter: float | None = None
    co2_g_per_km: float | None = None
    # Phase 2.3 — post-charge usable distance for EV auto-charging.
    # When 0 (the default), the kernel uses the route-wide max_distance_m
    # cap from Phase 1.5; when > 0, the per-segment battery check kicks
    # in and the budget refills on charging-request pickups. Defaults to
    # 0 so every existing test fixture / direct constructor keeps working.
    full_range_m: int = 0
    # Phase 2 multi-depot — set of location-indices this vehicle is
    # allowed to visit for mid-route reloads. Empty tuple is the sentinel
    # for "only `start_node`," matching the previous single-depot path
    # exactly. Populated only when the source `Courier.additional_depots`
    # is non-empty AND `features.multi_depot_vehicles_enabled`.
    allowed_depot_nodes: tuple[int, ...] = ()


@dataclass(slots=True)
class Visit:
    """A single stop on a route — mutable so the evaluator can update it."""
    request_id: str | None
    kind: VisitKind
    node: int
    arrival_s: int
    departure_s: int
    cumulative_load: dict[str, int] = field(default_factory=dict)


@dataclass
class Route:
    """One vehicle's ordered sequence of visits."""
    vehicle: Vehicle
    visits: list[Visit]
    total_distance_m: int = 0
    total_duration_s: int = 0
    cost: CostBreakdown = field(default_factory=CostBreakdown)
    feasible: bool = True


@dataclass
class Solution:
    """Top-level container for a complete assignment."""
    routes: list[Route]
    unserved: set[str]
    objective: LexicographicObjective | None = None


class MatrixView:
    """Read-only view of the duration/distance matrices from VRPInput."""

    __slots__ = (
        "_duration",
        "_distance",
        "_duration_by_profile",
        "_distance_by_profile",
        "_np_dur",
        "_np_dist",
    )

    def __init__(
        self,
        duration_matrix: list[list[int]],
        distance_matrix: list[list[int]],
        duration_matrices: dict[str, list[list[int]]] | None = None,
        distance_matrices: dict[str, list[list[int]]] | None = None,
    ) -> None:
        self._duration = duration_matrix
        self._distance = distance_matrix
        self._duration_by_profile: dict[str, list[list[int]]] = duration_matrices or {}
        self._distance_by_profile: dict[str, list[list[int]]] = distance_matrices or {}
        self._np_dur = None
        self._np_dist = None

    def duration(self, i: int, j: int, profile: str = "driving") -> int:
        matrix = self._duration_by_profile.get(profile, self._duration)
        return matrix[i][j]

    def distance(self, i: int, j: int, profile: str = "driving") -> int:
        matrix = self._distance_by_profile.get(profile, self._distance)
        return matrix[i][j]

    @property
    def np_duration(self):
        """Return the default duration matrix as a contiguous int64 numpy array.

        Built lazily and cached.  Used by the Cython hot-path evaluator.
        """
        if self._np_dur is None:
            import numpy as np
            self._np_dur = np.array(self._duration, dtype=np.int64)
        return self._np_dur

    @property
    def np_distance(self):
        """Return the default distance matrix as a contiguous int64 numpy array."""
        if self._np_dist is None:
            import numpy as np
            self._np_dist = np.array(self._distance, dtype=np.int64)
        return self._np_dist


@dataclass
class Problem:
    """Complete solver-internal representation of one VRP instance."""
    requests: list[Request]
    vehicles: list[Vehicle]
    matrix: MatrixView
    locations: list[tuple[float, float]]
    service_date_anchor: int = 0
    spread_fleet: bool = True

    def __post_init__(self) -> None:
        self._request_by_id: dict[str, Request] = {r.id: r for r in self.requests}
        self._vehicle_by_id: dict[str, Vehicle] = {v.id: v for v in self.vehicles}

    def request(self, request_id: str) -> Request:
        return self._request_by_id[request_id]

    def vehicle(self, vehicle_id: str) -> Vehicle:
        return self._vehicle_by_id[vehicle_id]
