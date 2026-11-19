"""Unit tests for VRP solver vehicle tag filtering.

Tag semantics: an order's required_vehicle_tags must be a *subset* of the
vehicle's vehicle_tags. An order with no required tags may be served by any
vehicle regardless of what tags the vehicle carries.
"""
from app.algorithms.vrp_solver import (
    OrderData,
    SolverStatus,
    VehicleData,
    build_vrp_input,
    solve,
)
from app.models.enums import VehicleType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TAG_REFRIGERATED = "tag-refrigerated"
TAG_HAZMAT = "tag-hazmat"
TAG_FRAGILE_CERT = "tag-fragile-cert"

_N = 3  # depot=0, pickup=1, delivery=2
_DURATION = [[0 if i == j else 600 for j in range(_N)] for i in range(_N)]
_DISTANCE = [[0 if i == j else 1000 for j in range(_N)] for i in range(_N)]
_LOCS = [(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)]


def _order(order_id: str, required_tags: frozenset[str] = frozenset(), **kwargs) -> OrderData:
    defaults = dict(
        order_id=order_id,
        pickup_index=1,
        delivery_index=2,
        weight_grams=1_000,
        earliest_time_s=0,
        latest_time_s=86_400,
        service_time_s=0,
        unserved_penalty=10_000,
    )
    defaults.update(kwargs)
    return OrderData(**defaults, required_vehicle_tags=required_tags)


def _vehicle(courier_id: str, tags: frozenset[str] = frozenset(), **kwargs) -> VehicleData:
    defaults = dict(
        courier_id=courier_id,
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=50_000,
        depot_index=0,
        start_time_s=0,
        end_time_s=86_400,
    )
    defaults.update(kwargs)
    return VehicleData(**defaults, vehicle_tags=tags)


def _solve(*orders: OrderData, vehicles: list[VehicleData]):
    inp = build_vrp_input(
        locations=_LOCS,
        distance_matrix=_DISTANCE,
        duration_matrix=_DURATION,
        orders=list(orders),
        vehicles=vehicles,
    )
    return solve(inp)


def _assigned_courier(sol, order_id: str) -> str | None:
    for route in sol.routes:
        for stop in route.stops:
            if stop.order_id == order_id:
                return route.courier_id
    return None


# ---------------------------------------------------------------------------
# No required tags — any vehicle works
# ---------------------------------------------------------------------------

class TestNoRequiredTags:
    def test_untagged_order_served_by_untagged_vehicle(self):
        order = _order("o1")
        vehicle = _vehicle("c1")
        sol = _solve(order, vehicles=[vehicle])
        # With no economic objective, status may be NOT_FOUND even when order is assigned.
        # The canonical check is whether the order appears in unassigned_orders.
        assert "o1" not in sol.unassigned_orders

    def test_untagged_order_served_by_tagged_vehicle(self):
        order = _order("o1")
        vehicle = _vehicle("c1", tags=frozenset({TAG_REFRIGERATED, TAG_HAZMAT}))
        sol = _solve(order, vehicles=[vehicle])
        assert "o1" not in sol.unassigned_orders

    def test_untagged_order_with_multiple_vehicles_still_served(self):
        order = _order("o1")
        vehicles = [
            _vehicle("c1", tags=frozenset({TAG_REFRIGERATED})),
            _vehicle("c2"),
        ]
        sol = _solve(order, vehicles=vehicles)
        assert "o1" not in sol.unassigned_orders


# ---------------------------------------------------------------------------
# Single required tag
# ---------------------------------------------------------------------------

