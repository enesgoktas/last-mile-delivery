"""
OR-Tools CVRPTW Solver — Capacitated Vehicle Routing with Time Windows.

Wraps Google OR-Tools' routing library to solve the daily batch
optimization problem. Handles:
  - Vehicle capacity constraints (weight in integer grams)
  - Time windows per stop + working hours per courier
  - Paired pickup → delivery constraints (same vehicle, pickup first)
  - Fragile-order / motorcycle exclusion
  - Per-stop service time

All time values are in seconds-from-midnight (integers).
All weight values are in grams (integers) to avoid float rounding.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.config import settings
from app.models.enums import FuelType, OrderPriority, VehicleType
from app.utils.compat import StrEnum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes — solver I/O
# ---------------------------------------------------------------------------

class SolverStatus(StrEnum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    GREEDY_FALLBACK = "GREEDY_FALLBACK"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class OrderData:
    """Per-order data fed into the solver."""
    order_id: str
    pickup_index: int       # index into the locations list
    delivery_index: int     # index into the locations list
    weight_grams: int       # kg × 1000 → integer grams
    pickup_is_depot: bool = False  # warehouse/depot preload or reload pickup
    volume_cm3: int = 0     # package volume in cm³
    package_count: int = 1  # number of physical packages
    is_fragile: bool = False
    is_temp_sensitive: bool = False
    required_vehicle_tags: frozenset[str] = frozenset()
    earliest_time_s: int = 0    # delivery earliest, seconds from midnight
    latest_time_s: int = 0      # preferred delivery latest, seconds from midnight
    hard_latest_time_s: int | None = None
    service_time_s: int = 0     # seconds spent at the stop
    pickup_earliest_time_s: int | None = None
    pickup_latest_time_s: int | None = None
    priority: OrderPriority = OrderPriority.NORMAL
    priority_penalty_multiplier: float = 1.0
    lateness_penalty_per_minute: int = 0
    unserved_penalty: int = 0
    allowed_vehicle_indices: tuple[int, ...] | None = None
    # Phase 2.3 — synthetic "go to charger and charge" order injected
    # at dispatch time when the tenant has ev_auto_charging_enabled.
    # The feasibility kernel resets the vehicle's remaining battery
    # range when a charging order's pickup visit is processed.
    is_charging: bool = False
    # Passenger count for RIDE orders. 0 for non-RIDE; the kernel's
    # generic load-dimension loop enforces this against
    # VehicleData.max_passengers exactly like weight/volume/packages.
    passenger_count: int = 0


@dataclass(frozen=True, slots=True)
class VehicleData:
    """Per-vehicle (courier) data fed into the solver."""
    courier_id: str
    vehicle_type: VehicleType
    capacity_grams: int     # max_weight_kg × 1000
    capacity_volume_cm3: int = 0  # max volume; 0 = unconstrained
    max_packages: int = 0         # max package count; 0 = unconstrained
    depot_index: int = 0          # index into the locations list (start depot)
    # Multi-depot extension (Phase 2): the set of depot location-indices
    # this vehicle is allowed to visit mid-route for reloads. Empty tuple
    # is a sentinel for "only `depot_index`" and produces bit-identical
    # behavior to the single-depot pre-MA3 path. When populated, the
    # FIRST element MUST equal `depot_index`; downstream code resolves
    # via `allowed_depot_indices or (depot_index,)`.
    allowed_depot_indices: tuple[int, ...] = ()
    # Optional shift-end depot. `None` means "end at `depot_index`,"
    # matching the previous behavior. When set, it MUST appear in
    # `allowed_depot_indices` (or equal `depot_index`).
    end_depot_index: int | None = None
    start_time_s: int = 0         # working hours start (seconds from midnight)
    end_time_s: int = 0           # preferred working-hours end (seconds from midnight)
    hard_end_time_s: int | None = None
    break_duration_s: int = 0
    break_earliest_s: int = 0
    break_latest_s: int = 0
    # `max_distance_m` is the *initial* battery budget for EV-aware
    # tenants (= current_pct × full_range × 1000). Non-EV: route
    # distance cap (or 0 = unconstrained).
    # `full_range_m` is the post-charge usable distance. Non-zero only
    # when ev_auto_charging_enabled is on; the kernel uses this to
    # reset the remaining-range counter after each charging visit.
    max_distance_m: int = 0
    full_range_m: int = 0
    max_stops: int = 0
    # Maximum passenger capacity. 0 = unconstrained (mirrors the
    # weight/volume/packages convention). Only meaningful for RIDE-mode
    # tenants; non-ride fleets leave it at 0.
    max_passengers: int = 0
    allow_fragile: bool = True
    allow_temp_sensitive: bool = True
    vehicle_tags: frozenset[str] = frozenset()
    trip_index: int = 0           # which trip for multi-trip VRP (0, 1, 2...)
    physical_courier_id: str = "" # groups virtual vehicles back to physical courier
    fixed_route_cost: int = 0
    distance_cost_per_km: int = 0
    labor_cost_per_minute: int = 0
    fuel_cost_per_km: int = 0
    overtime_cost_per_minute: int = 0
    overtime_penalty_per_minute: int = 0
    fuel_type: FuelType | None = None
    fuel_efficiency_km_per_liter: float | None = None  # from VehicleSpec; None = no spec
    co2_g_per_km: float | None = None                  # from VehicleSpec; None = no spec
    osrm_profile: str = "driving"


@dataclass(frozen=True, slots=True)
class EconomicCostBreakdown:
    total_generalized_cost: int = 0
    distance_cost: int = 0
    labor_cost: int = 0
    fuel_cost: int = 0
    fixed_route_cost: int = 0
    lateness_penalty: int = 0
    overtime_penalty: int = 0
    unserved_penalty: int = 0
    late_minutes: int = 0
    overtime_minutes: int = 0
    active_vehicle_count: int = 0
    estimated_fuel_liters: float | None = None
    estimated_co2_g: float | None = None
    # EV-specific: kWh delivered at solver-inserted CHARGING stops and the
    # ``₺``-equivalent at the tenant's planning electricity tariff. ``None``
    # for non-EV routes and routes where charging is disabled, so the
    # dispatcher can distinguish "no chargers needed" (None) from "no
    # cost incurred at the chargers we used" (0).
    estimated_charge_kwh: float | None = None
    charging_cost: int = 0


@dataclass(frozen=True, slots=True)
class PlannedStop:
    """A single stop in a solved route."""
    order_id: str
    stop_type: str         # "PICKUP" or "DELIVERY"
    location_index: int
    lat: float
    lon: float
    arrival_time_s: int    # estimated arrival (seconds from midnight)
    departure_time_s: int  # arrival + service time


@dataclass(frozen=True, slots=True)
class PlannedRoute:
    """A complete route for one courier."""
    courier_id: str
    stops: list[PlannedStop]
    total_distance_m: int
    total_duration_s: int
    route_start_time_s: int = 0
    route_end_time_s: int = 0
    cost_breakdown: EconomicCostBreakdown | None = None


@dataclass(frozen=True, slots=True)
class VRPSolution:
    """Complete solver output."""
    routes: list[PlannedRoute]
    unassigned_orders: list[str]
    total_distance_m: int
    total_duration_s: int
    solver_status: SolverStatus
    cost_breakdown: EconomicCostBreakdown | None = None


@dataclass
class VRPInput:
    """Everything the solver needs."""
    locations: list[tuple[float, float]]   # (lat, lon) for each unique point
    distance_matrix: list[list[int]]       # metres (ints), N×N
    duration_matrix: list[list[int]]       # seconds (ints), N×N
    orders: list[OrderData]
    vehicles: list[VehicleData]
    distance_matrices: dict[str, list[list[int]]] | None = None
    duration_matrices: dict[str, list[list[int]]] | None = None


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

# Default upper-bound for the Time dimension.  Sized for a single working day
# plus generous slack.  For weekly bulk planning we derive a larger horizon
# from the input vehicles in `_resolve_time_horizon`.  Keep this small enough
# to avoid OR-Tools int64 overflow when multiplied by per-arc transits.
_LARGE_TIME = 24 * 3600 * 2  # 48 hours expressed in seconds


def _resolve_time_horizon(vehicles: list[VehicleData]) -> int:
    """Pick a Time-dimension upper bound that fits every vehicle's shift.

    Single-day batches keep the legacy `_LARGE_TIME = 48h` default.  Multi-
    day shifts (e.g. the weekly bulk planner expressing time in
    `seconds-since-week-start`) need a larger ceiling so OR-Tools accepts the
    cumul ranges.  We always allow 6h of slack past the latest shift end so
    overtime soft-bounds remain reachable.
    """
    if not vehicles:
        return _LARGE_TIME
    latest_shift_end = max(
        max(v.start_time_s, v.end_time_s, v.hard_end_time_s or 0) for v in vehicles
    )
    return max(_LARGE_TIME, latest_shift_end + 6 * 3600)

# Weight scaling: we work in grams to avoid floating point.
_WEIGHT_SCALE = 1000
_COST_SCALE = 60

# Penalty (in arc-cost units) per unit of delivery-count span across vehicles.
# This is calibrated for legacy mode where arc costs are in meters:
#   100_000 ≈ 100 km per unit of imbalance — large enough to dominate
#   the ~30 km consolidation savings typical for 20-order Istanbul batches.
_DELIVERY_SPREAD_COEFFICIENT = 100_000


class _DistanceCallback:
    __slots__ = ("_manager", "_node_to_loc", "_matrix")
    def __init__(self, manager, node_to_loc, matrix):
        self._manager = manager
        self._node_to_loc = node_to_loc
        self._matrix = matrix

    def __call__(self, from_index: int, to_index: int) -> int:
        fn = self._node_to_loc[self._manager.IndexToNode(from_index)]
        tn = self._node_to_loc[self._manager.IndexToNode(to_index)]
        return self._matrix[fn][tn]


class _EconomicArcCostCallback:
    __slots__ = (
        "_manager",
        "_node_to_loc",
        "_distance_matrix",
        "_distance_cost_per_km",
        "_fuel_cost_per_km",
    )

    def __init__(
        self,
        manager,
        node_to_loc,
        distance_matrix,
        *,
        distance_cost_per_km: int,
        fuel_cost_per_km: int,
    ):
        self._manager = manager
        self._node_to_loc = node_to_loc
        self._distance_matrix = distance_matrix
        self._distance_cost_per_km = max(distance_cost_per_km, 0)
        self._fuel_cost_per_km = max(fuel_cost_per_km, 0)

    def __call__(self, from_index: int, to_index: int) -> int:
        fn = self._node_to_loc[self._manager.IndexToNode(from_index)]
        tn = self._node_to_loc[self._manager.IndexToNode(to_index)]
        distance_m = self._distance_matrix[fn][tn]
        cost_per_km = self._distance_cost_per_km + self._fuel_cost_per_km
        return int(round((distance_m * cost_per_km * _COST_SCALE) / 1000.0))


class _TimeCallback:
    __slots__ = ("_manager", "_node_to_loc", "_matrix", "_service_dict")
    def __init__(self, manager, node_to_loc, matrix, service_dict):
        self._manager = manager
        self._node_to_loc = node_to_loc
        self._matrix = matrix
        self._service_dict = service_dict

    def __call__(self, from_index: int, to_index: int) -> int:
        fn = self._node_to_loc[self._manager.IndexToNode(from_index)]
        tn = self._node_to_loc[self._manager.IndexToNode(to_index)]
        travel = self._matrix[fn][tn]
        service = self._service_dict.get(self._manager.IndexToNode(to_index), 0)
        return travel + service


def _profile_for_vehicle(vehicle: VehicleData) -> str:
    return vehicle.osrm_profile or "driving"


def _distance_matrix_for_vehicle(vrp_input: VRPInput, vehicle: VehicleData) -> list[list[int]]:
    matrices = vrp_input.distance_matrices or {}
    return matrices.get(_profile_for_vehicle(vehicle), vrp_input.distance_matrix)


def _duration_matrix_for_vehicle(vrp_input: VRPInput, vehicle: VehicleData) -> list[list[int]]:
    matrices = vrp_input.duration_matrices or {}
    return matrices.get(_profile_for_vehicle(vehicle), vrp_input.duration_matrix)


class _UnaryDemandCallback:
    __slots__ = ("_manager", "_demand_dict")
    def __init__(self, manager, demand_dict):
        self._manager = manager
        self._demand_dict = demand_dict

    def __call__(self, from_index: int) -> int:
        return self._demand_dict.get(self._manager.IndexToNode(from_index), 0)


class _StopsCallback:
    __slots__ = ("_manager", "_n_depots", "_stop_count_by_node")
    def __init__(self, manager, n_depots, stop_count_by_node=None):
        self._manager = manager
        self._n_depots = n_depots
        self._stop_count_by_node = stop_count_by_node or {}

    def __call__(self, from_index: int) -> int:
        vn = self._manager.IndexToNode(from_index)
        if vn < self._n_depots:
            return 0
        return self._stop_count_by_node.get(vn, 1)

def solve(
    vrp_input: VRPInput,
    *,
    initial_routes: list[list[int]] | None = None,
    time_limit_seconds: int | None = None,
    spread_fleet: bool = False,
) -> VRPSolution:
    """Run the CVRPTW solver.

    This is a synchronous, CPU-bound function.  It should be called from
    a Celery worker or a thread pool, never from an async event loop.

    Args:
        vrp_input: Fully constructed VRPInput (see build_vrp_input()).
        initial_routes: Optional warm-start: list of node sequences per
            vehicle from a previous solution. Each inner list contains
            the virtual node indices visited by that vehicle (excluding
            start/end depot nodes).
        time_limit_seconds: Optional explicit search limit override. Intended
            for deterministic tests and benchmark harnesses; production callers
            normally rely on adaptive limits derived from problem size.

    Returns:
        VRPSolution with planned routes and any unassigned orders.
    """
    orders = vrp_input.orders
    vehicles = vrp_input.vehicles
    locations = vrp_input.locations

    num_vehicles = len(vehicles)
    if num_vehicles == 0 or len(orders) == 0:
        return VRPSolution(
            routes=[],
            unassigned_orders=[o.order_id for o in orders],
            total_distance_m=0,
            total_duration_s=0,
            solver_status=SolverStatus.NOT_FOUND,
        )

    # ------------------------------------------------------------------
    # Build virtual node mapping
    # ------------------------------------------------------------------
    # Multiple orders may share the same physical location. OR-Tools
    # requires each pickup/delivery to be a unique node, so we create
    # virtual nodes and map them back to physical locations for
    # distance/duration lookups.
    #
    # Phase 5 multi-depot — each vehicle gets two virtual depot slots:
    # a START slot at 2*i and an END slot at 2*i+1. When the vehicle's
    # `end_depot_index` is None (the default), both slots resolve to
    # the same physical location and the layout is bit-identical to
    # the pre-Phase-5 single-slot layout. Vehicles with a distinct end
    # depot resolve the END slot to that location, so OR-Tools can
    # honor "start at A, end at B" routes naturally.
    #
    # Layout: [start_0, end_0, start_1, end_1, ..., pickup_0, delivery_0, pickup_1, delivery_1, ...]
    n_depots = 2 * num_vehicles
    n_order_nodes = 2 * len(orders)
    num_nodes = n_depots + n_order_nodes

    # node_to_loc maps virtual node index -> physical location index
    node_to_loc: list[int] = []
    for v in vehicles:
        node_to_loc.append(v.depot_index)
        node_to_loc.append(
            v.end_depot_index if v.end_depot_index is not None else v.depot_index
        )
    for order in orders:
        node_to_loc.append(order.pickup_index)
        node_to_loc.append(order.delivery_index)

    starts = [2 * i for i in range(num_vehicles)]
    ends = [2 * i + 1 for i in range(num_vehicles)]

    # Remap order indices to virtual nodes
    order_virtual_pickup: dict[str, int] = {}
    order_virtual_delivery: dict[str, int] = {}
    for i, order in enumerate(orders):
        order_virtual_pickup[order.order_id] = n_depots + 2 * i
        order_virtual_delivery[order.order_id] = n_depots + 2 * i + 1
    # ------------------------------------------------------------------
    # Routing model
    # ------------------------------------------------------------------
    manager = pywrapcp.RoutingIndexManager(num_nodes, num_vehicles,
                                           starts, ends)
    routing = pywrapcp.RoutingModel(manager)

    economic_mode = _uses_economic_objective(orders, vehicles)
    if economic_mode:
        _add_virtual_trip_activation_constraints(routing, vehicles)

    # ------------------------------------------------------------------
    # Arc costs (distance-only legacy mode, generalized economic cost when enabled)
    # ------------------------------------------------------------------
    # Register ONE distance callback per unique OSRM profile (not per vehicle).
    # Sharing callback indices across vehicles of the same profile lets OR-Tools
    # apply stronger global bound propagation, which is critical for solver
    # performance when many virtual trip-vehicles share the same matrix.
    _profile_dist_cb: dict[str, int] = {}       # profile → registered cb index
    _dist_cb_objects: list[_DistanceCallback] = []  # keep Python objects alive
    vehicle_distance_cb_indices: list[int] = []
    for vehicle in vehicles:
        profile = _profile_for_vehicle(vehicle)
        if profile not in _profile_dist_cb:
            cb = _DistanceCallback(
                manager,
                node_to_loc,
                _distance_matrix_for_vehicle(vrp_input, vehicle),
            )
            _dist_cb_objects.append(cb)
            _profile_dist_cb[profile] = routing.RegisterTransitCallback(cb)
        vehicle_distance_cb_indices.append(_profile_dist_cb[profile])

    if economic_mode:
        # Dedupe arc-cost callbacks by (profile, distance_cost_per_km,
        # fuel_cost_per_km).  Multi-trip expansion produces N×3 virtual
        # vehicles where every trip of the same physical courier shares
        # identical cost coefficients.  Re-using one callback index for all
        # vehicles in the same cost class lets OR-Tools propagate stronger
        # bounds across them and cuts callback registration time noticeably
        # on large fleets.  The behaviour for any single vehicle is
        # *identical* — same coefficients, same matrix per profile.
        _econ_cb_cache: dict[tuple[str, int, int], int] = {}
        _econ_cb_objects: list[_EconomicArcCostCallback] = []
        for v_idx, vehicle in enumerate(vehicles):
            profile = _profile_for_vehicle(vehicle)
            key = (
                profile,
                max(vehicle.distance_cost_per_km, 0),
                max(vehicle.fuel_cost_per_km, 0),
            )
            cost_cb_index = _econ_cb_cache.get(key)
            if cost_cb_index is None:
                cb = _EconomicArcCostCallback(
                    manager,
                    node_to_loc,
                    _distance_matrix_for_vehicle(vrp_input, vehicle),
                    distance_cost_per_km=vehicle.distance_cost_per_km,
                    fuel_cost_per_km=vehicle.fuel_cost_per_km,
                )
                _econ_cb_objects.append(cb)
                cost_cb_index = routing.RegisterTransitCallback(cb)
                _econ_cb_cache[key] = cost_cb_index
            routing.SetArcCostEvaluatorOfVehicle(cost_cb_index, v_idx)
            if vehicle.fixed_route_cost > 0:
                routing.SetFixedCostOfVehicle(vehicle.fixed_route_cost * _COST_SCALE, v_idx)
    else:
        for v_idx, cb_index in enumerate(vehicle_distance_cb_indices):
            routing.SetArcCostEvaluatorOfVehicle(cb_index, v_idx)

    # ------------------------------------------------------------------
    # Time dimension  (handles working hours + order time windows + service)
    # ------------------------------------------------------------------
    # Per-virtual-node service time lookup
    _vnode_service: dict[int, int] = {}
    for order in orders:
        pickup_service_s = 0 if order.pickup_is_depot else order.service_time_s
        _vnode_service[order_virtual_pickup[order.order_id]] = pickup_service_s
        _vnode_service[order_virtual_delivery[order.order_id]] = order.service_time_s

    # Register ONE time callback per unique OSRM profile (same reason as above).
    _profile_time_cb: dict[str, int] = {}
    _time_cb_objects: list[_TimeCallback] = []
    vehicle_time_cb_indices: list[int] = []
    for vehicle in vehicles:
        profile = _profile_for_vehicle(vehicle)
        if profile not in _profile_time_cb:
            cb = _TimeCallback(
                manager,
                node_to_loc,
                _duration_matrix_for_vehicle(vrp_input, vehicle),
                _vnode_service,
            )
            _time_cb_objects.append(cb)
            _profile_time_cb[profile] = routing.RegisterTransitCallback(cb)
        vehicle_time_cb_indices.append(_profile_time_cb[profile])

    time_horizon = _resolve_time_horizon(vehicles)
    routing.AddDimensionWithVehicleTransits(
        vehicle_time_cb_indices,
        time_horizon,       # max waiting time (slack) at a node
        time_horizon,       # max cumulative time per vehicle
        False,              # don't force start cumul to zero
        "Time",
    )
    time_dimension = routing.GetDimensionOrDie("Time")

    # Working hours per vehicle
    for v_idx, vehicle in enumerate(vehicles):
        start_index = routing.Start(v_idx)
        end_index = routing.End(v_idx)
        hard_end_s = vehicle.hard_end_time_s or vehicle.end_time_s
        time_dimension.CumulVar(start_index).SetRange(
            vehicle.start_time_s, hard_end_s
        )
        time_dimension.CumulVar(end_index).SetRange(
            vehicle.start_time_s, hard_end_s
        )
        if economic_mode and vehicle.labor_cost_per_minute > 0:
            time_dimension.SetSpanCostCoefficientForVehicle(
                vehicle.labor_cost_per_minute,
                v_idx,
            )
        overtime_penalty_per_minute = (
            vehicle.overtime_penalty_per_minute + vehicle.overtime_cost_per_minute
        )
        if economic_mode and overtime_penalty_per_minute > 0:
            time_dimension.SetCumulVarSoftUpperBound(
                end_index,
                vehicle.end_time_s,
                overtime_penalty_per_minute,
            )
        # Minimise total route time (soft upper bound)
        routing.AddVariableMinimizedByFinalizer(
            time_dimension.CumulVar(start_index)
        )
        routing.AddVariableMinimizedByFinalizer(
            time_dimension.CumulVar(end_index)
        )

    # Driver break intervals
    for v_idx, vehicle in enumerate(vehicles):
        if vehicle.break_duration_s <= 0:
            continue
        break_interval = routing.solver().FixedDurationIntervalVar(
            vehicle.break_earliest_s,
            vehicle.break_latest_s,
            vehicle.break_duration_s,
            False,
            f"break_{v_idx}",
        )
        time_dimension.SetBreakIntervalsOfVehicle([break_interval], v_idx, [0] * num_nodes)

    # Time windows per order (using virtual nodes — no shared-node conflicts)
    for order in orders:
        vp = order_virtual_pickup[order.order_id]
        vd = order_virtual_delivery[order.order_id]
        vp_idx = manager.NodeToIndex(vp)
        vd_idx = manager.NodeToIndex(vd)

        pickup_earliest = (
            0
            if order.pickup_is_depot
            else (
                order.pickup_earliest_time_s
                if order.pickup_earliest_time_s is not None
                else order.earliest_time_s
            )
        )
        pickup_latest = (
            time_horizon
            if order.pickup_is_depot
            else (
                order.pickup_latest_time_s
                if order.pickup_latest_time_s is not None
                else order.latest_time_s
            )
        )
        delivery_hard_latest = order.hard_latest_time_s or order.latest_time_s

        if vp_idx != -1:
            time_dimension.CumulVar(vp_idx).SetRange(pickup_earliest, pickup_latest)
        if vd_idx != -1:
            time_dimension.CumulVar(vd_idx).SetRange(order.earliest_time_s, delivery_hard_latest)
            if economic_mode:
                penalty = _priority_weighted_lateness_penalty(order)
                if penalty > 0:
                    time_dimension.SetCumulVarSoftUpperBound(
                        vd_idx,
                        order.latest_time_s,
                        penalty,
                    )

    # ------------------------------------------------------------------
    # Capacity dimension  (weight in grams)
    # ------------------------------------------------------------------
    _vnode_demand: dict[int, int] = {}
    for order in orders:
        _vnode_demand[order_virtual_pickup[order.order_id]] = order.weight_grams
        _vnode_demand[order_virtual_delivery[order.order_id]] = -order.weight_grams

    demand_callback = _UnaryDemandCallback(manager, _vnode_demand)
    demand_cb_index = routing.RegisterUnaryTransitCallback(demand_callback)

    vehicle_capacities = [v.capacity_grams for v in vehicles]
    routing.AddDimensionWithVehicleCapacity(
        demand_cb_index,
        0,                  # no slack for capacity
        vehicle_capacities,
        True,               # start cumul at zero
        "Weight",
    )

    # ------------------------------------------------------------------
    # Volume dimension (cm³)
    # ------------------------------------------------------------------
    has_volume_cap = any(v.capacity_volume_cm3 > 0 for v in vehicles)
    if has_volume_cap:
        _vnode_volume: dict[int, int] = {}
        for order in orders:
            _vnode_volume[order_virtual_pickup[order.order_id]] = order.volume_cm3
            _vnode_volume[order_virtual_delivery[order.order_id]] = -order.volume_cm3

        volume_callback = _UnaryDemandCallback(manager, _vnode_volume)
        volume_cb_index = routing.RegisterUnaryTransitCallback(volume_callback)
        vehicle_volumes = [
            v.capacity_volume_cm3 if v.capacity_volume_cm3 > 0 else 999_999_999
            for v in vehicles
        ]
        routing.AddDimensionWithVehicleCapacity(
            volume_cb_index,
            0,
            vehicle_volumes,
            True,
            "Volume",
        )

    # ------------------------------------------------------------------
    # Package count dimension
    # ------------------------------------------------------------------
    has_pkg_cap = any(v.max_packages > 0 for v in vehicles)
    if has_pkg_cap:
        _vnode_pkg: dict[int, int] = {}
        for order in orders:
            _vnode_pkg[order_virtual_pickup[order.order_id]] = order.package_count
            _vnode_pkg[order_virtual_delivery[order.order_id]] = -order.package_count

        pkg_callback = _UnaryDemandCallback(manager, _vnode_pkg)
        pkg_cb_index = routing.RegisterUnaryTransitCallback(pkg_callback)
        vehicle_pkg_caps = [
            v.max_packages if v.max_packages > 0 else 9999
            for v in vehicles
        ]
        routing.AddDimensionWithVehicleCapacity(
            pkg_cb_index,
            0,
            vehicle_pkg_caps,
            True,
            "PkgCount",
        )

    # ------------------------------------------------------------------
    # Per-vehicle distance cap
    # ------------------------------------------------------------------
    has_distance_cap = any(v.max_distance_m > 0 for v in vehicles)
    if has_distance_cap:
        vehicle_max_dists = [
            v.max_distance_m if v.max_distance_m > 0 else 999_999_999
            for v in vehicles
        ]
        routing.AddDimensionWithVehicleTransitAndCapacity(
            vehicle_distance_cb_indices,
            0,
            vehicle_max_dists,
            True,
            "RouteDist",
        )

    # ------------------------------------------------------------------
    # Per-vehicle max stops cap
    # ------------------------------------------------------------------
    has_stops_cap = any(v.max_stops > 0 for v in vehicles)
    if has_stops_cap:

        stop_count_by_node = {}
        for order in orders:
            pickup_node = order_virtual_pickup[order.order_id]
            delivery_node = order_virtual_delivery[order.order_id]
            stop_count_by_node[pickup_node] = 0 if order.pickup_is_depot else 1
            stop_count_by_node[delivery_node] = 1
        stops_callback = _StopsCallback(manager, n_depots, stop_count_by_node)
        stops_cb_index = routing.RegisterUnaryTransitCallback(stops_callback)
        vehicle_max_stops = [
            v.max_stops if v.max_stops > 0 else 9999
            for v in vehicles
        ]
        routing.AddDimensionWithVehicleCapacity(
            stops_cb_index,
            0,
            vehicle_max_stops,
            True,
            "Stops",
        )

    # ------------------------------------------------------------------
    # Pickup & delivery pairs
    # ------------------------------------------------------------------
    for order in orders:
        vp_idx = manager.NodeToIndex(order_virtual_pickup[order.order_id])
        vd_idx = manager.NodeToIndex(order_virtual_delivery[order.order_id])
        if vp_idx == -1 or vd_idx == -1:
            continue
        routing.AddPickupAndDelivery(vp_idx, vd_idx)
        routing.solver().Add(routing.VehicleVar(vp_idx) == routing.VehicleVar(vd_idx))
        routing.solver().Add(time_dimension.CumulVar(vp_idx) <= time_dimension.CumulVar(vd_idx))

    # ------------------------------------------------------------------
    # Fragile constraint — exclude motorcycles
    # ------------------------------------------------------------------
    no_fragile_vehicles = [
        i
        for i, v in enumerate(vehicles)
        if not v.allow_fragile or v.vehicle_type == VehicleType.MOTORCYCLE
    ]
    no_temp_vehicles = [i for i, v in enumerate(vehicles) if not v.allow_temp_sensitive]

    for order in orders:
        vp_idx = manager.NodeToIndex(order_virtual_pickup[order.order_id])
        vd_idx = manager.NodeToIndex(order_virtual_delivery[order.order_id])

        if order.allowed_vehicle_indices is not None:
            allowed = set(order.allowed_vehicle_indices)
            for v_idx in range(num_vehicles):
                if v_idx in allowed:
                    continue
                if vp_idx != -1:
                    routing.VehicleVar(vp_idx).RemoveValue(v_idx)
                if vd_idx != -1:
                    routing.VehicleVar(vd_idx).RemoveValue(v_idx)

        if order.is_fragile:
            for v_idx in no_fragile_vehicles:
                if vp_idx != -1:
                    routing.VehicleVar(vp_idx).RemoveValue(v_idx)
                if vd_idx != -1:
                    routing.VehicleVar(vd_idx).RemoveValue(v_idx)

        if order.is_temp_sensitive:
            for v_idx in no_temp_vehicles:
                if vp_idx != -1:
                    routing.VehicleVar(vp_idx).RemoveValue(v_idx)
                if vd_idx != -1:
                    routing.VehicleVar(vd_idx).RemoveValue(v_idx)

        if order.required_vehicle_tags:
            for v_idx, vehicle in enumerate(vehicles):
                if not order.required_vehicle_tags.issubset(vehicle.vehicle_tags):
                    if vp_idx != -1:
                        routing.VehicleVar(vp_idx).RemoveValue(v_idx)
                    if vd_idx != -1:
                        routing.VehicleVar(vd_idx).RemoveValue(v_idx)

    # ------------------------------------------------------------------
    # Allow dropping orders
    # ------------------------------------------------------------------
    # Separate disjunctions per pickup and delivery. AddPickupAndDelivery
    # links them; when the solver drops the pickup, the delivery is orphaned
    # (precedence constraint), so both get dropped together. Penalty paid
    # per dropped node (2x per dropped order).
    _DROP_PENALTY = 1_000_000_000
    for order in orders:
        vp_idx = manager.NodeToIndex(order_virtual_pickup[order.order_id])
        vd_idx = manager.NodeToIndex(order_virtual_delivery[order.order_id])
        penalty = (
            max(
                1,
                int(
                    round(
                        (order.unserved_penalty * order.priority_penalty_multiplier * _COST_SCALE)
                        / 2.0
                    )
                ),
            )
            if economic_mode and order.unserved_penalty > 0
            else _DROP_PENALTY
        )
        if vp_idx != -1:
            routing.AddDisjunction([vp_idx], penalty)
        if vd_idx != -1:
            routing.AddDisjunction([vd_idx], penalty)

    # ------------------------------------------------------------------
    # Fleet spread dimension (optional)
    # ------------------------------------------------------------------
    # When spread_fleet=True, add a "delivery count" dimension and set a
    # global span cost coefficient on it.  This penalises the difference
    # between the most-loaded and least-loaded vehicle, strongly incentivising
    # the solver to distribute orders across every available courier.
    #
    # Dimension layout:
    #   transit = 1 at every delivery virtual-node, 0 everywhere else.
    #   cumul at end of each vehicle route = deliveries served by that vehicle.
    #   global span cost = coefficient × (max_count − min_count)
    #
    # Only applied when there are at least 2 orders AND 2 vehicles, because
    # there is nothing to spread with a single vehicle or a single order.
    if spread_fleet and len(orders) >= 2 and num_vehicles >= 2:
        delivery_nodes = frozenset(
            n_depots + 2 * i + 1  # delivery virtual-node for order i
            for i in range(len(orders))
        )

        def _delivery_count_transit(index: int) -> int:
            return 1 if manager.IndexToNode(index) in delivery_nodes else 0

        count_cb_index = routing.RegisterUnaryTransitCallback(_delivery_count_transit)
        routing.AddDimension(
            count_cb_index,
            0,            # slack_max: pure accumulation, no waiting
            len(orders),  # capacity: at most all deliveries on one vehicle
            True,         # fix_start_cumul_to_zero
            "DeliveryCount",
        )
        routing.GetDimensionOrDie("DeliveryCount").SetGlobalSpanCostCoefficient(
            _DELIVERY_SPREAD_COEFFICIENT
        )

    # ------------------------------------------------------------------
    # Adaptive search parameters by problem size
    # ------------------------------------------------------------------
    #
    # Important: the batch dispatch service passes explicit production solve
    # budgets (30/60/120s). The raw solver is also used directly by unit tests
    # and benchmark helpers, so its default budgets stay intentionally light
    # for tiny ad-hoc problems to avoid burning a full local-search window on
    # trivial instances.
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    n_orders = len(orders)

    if economic_mode or n_orders <= 50:
        search_params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
        )
        search_params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
    else:
        search_params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
        )
        search_params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.SIMULATED_ANNEALING
        )

    adaptive_limit = max(settings.TIME_LIMIT_SECONDS, 1)
    if time_limit_seconds is not None:
        adaptive_limit = max(1, time_limit_seconds)

    search_params.time_limit.FromSeconds(adaptive_limit)
    search_params.log_search = False

    # ------------------------------------------------------------------
    # Solve
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Warm-start from previous solution (if available)
    # ------------------------------------------------------------------
    initial_solution = None
    if initial_routes is not None and len(initial_routes) == num_vehicles:
        try:
            initial_solution = routing.ReadAssignmentFromRoutes(
                initial_routes, True  # ignore_inactive_indices
            )
            if initial_solution is not None:
                logger.info("Warm-start: loaded initial solution from %d vehicle routes",
                            num_vehicles)
            else:
                logger.info("Warm-start: initial routes were not feasible, starting fresh")
        except Exception as exc:
            logger.warning("Warm-start failed (%s), starting fresh", exc)
            initial_solution = None

    logger.info("Starting VRP solver: %d orders, %d vehicles, %d nodes (time limit %ds, warm_start=%s)",
                len(orders), num_vehicles, num_nodes, adaptive_limit, initial_solution is not None)

    solve_start = time.monotonic()
    if initial_solution is not None:
        solution = routing.SolveFromAssignmentWithParameters(
            initial_solution, search_params
        )
    else:
        solution = routing.SolveWithParameters(search_params)
    actual_solve_s = time.monotonic() - solve_start

    if solution is None:
        logger.warning(
            "VRP solver found no solution (status=%s, %.1fs), falling back to greedy",
            routing.status(), actual_solve_s,
        )
        from app.algorithms.greedy_fallback import greedy_assign
        return greedy_assign(vrp_input)

    # ------------------------------------------------------------------
    # Extract solution
    # ------------------------------------------------------------------
    status = _map_solver_status(
        routing.status(),
        time_limit_s=adaptive_limit,
        actual_solve_s=actual_solve_s,
    )
    planned_routes: list[PlannedRoute] = []
    assigned_order_ids: set[str] = set()
    grand_distance = 0
    grand_duration = 0
    order_by_id = {order.order_id: order for order in orders}

    for v_idx in range(num_vehicles):
        index = routing.Start(v_idx)
        stops: list[PlannedStop] = []
        route_distance = 0
        route_start_time = solution.Min(time_dimension.CumulVar(index))
        route_end_time = 0

        while not routing.IsEnd(index):
            vnode = manager.IndexToNode(index)
            phys = node_to_loc[vnode]
            time_var = time_dimension.CumulVar(index)
            arrival = solution.Min(time_var)
            service = _vnode_service.get(vnode, 0)
            departure = arrival + service

            stop_info = _identify_virtual_stop(vnode, n_depots, orders)
            if stop_info:
                order_id_, stop_type_ = stop_info
                assigned_order_ids.add(order_id_)
                lat, lon = locations[phys]
                stops.append(PlannedStop(
                    order_id=order_id_,
                    stop_type=stop_type_,
                    location_index=phys,
                    lat=lat,
                    lon=lon,
                    arrival_time_s=arrival,
                    departure_time_s=departure,
                ))

            next_index = solution.Value(routing.NextVar(index))
            fn = node_to_loc[manager.IndexToNode(index)]
            tn = node_to_loc[manager.IndexToNode(next_index)]
            route_distance += _distance_matrix_for_vehicle(vrp_input, vehicles[v_idx])[fn][tn]
            index = next_index

        # End node time
        time_var = time_dimension.CumulVar(index)
        route_end_time = solution.Min(time_var)
        route_duration = route_end_time - route_start_time

        if stops:
            route_cost_breakdown = _build_route_cost_breakdown(
                vehicle=vehicles[v_idx],
                route_distance_m=route_distance,
                route_duration_s=max(route_duration, 0),
                route_end_time_s=route_end_time,
                stops=stops,
                orders_by_id=order_by_id,
            )
            planned_routes.append(PlannedRoute(
                courier_id=vehicles[v_idx].courier_id,
                stops=stops,
                total_distance_m=route_distance,
                total_duration_s=max(route_duration, 0),
                route_start_time_s=max(route_start_time, 0),
                route_end_time_s=max(route_end_time, 0),
                cost_breakdown=route_cost_breakdown,
            ))
            grand_distance += route_distance
            grand_duration += max(route_duration, 0)

    # Unassigned orders
    unassigned = [
        o.order_id for o in orders if o.order_id not in assigned_order_ids
    ]
    solution_cost_breakdown = _build_solution_cost_breakdown(
        routes=planned_routes,
        unassigned_order_ids=unassigned,
        orders_by_id=order_by_id,
    )

    logger.info(
        "VRP solver finished: status=%s, %d routes, %d unassigned, "
        "objective=%d, generalized_cost=%d, total_distance=%dm, total_duration=%ds",
        status.value, len(planned_routes), len(unassigned),
        solution.ObjectiveValue(),
        solution_cost_breakdown.total_generalized_cost,
        grand_distance, grand_duration,
    )

    return VRPSolution(
        routes=planned_routes,
        unassigned_orders=unassigned,
        total_distance_m=grand_distance,
        total_duration_s=grand_duration,
        solver_status=status,
        cost_breakdown=solution_cost_breakdown,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _identify_virtual_stop(
    vnode: int,
    n_depots: int,
    orders: list[OrderData],
) -> tuple[str, str] | None:
    """Map a virtual node back to (order_id, "PICKUP" | "DELIVERY"). O(1) via layout."""
    if vnode < n_depots:
        return None
    order_idx = (vnode - n_depots) // 2
    if order_idx >= len(orders):
        return None
    is_pickup = (vnode - n_depots) % 2 == 0
    return (orders[order_idx].order_id, "PICKUP" if is_pickup else "DELIVERY")


def _map_solver_status(
    status_code: int,
    *,
    time_limit_s: int = 0,
    actual_solve_s: float = 0.0,
) -> SolverStatus:
    """Map OR-Tools routing status to our enum.

    Heuristic: if OR-Tools returns ROUTING_SUCCESS (1) and the solver
    finished well under the time limit, it likely proved optimality.
    If it consumed nearly all the budget, the solution is feasible but
    may not be optimal.
    """
    # pywrapcp.RoutingModel status codes:
    # 0 = ROUTING_NOT_SOLVED
    # 1 = ROUTING_SUCCESS (optimal or feasible)
    # 2 = ROUTING_PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED
    # 3 = ROUTING_FAIL
    # 4 = ROUTING_FAIL_TIMEOUT
    # 5 = ROUTING_INVALID
    if status_code == 1:
        if time_limit_s > 0 and actual_solve_s >= time_limit_s * 0.95:
            return SolverStatus.FEASIBLE
        return SolverStatus.OPTIMAL
    elif status_code in (2, 4):
        return SolverStatus.FEASIBLE
    else:
        return SolverStatus.NOT_FOUND


def _uses_economic_objective(
    orders: list[OrderData],
    vehicles: list[VehicleData],
) -> bool:
    return any(
        order.hard_latest_time_s not in (None, order.latest_time_s)
        or order.lateness_penalty_per_minute > 0
        or order.unserved_penalty > 0
        or order.priority_penalty_multiplier != 1.0
        for order in orders
    ) or any(
        vehicle.hard_end_time_s not in (None, vehicle.end_time_s)
        or vehicle.fixed_route_cost > 0
        or vehicle.distance_cost_per_km > 0
        or vehicle.labor_cost_per_minute > 0
        or vehicle.fuel_cost_per_km > 0
        or vehicle.overtime_cost_per_minute > 0
        or vehicle.overtime_penalty_per_minute > 0
        for vehicle in vehicles
    )


def _add_virtual_trip_activation_constraints(
    routing: pywrapcp.RoutingModel,
    vehicles: list[VehicleData],
) -> None:
    grouped_trip_indices: dict[str, list[tuple[int, int]]] = {}
    for vehicle_index, vehicle in enumerate(vehicles):
        physical_courier_id = vehicle.physical_courier_id or vehicle.courier_id
        grouped_trip_indices.setdefault(physical_courier_id, []).append(
            (vehicle.trip_index, vehicle_index)
        )

    solver = routing.solver()
    for trip_entries in grouped_trip_indices.values():
        if len(trip_entries) < 2:
            continue
        trip_entries.sort(key=lambda item: item[0])
        # Intentionally pair each trip with its next trip; the shifted tail is shorter.
        for (_, earlier_vehicle_index), (_, later_vehicle_index) in zip(
            trip_entries,
            trip_entries[1:],
            strict=False,
        ):
            solver.Add(
                routing.ActiveVehicleVar(later_vehicle_index)
                <= routing.ActiveVehicleVar(earlier_vehicle_index)
            )


def _priority_weighted_lateness_penalty(order: OrderData) -> int:
    if order.lateness_penalty_per_minute <= 0:
        return 0
    return max(
        1,
        int(round(order.lateness_penalty_per_minute * order.priority_penalty_multiplier)),
    )


def _seconds_to_billed_minutes(seconds: int) -> int:
    if seconds <= 0:
        return 0
    return (seconds + 59) // 60


def _build_route_cost_breakdown(
    *,
    vehicle: VehicleData,
    route_distance_m: int,
    route_duration_s: int,
    route_end_time_s: int,
    stops: list[PlannedStop],
    orders_by_id: dict[str, OrderData],
) -> EconomicCostBreakdown:
    distance_cost = int(round((route_distance_m * vehicle.distance_cost_per_km) / 1000.0))
    fuel_cost = int(round((route_distance_m * vehicle.fuel_cost_per_km) / 1000.0))
    labor_cost = int(round((route_duration_s * vehicle.labor_cost_per_minute) / 60.0))

    lateness_penalty = 0
    late_minutes = 0
    for stop in stops:
        if stop.stop_type != "DELIVERY":
            continue
        order = orders_by_id.get(stop.order_id)
        if order is None:
            continue
        late_seconds = max(stop.arrival_time_s - order.latest_time_s, 0)
        if late_seconds <= 0:
            continue
        late_minutes += _seconds_to_billed_minutes(late_seconds)
        lateness_penalty += int(
            round(
                (late_seconds * order.lateness_penalty_per_minute * order.priority_penalty_multiplier)
                / 60.0
            )
        )

    overtime_seconds = max(route_end_time_s - vehicle.end_time_s, 0)
    overtime_minutes = _seconds_to_billed_minutes(overtime_seconds)
    overtime_penalty_per_minute = (
        vehicle.overtime_penalty_per_minute + vehicle.overtime_cost_per_minute
    )
    overtime_penalty = int(
        round((overtime_seconds * overtime_penalty_per_minute) / 60.0)
    )
    fixed_route_cost = vehicle.fixed_route_cost
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
    if vehicle.fuel_efficiency_km_per_liter and vehicle.fuel_efficiency_km_per_liter > 0:
        estimated_fuel_liters = round(route_distance_km / vehicle.fuel_efficiency_km_per_liter, 3)
    if vehicle.co2_g_per_km and vehicle.co2_g_per_km > 0:
        estimated_co2_g = round(route_distance_km * vehicle.co2_g_per_km, 1)

    return EconomicCostBreakdown(
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


def build_route_cost_breakdown(
    *,
    vehicle: VehicleData,
    route_distance_m: int,
    route_duration_s: int,
    route_end_time_s: int,
    stops: list[PlannedStop],
    orders_by_id: dict[str, OrderData],
) -> EconomicCostBreakdown:
    """Public wrapper for route-level economic cost accounting."""
    return _build_route_cost_breakdown(
        vehicle=vehicle,
        route_distance_m=route_distance_m,
        route_duration_s=route_duration_s,
        route_end_time_s=route_end_time_s,
        stops=stops,
        orders_by_id=orders_by_id,
    )


def _build_solution_cost_breakdown(
    *,
    routes: list[PlannedRoute],
    unassigned_order_ids: list[str],
    orders_by_id: dict[str, OrderData],
) -> EconomicCostBreakdown:
    totals = EconomicCostBreakdown(active_vehicle_count=len(routes))
    # Aggregate the EV-only ``estimated_charge_kwh`` separately so the
    # solution-level value stays ``None`` when every route reported
    # ``None`` (no chargers in play) but flips to a concrete float as
    # soon as one route reports any kWh.
    any_charge_reported = False
    total_kwh = 0.0
    for route in routes:
        route_costs = route.cost_breakdown or EconomicCostBreakdown()
        totals = EconomicCostBreakdown(
            total_generalized_cost=totals.total_generalized_cost + route_costs.total_generalized_cost,
            distance_cost=totals.distance_cost + route_costs.distance_cost,
            labor_cost=totals.labor_cost + route_costs.labor_cost,
            fuel_cost=totals.fuel_cost + route_costs.fuel_cost,
            fixed_route_cost=totals.fixed_route_cost + route_costs.fixed_route_cost,
            lateness_penalty=totals.lateness_penalty + route_costs.lateness_penalty,
            overtime_penalty=totals.overtime_penalty + route_costs.overtime_penalty,
            unserved_penalty=totals.unserved_penalty,
            late_minutes=totals.late_minutes + route_costs.late_minutes,
            overtime_minutes=totals.overtime_minutes + route_costs.overtime_minutes,
            active_vehicle_count=len(routes),
            charging_cost=totals.charging_cost + route_costs.charging_cost,
        )
        if route_costs.estimated_charge_kwh is not None:
            any_charge_reported = True
            total_kwh += route_costs.estimated_charge_kwh

    unserved_penalty = sum(
        int(
            round(
                orders_by_id[order_id].unserved_penalty
                * orders_by_id[order_id].priority_penalty_multiplier
            )
        )
        for order_id in unassigned_order_ids
        if order_id in orders_by_id
    )
    return EconomicCostBreakdown(
        total_generalized_cost=totals.total_generalized_cost + unserved_penalty,
        distance_cost=totals.distance_cost,
        labor_cost=totals.labor_cost,
        fuel_cost=totals.fuel_cost,
        fixed_route_cost=totals.fixed_route_cost,
        lateness_penalty=totals.lateness_penalty,
        overtime_penalty=totals.overtime_penalty,
        unserved_penalty=unserved_penalty,
        late_minutes=totals.late_minutes,
        overtime_minutes=totals.overtime_minutes,
        active_vehicle_count=len(routes),
        estimated_charge_kwh=total_kwh if any_charge_reported else None,
        charging_cost=totals.charging_cost,
    )


def build_solution_cost_breakdown(
    *,
    routes: list[PlannedRoute],
    unassigned_order_ids: list[str],
    orders_by_id: dict[str, OrderData],
) -> EconomicCostBreakdown:
    """Public wrapper for solution-level economic cost accounting."""
    return _build_solution_cost_breakdown(
        routes=routes,
        unassigned_order_ids=unassigned_order_ids,
        orders_by_id=orders_by_id,
    )


def build_vrp_input(
    locations: list[tuple[float, float]],
    distance_matrix: list[list[int]],
    duration_matrix: list[list[int]],
    orders: list[OrderData],
    vehicles: list[VehicleData],
    distance_matrices: dict[str, list[list[int]]] | None = None,
    duration_matrices: dict[str, list[list[int]]] | None = None,
) -> VRPInput:
    """Convenience constructor with validation."""
    n = len(locations)
    if len(distance_matrix) != n or len(duration_matrix) != n:
        raise ValueError(
            f"Matrix size mismatch: {n} locations but "
            f"{len(distance_matrix)} distance rows, "
            f"{len(duration_matrix)} duration rows"
        )
    for i, (d_row, t_row) in enumerate(
        zip(distance_matrix, duration_matrix, strict=True)
    ):
        if len(d_row) != n or len(t_row) != n:
            raise ValueError(f"Row {i} has wrong length")
    for label, matrices in (
        ("distance_matrices", distance_matrices or {}),
        ("duration_matrices", duration_matrices or {}),
    ):
        for profile, matrix in matrices.items():
            if len(matrix) != n:
                raise ValueError(f"{label}[{profile!r}] has {len(matrix)} rows, expected {n}")
            for i, row in enumerate(matrix):
                if len(row) != n:
                    raise ValueError(f"{label}[{profile!r}] row {i} has wrong length")

    # Validate all indices are within bounds
    for o in orders:
        if not (0 <= o.pickup_index < n and 0 <= o.delivery_index < n):
            raise ValueError(
                f"Order {o.order_id}: indices out of bounds "
                f"(pickup={o.pickup_index}, delivery={o.delivery_index}, "
                f"n={n})"
            )
        if o.latest_time_s < o.earliest_time_s:
            raise ValueError(
                f"Order {o.order_id}: delivery window is invalid "
                f"({o.earliest_time_s}>{o.latest_time_s})"
            )
        if o.hard_latest_time_s is not None and o.hard_latest_time_s < o.latest_time_s:
            raise ValueError(
                f"Order {o.order_id}: hard delivery latest must be >= preferred latest "
                f"({o.hard_latest_time_s}<{o.latest_time_s})"
            )
        if (
            o.pickup_earliest_time_s is not None
            and o.pickup_latest_time_s is not None
            and o.pickup_latest_time_s < o.pickup_earliest_time_s
        ):
            raise ValueError(
                f"Order {o.order_id}: pickup window is invalid "
                f"({o.pickup_earliest_time_s}>{o.pickup_latest_time_s})"
            )
    for v in vehicles:
        if not (0 <= v.depot_index < n):
            raise ValueError(
                f"Vehicle {v.courier_id}: depot_index={v.depot_index} "
                f"out of bounds (n={n})"
            )
        for d in v.allowed_depot_indices:
            if not (0 <= d < n):
                raise ValueError(
                    f"Vehicle {v.courier_id}: allowed_depot_indices contains "
                    f"out-of-bounds index {d} (n={n})"
                )
        if v.allowed_depot_indices and v.depot_index not in v.allowed_depot_indices:
            raise ValueError(
                f"Vehicle {v.courier_id}: depot_index={v.depot_index} must be "
                f"in allowed_depot_indices={v.allowed_depot_indices}"
            )
        if v.end_depot_index is not None:
            if not (0 <= v.end_depot_index < n):
                raise ValueError(
                    f"Vehicle {v.courier_id}: end_depot_index={v.end_depot_index} "
                    f"out of bounds (n={n})"
                )
            allowed = v.allowed_depot_indices or (v.depot_index,)
            if v.end_depot_index not in allowed:
                raise ValueError(
                    f"Vehicle {v.courier_id}: end_depot_index={v.end_depot_index} "
                    f"must be in allowed_depot_indices={allowed}"
                )
        if v.hard_end_time_s is not None and v.hard_end_time_s < v.end_time_s:
            raise ValueError(
                f"Vehicle {v.courier_id}: hard shift end must be >= preferred shift end "
                f"({v.hard_end_time_s}<{v.end_time_s})"
            )

    return VRPInput(
        locations=locations,
        distance_matrix=distance_matrix,
        duration_matrix=duration_matrix,
        orders=orders,
        vehicles=vehicles,
        distance_matrices=distance_matrices,
        duration_matrices=duration_matrices,
    )
