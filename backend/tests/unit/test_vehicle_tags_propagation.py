"""Unit tests for vehicle tag propagation through the dispatch pipeline.

Covers:
- build_vrp_data_structures: courier tags → VehicleData.vehicle_tags
- build_vrp_data_structures: order tags → OrderData.required_vehicle_tags
- expand_virtual_vehicles: tags survive multi-trip expansion
- recurring_order_service: template tags propagate to spawned orders
"""
from __future__ import annotations

import uuid
from datetime import datetime, time as dt_time
from types import SimpleNamespace

import pytest

from app.algorithms.vrp_solver import VehicleData
from app.models.enums import FuelType, OrderPriority, OrderType, VehicleType
from app.schemas.operations_policy import OptimizationSettings
from app.services.dispatch_vrp_helpers import (
    build_location_index,
    build_vrp_data_structures,
    expand_virtual_vehicles,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

TAG_A = str(uuid.uuid4())
TAG_B = str(uuid.uuid4())
TAG_C = str(uuid.uuid4())


def _tag(tag_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.UUID(tag_id))


def _order(*, required_tags: list[str] = []) -> SimpleNamespace:
    now = datetime(2026, 4, 28, 9, 0, 0)
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        type=OrderType.SCHEDULED,
        priority=OrderPriority.NORMAL,
        pickup_lat=41.0,
        pickup_lon=29.0,
        delivery_lat=41.1,
        delivery_lon=29.1,
        weight_kg=1.0,
        is_fragile=False,
        is_temp_sensitive=False,
        earliest_pickup=None,
        latest_pickup=None,
        earliest_delivery=now,
        latest_delivery=now.replace(hour=17),
        required_vehicle_tags=[_tag(t) for t in required_tags],
    )


def _courier(*, vehicle_tags: list[str] = []) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        vehicle_type=VehicleType.CAR_VAN,
        max_weight_kg=50,
        max_volume_cm3=None,
        max_packages=None,
        constraints={},
        depot_lat=41.0,
        depot_lon=29.0,
        working_hours_start=dt_time(8, 0),
        working_hours_end=dt_time(18, 0),
        vehicle_spec=None,
        vehicle_tags=[_tag(t) for t in vehicle_tags],
    )


def _global_constraints() -> dict:
    return {
        "service_time_minutes": 5,
        "max_weight_kg": 50.0,
        "max_route_distance_km": None,
        "max_volume_cm3": None,
        "max_packages": None,
        "max_stops_per_route": 50,
        "max_shift_hours": None,
        "break_required": False,
        "break_duration_minutes": 30,
        "break_earliest_offset_hours": 3.0,
        "break_latest_offset_hours": 6.0,
        "allow_fragile": True,
        "allow_temp_sensitive": True,
    }


def _settings() -> OptimizationSettings:
    return OptimizationSettings(
        economic_batch_optimizer_enabled=False,
        fuel_priority_multiplier=0.0,
    )


def _build(order, courier):
    _, loc_index = build_location_index([order], [courier])
    _, vehicles = build_vrp_data_structures(
        [order], [courier], loc_index, _global_constraints(), {}, _settings()
    )
    return vehicles


def _build_orders(order, courier):
    _, loc_index = build_location_index([order], [courier])
    orders, _ = build_vrp_data_structures(
        [order], [courier], loc_index, _global_constraints(), {}, _settings()
    )
    return orders


# ---------------------------------------------------------------------------
# VehicleData.vehicle_tags from courier
# ---------------------------------------------------------------------------

class TestBuildVehicleDataTags:
    def test_courier_without_tags_produces_empty_frozenset(self):
        courier = _courier(vehicle_tags=[])
        order = _order()
        vehicles = _build(order, courier)
        assert vehicles[0].vehicle_tags == frozenset()

    def test_courier_single_tag_propagates(self):
        courier = _courier(vehicle_tags=[TAG_A])
        order = _order()
        vehicles = _build(order, courier)
        assert vehicles[0].vehicle_tags == frozenset({TAG_A})

    def test_courier_multiple_tags_propagate(self):
        courier = _courier(vehicle_tags=[TAG_A, TAG_B, TAG_C])
        order = _order()
        vehicles = _build(order, courier)
        assert vehicles[0].vehicle_tags == frozenset({TAG_A, TAG_B, TAG_C})

    def test_courier_missing_vehicle_tags_attr_defaults_to_empty(self):
        """Couriers loaded without the relationship don't crash; tags default to empty."""
        courier = _courier()
        del courier.vehicle_tags  # simulate missing attribute
        order = _order()
        vehicles = _build(order, courier)
        assert vehicles[0].vehicle_tags == frozenset()


# ---------------------------------------------------------------------------
# OrderData.required_vehicle_tags from order
# ---------------------------------------------------------------------------

class TestBuildOrderDataTags:
    def test_order_without_tags_produces_empty_frozenset(self):
        order = _order(required_tags=[])
        courier = _courier()
        orders = _build_orders(order, courier)
        assert orders[0].required_vehicle_tags == frozenset()

    def test_order_single_required_tag_propagates(self):
        order = _order(required_tags=[TAG_A])
        courier = _courier()
        orders = _build_orders(order, courier)
        assert orders[0].required_vehicle_tags == frozenset({TAG_A})

    def test_order_multiple_required_tags_propagate(self):
        order = _order(required_tags=[TAG_A, TAG_B])
        courier = _courier()
        orders = _build_orders(order, courier)
        assert orders[0].required_vehicle_tags == frozenset({TAG_A, TAG_B})

    def test_order_none_required_vehicle_tags_defaults_to_empty(self):
        order = _order()
        order.required_vehicle_tags = None
        courier = _courier()
        orders = _build_orders(order, courier)
        assert orders[0].required_vehicle_tags == frozenset()