class TestSingleRequiredTag:
    def test_exact_tag_match_serves_order(self):
        order = _order("o1", required_tags=frozenset({TAG_REFRIGERATED}))
        vehicle = _vehicle("c1", tags=frozenset({TAG_REFRIGERATED}))
        sol = _solve(order, vehicles=[vehicle])
        assert "o1" not in sol.unassigned_orders

    def test_vehicle_superset_satisfies_single_tag(self):
        order = _order("o1", required_tags=frozenset({TAG_REFRIGERATED}))
        vehicle = _vehicle("c1", tags=frozenset({TAG_REFRIGERATED, TAG_HAZMAT}))
        sol = _solve(order, vehicles=[vehicle])
        assert "o1" not in sol.unassigned_orders

    def test_missing_tag_drops_order(self):
        order = _order("o1", required_tags=frozenset({TAG_REFRIGERATED}))
        vehicle = _vehicle("c1", tags=frozenset())
        sol = _solve(order, vehicles=[vehicle])
        assert "o1" in sol.unassigned_orders

    def test_wrong_tag_drops_order(self):
        order = _order("o1", required_tags=frozenset({TAG_REFRIGERATED}))
        vehicle = _vehicle("c1", tags=frozenset({TAG_HAZMAT}))
        sol = _solve(order, vehicles=[vehicle])
        assert "o1" in sol.unassigned_orders


# ---------------------------------------------------------------------------
# Multiple required tags (ALL must be present)
# ---------------------------------------------------------------------------

class TestMultipleRequiredTags:
    def test_vehicle_with_all_tags_serves_order(self):
        order = _order("o1", required_tags=frozenset({TAG_REFRIGERATED, TAG_HAZMAT}))
        vehicle = _vehicle("c1", tags=frozenset({TAG_REFRIGERATED, TAG_HAZMAT}))
        sol = _solve(order, vehicles=[vehicle])
        assert "o1" not in sol.unassigned_orders

    def test_vehicle_with_only_one_of_two_tags_drops_order(self):
        order = _order("o1", required_tags=frozenset({TAG_REFRIGERATED, TAG_HAZMAT}))
        vehicle = _vehicle("c1", tags=frozenset({TAG_REFRIGERATED}))
        sol = _solve(order, vehicles=[vehicle])
        assert "o1" in sol.unassigned_orders

    def test_vehicle_with_superset_of_tags_serves_order(self):
        order = _order("o1", required_tags=frozenset({TAG_REFRIGERATED, TAG_HAZMAT}))
        vehicle = _vehicle("c1", tags=frozenset({TAG_REFRIGERATED, TAG_HAZMAT, TAG_FRAGILE_CERT}))
        sol = _solve(order, vehicles=[vehicle])
        assert "o1" not in sol.unassigned_orders

    def test_both_tags_missing_drops_order(self):
        order = _order("o1", required_tags=frozenset({TAG_REFRIGERATED, TAG_HAZMAT}))
        vehicle = _vehicle("c1", tags=frozenset())
        sol = _solve(order, vehicles=[vehicle])
        assert "o1" in sol.unassigned_orders


# ---------------------------------------------------------------------------
# Multiple vehicles — routing to correct vehicle
# ---------------------------------------------------------------------------

class TestVehicleSelection:
    def test_tagged_order_routed_to_tagged_vehicle_only(self):
        """Order needing refrigerated must go to c2 even though c1 is available."""
        order = _order("o1", required_tags=frozenset({TAG_REFRIGERATED}))
        vehicles = [
            _vehicle("c1", tags=frozenset()),
            _vehicle("c2", tags=frozenset({TAG_REFRIGERATED})),
        ]
        sol = _solve(order, vehicles=vehicles)
        assert "o1" not in sol.unassigned_orders
        assert _assigned_courier(sol, "o1") == "c2"

    def test_two_orders_each_routed_to_specialized_vehicle(self):
        """o1 needs refrigerated, o2 needs hazmat; c1 has refrigerated, c2 has hazmat."""
        o1 = _order("o1", required_tags=frozenset({TAG_REFRIGERATED}))
        o2 = _order("o2", required_tags=frozenset({TAG_HAZMAT}), pickup_index=1, delivery_index=2)
        vehicles = [
            _vehicle("c1", tags=frozenset({TAG_REFRIGERATED})),
            _vehicle("c2", tags=frozenset({TAG_HAZMAT})),
        ]
        sol = _solve(o1, o2, vehicles=vehicles)
        assert "o1" not in sol.unassigned_orders
        assert "o2" not in sol.unassigned_orders
        assert _assigned_courier(sol, "o1") == "c1"
        assert _assigned_courier(sol, "o2") == "c2"

    def test_untagged_order_can_use_any_vehicle(self):
        """When one vehicle is overloaded tags should still allow fallback to the other."""
        o1 = _order("o1")
        o2 = _order("o2", pickup_index=1, delivery_index=2)
        vehicles = [
            _vehicle("c1", tags=frozenset({TAG_REFRIGERATED})),
            _vehicle("c2"),
        ]
        sol = _solve(o1, o2, vehicles=vehicles)
        unserved = set(sol.unassigned_orders)
        assert "o1" not in unserved
        assert "o2" not in unserved


