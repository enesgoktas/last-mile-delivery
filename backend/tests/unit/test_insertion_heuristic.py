"""Unit tests for the insertion heuristic."""

import pytest

from app.algorithms.insertion_heuristic import (
    EligibleCourier,
    ExistingStop,
    NewOrder,
    find_best_insertion,
)
from app.models.enums import (
    OrderPriority,
    VehicleType,
)
from tests.helpers.insertion_data import (
    make_courier as _make_courier,
)
from tests.helpers.insertion_data import (
    make_existing_stop as _make_existing_stop,
)
from tests.helpers.insertion_data import (
    make_order as _make_order,
)
from tests.helpers.insertion_data import (
    mock_matrix_provider as _mock_matrix_provider,
)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInsertionNoEligible:
    """Cases where no insertion is possible."""

    @pytest.mark.asyncio
    async def test_no_couriers(self):
        """Empty courier list → no_eligible_courier."""
        result = await find_best_insertion(
            _make_order(), [], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is None
        assert result.reason == "no_eligible_courier"

    @pytest.mark.asyncio
    async def test_all_couriers_over_capacity(self):
        """Only courier is at full capacity → no candidate."""
        courier = _make_courier(
            capacity_grams=1000,
            current_load=900,
            route_id=None,  # no active route
        )
        order = _make_order(weight_grams=500)  # 900 + 500 > 1000
        result = await find_best_insertion(
            order, [courier], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is None

    @pytest.mark.asyncio
    async def test_fragile_motorcycle_only(self):
        """Fragile order + only motorcycle courier → filtered out."""
        courier = _make_courier(
            vehicle_type=VehicleType.MOTORCYCLE,
            route_id=None,
            allow_fragile=False,
        )
        order = _make_order(is_fragile=True)
        result = await find_best_insertion(
            order, [courier], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is None

    def test_validate_candidate_rejects_volume_overflow(self):
        """Direct unit test: an insertion that bursts the volume cap at any
        intermediate position must be rejected even when weight + packages
        are well under their caps. Pre-the-fix this returned True because
        only weight was tracked through the route walk."""
        from app.algorithms.insertion_heuristic import ExistingStop, _validate_candidate

        existing = ExistingStop(
            lat=41.005, lon=29.005,
            earliest_s=0, latest_s=86_400,
            arrival_s=3_600, service_s=300,
            weight_delta_g=1_000,
            volume_delta_cm3=80_000,
            package_delta=1,
        )
        courier = EligibleCourier(
            courier_id="c", vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=50_000, capacity_volume_cm3=100_000, max_packages=20,
            current_load_grams=0, current_load_volume_cm3=0, current_load_packages=0,
            depot_lat=41.0, depot_lon=29.0,
            start_time_s=0, end_time_s=86_400,
            route_id="r1", current_stops=[existing],
        )
        order = NewOrder(
            order_id="o1",
            pickup_lat=41.01, pickup_lon=29.01,
            delivery_lat=41.02, delivery_lon=29.02,
            weight_grams=500, volume_cm3=30_000, package_count=1,
            earliest_s=0, latest_s=86_400,
            pickup_earliest_s=0, pickup_latest_s=86_400,
            priority=OrderPriority.NORMAL, service_time_s=300,
        )
        # Trivial 3-node travel matrix: depot=0, existing=1, pickup=2, delivery=3
        travel_s = [[0]*4 for _ in range(4)]
        # Position (0, 2): pickup_new at start → existing_pickup → delivery_new
        # Volume profile: 30k → 110k (over cap) → 80k. Must reject.
        ok = _validate_candidate(
            courier=courier, stops=[existing], order=order, travel_s=travel_s,
            base_departures=[300], base_loads=[1_000],
            base_volumes=[80_000], base_packages=[1], base_passengers=[0],
            pickup_pos=0, delivery_pos=2,
            pickup_node=2, delivery_node=3,
        )
        assert ok is False, "volume cap violation must be rejected by _validate_candidate"

    def test_validate_candidate_rejects_package_overflow(self):
        """Symmetric to volume case but for the package_count dimension."""
        from app.algorithms.insertion_heuristic import ExistingStop, _validate_candidate

        existing = ExistingStop(
            lat=41.005, lon=29.005,
            earliest_s=0, latest_s=86_400,
            arrival_s=3_600, service_s=300,
            weight_delta_g=500, volume_delta_cm3=1_000, package_delta=8,
        )
        courier = EligibleCourier(
            courier_id="c", vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=50_000, capacity_volume_cm3=500_000, max_packages=10,
            current_load_grams=0, current_load_volume_cm3=0, current_load_packages=0,
            depot_lat=41.0, depot_lon=29.0,
            start_time_s=0, end_time_s=86_400,
            route_id="r1", current_stops=[existing],
        )
        order = NewOrder(
            order_id="o1",
            pickup_lat=41.01, pickup_lon=29.01,
            delivery_lat=41.02, delivery_lon=29.02,
            weight_grams=500, volume_cm3=1_000, package_count=5,
            earliest_s=0, latest_s=86_400,
            pickup_earliest_s=0, pickup_latest_s=86_400,
            priority=OrderPriority.NORMAL, service_time_s=300,
        )
        travel_s = [[0]*4 for _ in range(4)]
        # Position (0, 2): pickup_new(5) → existing_pickup(13 > cap=10) → reject
        ok = _validate_candidate(
            courier=courier, stops=[existing], order=order, travel_s=travel_s,
            base_departures=[300], base_loads=[500],
            base_volumes=[1_000], base_packages=[8], base_passengers=[0],
            pickup_pos=0, delivery_pos=2,
            pickup_node=2, delivery_node=3,
        )
        assert ok is False, "package cap violation must be rejected by _validate_candidate"

    def test_validate_candidate_accepts_when_all_dims_fit(self):
        """Sanity check the same harness — a strictly-fitting insertion is accepted."""
        from app.algorithms.insertion_heuristic import ExistingStop, _validate_candidate

        existing = ExistingStop(
            lat=41.005, lon=29.005,
            earliest_s=0, latest_s=86_400,
            arrival_s=3_600, service_s=300,
            weight_delta_g=1_000, volume_delta_cm3=10_000, package_delta=1,
        )
        courier = EligibleCourier(
            courier_id="c", vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=50_000, capacity_volume_cm3=100_000, max_packages=20,
            current_load_grams=0, current_load_volume_cm3=0, current_load_packages=0,
            depot_lat=41.0, depot_lon=29.0,
            start_time_s=0, end_time_s=86_400,
            route_id="r1", current_stops=[existing],
        )
        order = NewOrder(
            order_id="o1",
            pickup_lat=41.01, pickup_lon=29.01,
            delivery_lat=41.02, delivery_lon=29.02,
            weight_grams=500, volume_cm3=5_000, package_count=1,
            earliest_s=0, latest_s=86_400,
            pickup_earliest_s=0, pickup_latest_s=86_400,
            priority=OrderPriority.NORMAL, service_time_s=300,
        )
        travel_s = [[0]*4 for _ in range(4)]
        ok = _validate_candidate(
            courier=courier, stops=[existing], order=order, travel_s=travel_s,
            base_departures=[300], base_loads=[1_000],
            base_volumes=[10_000], base_packages=[1], base_passengers=[0],
            pickup_pos=0, delivery_pos=1,
            pickup_node=2, delivery_node=3,
        )
        assert ok is True


class TestInsertionNewRoute:
    """Creating new routes for idle couriers."""

    @pytest.mark.asyncio
    async def test_insert_idle_courier_creates_new_route(self):
        """Courier with no active route → new_route."""
        courier = _make_courier(route_id=None)
        order = _make_order()
        result = await find_best_insertion(
            order, [courier], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is not None
        assert result.reason == "new_route"
        assert result.candidate.route_id is None
        assert result.candidate.courier_id == "c1"

    @pytest.mark.asyncio
    async def test_nearest_courier_preferred(self):
        """Among 2 idle couriers, the nearer one should have lower cost."""
        near = _make_courier(courier_id="near", depot_lat=41.005,
                             depot_lon=29.005, route_id=None)
        far = _make_courier(courier_id="far", depot_lat=42.0,
                            depot_lon=30.0, route_id=None)
        order = _make_order()
        result = await find_best_insertion(
            order, [near, far], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is not None
        assert result.candidate.courier_id == "near"


class TestInsertionExistingRoute:
    """Inserting into routes with existing stops."""

    @pytest.mark.asyncio
    async def test_insert_into_route_with_one_stop(self):
        """Route with 1 existing stop → can insert pickup + delivery."""
        stop = _make_existing_stop()
        courier = _make_courier(current_stops=[stop], current_load=1000)
        order = _make_order(weight_grams=2000)
        result = await find_best_insertion(
            order, [courier], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is not None
        assert result.reason == "inserted"
        assert result.candidate.route_id == "route-1"

    @pytest.mark.asyncio
    async def test_priority_weighting_urgent(self):
        """URGENT orders should have lower weighted cost (0.5x)."""
        stop = _make_existing_stop()
        courier = _make_courier(current_stops=[stop], current_load=0)
        normal = _make_order(order_id="normal", priority=OrderPriority.NORMAL)
        urgent = _make_order(order_id="urgent", priority=OrderPriority.URGENT)

        res_normal = await find_best_insertion(
            normal, [courier], matrix_provider=_mock_matrix_provider,
        )
        res_urgent = await find_best_insertion(
            urgent, [courier], matrix_provider=_mock_matrix_provider,
        )

        if res_normal.candidate and res_urgent.candidate:
            assert res_urgent.candidate.weighted_cost <= res_normal.candidate.weighted_cost

    @pytest.mark.asyncio
    async def test_prefers_new_route_when_existing_insertion_is_more_expensive(self):
        far_stop = _make_existing_stop(lat=42.0, lon=30.0)
        far_route_courier = _make_courier(
            courier_id="far-route",
            depot_lat=42.0,
            depot_lon=30.0,
            current_stops=[far_stop],
            route_id="route-far",
        )
        near_idle_courier = _make_courier(
            courier_id="near-idle",
            depot_lat=41.0,
            depot_lon=29.0,
            current_stops=[],
            route_id=None,
        )
        order = _make_order(
            pickup_lat=41.001,
            pickup_lon=29.001,
            delivery_lat=41.002,
            delivery_lon=29.002,
        )

        result = await find_best_insertion(
            order, [far_route_courier, near_idle_courier],
            matrix_provider=_mock_matrix_provider,
        )

        assert result.candidate is not None
        assert result.reason == "new_route"
        assert result.candidate.courier_id == "near-idle"
        assert result.candidate.route_id is None


class TestInsertionTimeWindows:
    """Time window validation during insertion."""

    @pytest.mark.asyncio
    async def test_expired_window_no_candidate(self):
        """Order with a window that's already passed → no insertion."""
        courier = EligibleCourier(
            courier_id="c1",
            vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=50_000,
            depot_lat=41.0,
            depot_lon=29.0,
            start_time_s=80000,  # starts very late
            end_time_s=86400,
            route_id=None,
            current_stops=[],
            current_load_grams=0,
        )
        # Order must be delivered before 100s
        order = NewOrder(
            order_id="o_expired",
            pickup_lat=41.5, pickup_lon=29.5,
            delivery_lat=42.0, delivery_lon=30.0,
            weight_grams=1000, is_fragile=False, is_temp_sensitive=False,
            earliest_s=0, latest_s=100,
            pickup_earliest_s=0, pickup_latest_s=100,
            priority=OrderPriority.NORMAL,
            service_time_s=300,
        )
        result = await find_best_insertion(
            order, [courier], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is None


class TestInsertionRide:
    """RIDE-aware constraints in the insertion heuristic."""

    def _ride(self, passenger_count: int) -> NewOrder:
        return NewOrder(
            order_id="ride1",
            pickup_lat=41.01, pickup_lon=29.01,
            delivery_lat=41.02, delivery_lon=29.02,
            weight_grams=0,
            earliest_s=0, latest_s=86_400,
            pickup_earliest_s=0, pickup_latest_s=86_400,
            priority=OrderPriority.NORMAL,
            service_time_s=300,
            is_ride=True,
            passenger_count=passenger_count,
        )

    def _car(self, *, max_passengers: int, current_load_passengers: int = 0,
             current_stops: list | None = None, route_id: str | None = None,
             ) -> EligibleCourier:
        return EligibleCourier(
            courier_id="c1",
            vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=50_000,
            depot_lat=41.0, depot_lon=29.0,
            start_time_s=0, end_time_s=86_400,
            route_id=route_id,
            current_stops=current_stops or [],
            current_load_grams=0,
            max_passengers=max_passengers,
            current_load_passengers=current_load_passengers,
        )

    @pytest.mark.asyncio
    async def test_ride_rejects_vehicle_with_no_passenger_capacity(self):
        """RIDE order must reject vehicles whose max_passengers is 0 — they're
        not configured for passenger transport at all."""
        courier = self._car(max_passengers=0, route_id=None)
        order = self._ride(passenger_count=2)
        result = await find_best_insertion(
            order, [courier], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is None
        assert result.rejection_summary.get("capacity_passengers", 0) == 1

    @pytest.mark.asyncio
    async def test_ride_rejects_vehicle_with_too_few_seats(self):
        """10 passengers cannot ride a 4-seater — the whole point of the user
        feedback. Must reject."""
        courier = self._car(max_passengers=4, route_id=None)
        order = self._ride(passenger_count=10)
        result = await find_best_insertion(
            order, [courier], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is None
        assert result.rejection_summary.get("capacity_passengers", 0) == 1

    @pytest.mark.asyncio
    async def test_ride_accepts_when_seats_match(self):
        """RIDE that fits exactly should be assignable to a new route."""
        courier = self._car(max_passengers=4, route_id=None)
        order = self._ride(passenger_count=4)
        result = await find_best_insertion(
            order, [courier], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is not None
        assert result.reason == "new_route"

    @pytest.mark.asyncio
    async def test_ride_rejects_when_existing_passengers_leave_no_room(self):
        """Vehicle is already carrying 3 passengers on an in-flight pickup; a
        new 2-passenger RIDE would overflow even though max_passengers=4."""
        existing_pickup = ExistingStop(
            lat=41.005, lon=29.005,
            earliest_s=0, latest_s=86_400,
            arrival_s=3_600, service_s=300,
            weight_delta_g=0,
            passenger_delta=3,
        )
        existing_delivery = ExistingStop(
            lat=41.008, lon=29.008,
            earliest_s=0, latest_s=86_400,
            arrival_s=7_200, service_s=300,
            weight_delta_g=0,
            passenger_delta=-3,
        )
        courier = self._car(
            max_passengers=4,
            current_load_passengers=3,
            current_stops=[existing_pickup, existing_delivery],
            route_id="r1",
        )
        order = self._ride(passenger_count=2)
        result = await find_best_insertion(
            order, [courier], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is None

    @pytest.mark.asyncio
    async def test_non_ride_order_not_blocked_by_zero_max_passengers(self):
        """Regression: a normal package delivery on a vehicle with
        max_passengers=0 must still be assignable — the RIDE filter must not
        leak into non-RIDE flows."""
        courier = self._car(max_passengers=0, route_id=None)
        package = NewOrder(
            order_id="pkg1",
            pickup_lat=41.01, pickup_lon=29.01,
            delivery_lat=41.02, delivery_lon=29.02,
            weight_grams=2_000,
            earliest_s=0, latest_s=86_400,
            pickup_earliest_s=0, pickup_latest_s=86_400,
            priority=OrderPriority.NORMAL,
            service_time_s=300,
            is_ride=False,
            passenger_count=0,
        )
        result = await find_best_insertion(
            package, [courier], matrix_provider=_mock_matrix_provider,
        )
        assert result.candidate is not None

    @pytest.mark.asyncio
    async def test_split_ride_siblings_land_on_separate_vehicles(self):
        """End-to-end check on the hardest mid-day scenario:

        A 10-passenger RIDE arrives mid-day. The split distribution for
        per_vehicle=4 is [4, 3, 3] — three siblings, three cars needed. Each
        car in the fleet is a 4-seater. The siblings share one pickup
        location and one pickup window (they came from one parent RIDE), so
        the heuristic cannot defer a sibling until after another's delivery.
        Dispatched sequentially, every sibling must land on a *different*
        car; otherwise a sedan would be told to carry 7 passengers at the
        shared pickup time, which is the failure mode the user called out.
        """
        from dataclasses import replace

        # Realistic shared pickup window for an airport-style group
        # pickup: 09:00 ± 15 min. Delivery anywhere up to 10:30.
        PICKUP_EARLIEST_S = 9 * 3_600           # 09:00
        PICKUP_LATEST_S = 9 * 3_600 + 15 * 60   # 09:15
        DELIVERY_EARLIEST_S = 9 * 3_600 + 30 * 60   # 09:30
        DELIVERY_LATEST_S = 10 * 3_600 + 30 * 60    # 10:30
        SCHEDULED_ARRIVAL_S = 9 * 3_600         # 09:00

        # Three idle 4-seater couriers at slightly different depot
        # locations. After the first sibling is assigned to one of them,
        # the test mirrors _apply_on_demand_insertion by attaching the
        # pickup+delivery stops with realistic time windows so the next
        # call sees the in-flight route correctly.
        couriers = [
            EligibleCourier(
                courier_id=f"c{idx}",
                vehicle_type=VehicleType.CAR_VAN,
                capacity_grams=50_000,
                depot_lat=41.0 + 0.001 * idx,
                depot_lon=29.0 + 0.001 * idx,
                start_time_s=0, end_time_s=86_400,
                route_id=None,
                current_stops=[],
                current_load_grams=0,
                max_passengers=4,
                current_load_passengers=0,
            )
            for idx in range(3)
        ]

        siblings = [
            NewOrder(
                order_id=f"ride-sibling-{idx}",
                pickup_lat=41.01, pickup_lon=29.01,
                delivery_lat=41.02, delivery_lon=29.02,
                weight_grams=0,
                earliest_s=DELIVERY_EARLIEST_S, latest_s=DELIVERY_LATEST_S,
                pickup_earliest_s=PICKUP_EARLIEST_S, pickup_latest_s=PICKUP_LATEST_S,
                priority=OrderPriority.NORMAL,
                service_time_s=300,
                is_ride=True,
                passenger_count=pax,
            )
            for idx, pax in enumerate([4, 3, 3])
        ]

        assignments: list[str] = []

        for sibling in siblings:
            result = await find_best_insertion(
                sibling, couriers, matrix_provider=_mock_matrix_provider,
            )
            assert result.candidate is not None, (
                f"Sibling {sibling.order_id} ({sibling.passenger_count} pax) could not be "
                f"placed — fleet has 3 × 4-seat cars and {len(assignments)} are already used"
            )
            assignments.append(result.candidate.courier_id)

            picked_idx = next(
                i for i, c in enumerate(couriers)
                if c.courier_id == result.candidate.courier_id
            )
            pickup_stop = ExistingStop(
                lat=sibling.pickup_lat, lon=sibling.pickup_lon,
                earliest_s=PICKUP_EARLIEST_S, latest_s=PICKUP_LATEST_S,
                arrival_s=SCHEDULED_ARRIVAL_S, service_s=300,
                weight_delta_g=0,
                passenger_delta=sibling.passenger_count,
            )
            delivery_stop = ExistingStop(
                lat=sibling.delivery_lat, lon=sibling.delivery_lon,
                earliest_s=DELIVERY_EARLIEST_S, latest_s=DELIVERY_LATEST_S,
                arrival_s=SCHEDULED_ARRIVAL_S + 30 * 60, service_s=300,
                weight_delta_g=0,
                passenger_delta=-sibling.passenger_count,
            )
            couriers[picked_idx] = replace(
                couriers[picked_idx],
                route_id=f"route-{picked_idx}",
                current_stops=[pickup_stop, delivery_stop],
            )

        # All three siblings placed.
        assert len(assignments) == 3
        # Each on its own vehicle — "10 passengers at a single location, as a
        # single pickup, and you send over a single sedan and can only pick
        # up 4 of them" is the failure mode the user named, and it must not
        # happen.
        assert len(set(assignments)) == 3, (
            f"Expected 3 distinct couriers, got {assignments} — at least two "
            f"siblings collided on one vehicle, exceeding its 4-seat capacity "
            f"at the shared pickup time"
        )