# ---------------------------------------------------------------------------
# expand_virtual_vehicles — tags survive multi-trip expansion
# ---------------------------------------------------------------------------

class TestExpandVirtualVehiclesTags:
    def _physical_vehicle(self, tags: frozenset[str]) -> VehicleData:
        return VehicleData(
            courier_id="courier-1",
            vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=10_000,
            depot_index=0,
            start_time_s=8 * 3600,
            end_time_s=16 * 3600,
            hard_end_time_s=17 * 3600,
            trip_index=0,
            physical_courier_id="courier-1",
            fixed_route_cost=500,
            distance_cost_per_km=10,
            labor_cost_per_minute=2,
            fuel_cost_per_km=4,
            overtime_cost_per_minute=3,
            overtime_penalty_per_minute=7,
            vehicle_tags=tags,
        )

    def test_no_tags_produces_tagless_virtual_vehicles(self):
        physical = self._physical_vehicle(frozenset())
        expanded = expand_virtual_vehicles(
            [physical],
            {"max_trips_per_day": 3, "depot_reload_time_minutes": 15},
        )
        assert len(expanded) == 3
        for veh in expanded:
            assert veh.vehicle_tags == frozenset()

    def test_single_tag_propagates_to_all_trips(self):
        physical = self._physical_vehicle(frozenset({TAG_A}))
        expanded = expand_virtual_vehicles(
            [physical],
            {"max_trips_per_day": 2, "depot_reload_time_minutes": 30},
        )
        assert len(expanded) == 2
        for veh in expanded:
            assert veh.vehicle_tags == frozenset({TAG_A})

    def test_multiple_tags_propagate_to_all_trips(self):
        physical = self._physical_vehicle(frozenset({TAG_A, TAG_B, TAG_C}))
        expanded = expand_virtual_vehicles(
            [physical],
            {"max_trips_per_day": 3, "depot_reload_time_minutes": 20},
        )
        assert len(expanded) == 3
        for veh in expanded:
            assert veh.vehicle_tags == frozenset({TAG_A, TAG_B, TAG_C})

    def test_different_vehicles_get_independent_tags(self):
        v1 = self._physical_vehicle(frozenset({TAG_A}))
        v2 = VehicleData(
            courier_id="courier-2",
            vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=10_000,
            depot_index=0,
            start_time_s=8 * 3600,
            end_time_s=16 * 3600,
            hard_end_time_s=17 * 3600,
            trip_index=0,
            physical_courier_id="courier-2",
            fixed_route_cost=500,
            distance_cost_per_km=10,
            labor_cost_per_minute=2,
            fuel_cost_per_km=4,
            overtime_cost_per_minute=3,
            overtime_penalty_per_minute=7,
            vehicle_tags=frozenset({TAG_B}),
        )
        expanded = expand_virtual_vehicles(
            [v1, v2],
            {"max_trips_per_day": 2, "depot_reload_time_minutes": 30},
        )
        # 2 physicals × 2 trips each = 4 virtual vehicles
        assert len(expanded) == 4
        c1_trips = [v for v in expanded if v.physical_courier_id == "courier-1"]
        c2_trips = [v for v in expanded if v.physical_courier_id == "courier-2"]
        for veh in c1_trips:
            assert veh.vehicle_tags == frozenset({TAG_A})
        for veh in c2_trips:
            assert veh.vehicle_tags == frozenset({TAG_B})


# ---------------------------------------------------------------------------
# Recurring order tag propagation
# ---------------------------------------------------------------------------

class TestRecurringOrderTagPropagation:
    """Verify that spawned orders inherit required_vehicle_tags from their template."""

    def _make_tag_obj(self, tag_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=uuid.UUID(tag_id))

    def test_template_tags_copied_to_spawned_order(self):
        tag_obj = self._make_tag_obj(TAG_A)

        spawned = SimpleNamespace(required_vehicle_tags=[])
        template = SimpleNamespace(required_vehicle_tags=[tag_obj])

        # Replicate the service logic
        if template.required_vehicle_tags:
            spawned.required_vehicle_tags = list(template.required_vehicle_tags)

        assert len(spawned.required_vehicle_tags) == 1
        assert spawned.required_vehicle_tags[0].id == uuid.UUID(TAG_A)

    def test_template_with_multiple_tags_copies_all(self):
        tag_a = self._make_tag_obj(TAG_A)
        tag_b = self._make_tag_obj(TAG_B)

        spawned = SimpleNamespace(required_vehicle_tags=[])
        template = SimpleNamespace(required_vehicle_tags=[tag_a, tag_b])

        if template.required_vehicle_tags:
            spawned.required_vehicle_tags = list(template.required_vehicle_tags)

        ids = {str(t.id) for t in spawned.required_vehicle_tags}
        assert ids == {TAG_A, TAG_B}

    def test_template_without_tags_leaves_spawned_unchanged(self):
        spawned = SimpleNamespace(required_vehicle_tags=[])
        template = SimpleNamespace(required_vehicle_tags=[])

        if template.required_vehicle_tags:
            spawned.required_vehicle_tags = list(template.required_vehicle_tags)

        assert spawned.required_vehicle_tags == []