# ---------------------------------------------------------------------------
# All vehicles reject the order
# ---------------------------------------------------------------------------

class TestAllVehiclesReject:
    def test_all_vehicles_missing_tag_order_unassigned(self):
        order = _order("o1", required_tags=frozenset({TAG_REFRIGERATED}))
        vehicles = [
            _vehicle("c1", tags=frozenset({TAG_HAZMAT})),
            _vehicle("c2", tags=frozenset()),
            _vehicle("c3", tags=frozenset({TAG_FRAGILE_CERT})),
        ]
        sol = _solve(order, vehicles=vehicles)
        assert "o1" in sol.unassigned_orders

    def test_mixed_tagged_and_untagged_orders_partial_drop(self):
        """o1 has an unserviceable tag; o2 has no tag and should still be served."""
        o1 = _order("o1", required_tags=frozenset({TAG_REFRIGERATED}))
        o2 = _order("o2", pickup_index=1, delivery_index=2)
        vehicles = [_vehicle("c1", tags=frozenset())]
        sol = _solve(o1, o2, vehicles=vehicles)
        assert "o1" in sol.unassigned_orders
        assert "o2" not in sol.unassigned_orders


# ---------------------------------------------------------------------------
# Tag filtering composes with existing constraint filters
# ---------------------------------------------------------------------------

class TestTagComposesWithFragile:
    def test_fragile_order_needing_tag_rejected_by_vehicle_without_tag(self):
        order = _order(
            "o1",
            required_tags=frozenset({TAG_REFRIGERATED}),
            is_fragile=True,
        )
        # Vehicle has tag but doesn't allow fragile
        vehicle = _vehicle("c1", tags=frozenset({TAG_REFRIGERATED}), allow_fragile=False)
        sol = _solve(order, vehicles=[vehicle])
        assert "o1" in sol.unassigned_orders

    def test_fragile_order_needing_tag_served_when_vehicle_qualifies(self):
        order = _order(
            "o1",
            required_tags=frozenset({TAG_REFRIGERATED}),
            is_fragile=True,
        )
        vehicle = _vehicle("c1", tags=frozenset({TAG_REFRIGERATED}), allow_fragile=True)
        sol = _solve(order, vehicles=[vehicle])
        assert "o1" not in sol.unassigned_orders

    def test_tagged_order_with_fragile_two_vehicles_correct_one_chosen(self):
        """c1: has tag, no fragile. c2: has tag, allows fragile → must use c2."""
        order = _order(
            "o1",
            required_tags=frozenset({TAG_REFRIGERATED}),
            is_fragile=True,
        )
        vehicles = [
            _vehicle("c1", tags=frozenset({TAG_REFRIGERATED}), allow_fragile=False),
            _vehicle("c2", tags=frozenset({TAG_REFRIGERATED}), allow_fragile=True),
        ]
        sol = _solve(order, vehicles=vehicles)
        assert "o1" not in sol.unassigned_orders
        assert _assigned_courier(sol, "o1") == "c2"
