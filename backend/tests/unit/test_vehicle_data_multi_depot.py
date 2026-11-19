"""Phase 2 plumbing tests — VehicleData/Vehicle carry the new fields cleanly.

Verifies:
  - Defaults preserve back-compat (empty tuple, None end_depot_index).
  - `build_vrp_input` validates allowed_depot_indices + end_depot_index
    against location-list bounds and against each other.
  - `_build_vehicle` translates VehicleData → internal Vehicle correctly
    (`end_node` defaults to start when end_depot_index is None; differs
    when set).
  - `remap_vehicle` remaps every depot index in the new tuple, not just
    the home depot_index.

No feasibility / solver behavior is asserted yet — that's Phase 3+.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.algorithms.rich_vrp.domain.builders import _build_vehicle
from app.algorithms.vrp_solver import (
    OrderData,
    VehicleData,
    build_vrp_input,
)
from app.models.enums import VehicleType
from app.services.dispatch_vrp_helpers import remap_vehicle


def _matrix(n: int) -> list[list[int]]:
    return [[0 if i == j else 100 for j in range(n)] for i in range(n)]


def _vehicle(**overrides) -> VehicleData:
    defaults = dict(
        courier_id="c1",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=1000,
        depot_index=0,
    )
    defaults.update(overrides)
    return VehicleData(**defaults)


def _order(pickup_idx: int, delivery_idx: int) -> OrderData:
    return OrderData(
        order_id="o1",
        pickup_index=pickup_idx,
        delivery_index=delivery_idx,
        weight_grams=10,
        earliest_time_s=0,
        latest_time_s=86400,
    )


def test_vehicle_data_defaults_back_compat():
    v = _vehicle()
    assert v.allowed_depot_indices == ()
    assert v.end_depot_index is None
    assert v.depot_index == 0


def test_build_vrp_input_accepts_empty_multi_depot_defaults():
    locations = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]
    m = _matrix(3)
    vrp = build_vrp_input(
        locations,
        m,
        m,
        [_order(1, 2)],
        [_vehicle()],
    )
    assert vrp.vehicles[0].allowed_depot_indices == ()
    assert vrp.vehicles[0].end_depot_index is None


def test_build_vrp_input_accepts_valid_multi_depot_set():
    locations = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]
    m = _matrix(3)
    v = _vehicle(allowed_depot_indices=(0, 1, 2), end_depot_index=1)
    vrp = build_vrp_input(locations, m, m, [_order(1, 2)], [v])
    assert vrp.vehicles[0].allowed_depot_indices == (0, 1, 2)
    assert vrp.vehicles[0].end_depot_index == 1


def test_build_vrp_input_rejects_out_of_bounds_allowed_depot():
    locations = [(0.0, 0.0), (1.0, 1.0)]
    m = _matrix(2)
    v = _vehicle(allowed_depot_indices=(0, 5))
    with pytest.raises(ValueError, match="allowed_depot_indices contains out-of-bounds"):
        build_vrp_input(locations, m, m, [], [v])


def test_build_vrp_input_rejects_depot_index_not_in_allowed_set():
    locations = [(0.0, 0.0), (1.0, 1.0)]
    m = _matrix(2)
    v = _vehicle(depot_index=0, allowed_depot_indices=(1,))
    with pytest.raises(ValueError, match="must be in allowed_depot_indices"):
        build_vrp_input(locations, m, m, [], [v])


def test_build_vrp_input_rejects_end_depot_outside_allowed_set():
    locations = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]
    m = _matrix(3)
    v = _vehicle(depot_index=0, allowed_depot_indices=(0, 1), end_depot_index=2)
    with pytest.raises(ValueError, match="end_depot_index=2 must be in allowed"):
        build_vrp_input(locations, m, m, [], [v])


def test_build_vrp_input_rejects_out_of_bounds_end_depot():
    locations = [(0.0, 0.0), (1.0, 1.0)]
    m = _matrix(2)
    v = _vehicle(end_depot_index=5)
    with pytest.raises(ValueError, match="end_depot_index=5 out of bounds"):
        build_vrp_input(locations, m, m, [], [v])


def test_build_vehicle_maps_end_depot_index_none_to_start():
    v = _vehicle(depot_index=3)
    vehicle = _build_vehicle(v, idx=0)
    assert vehicle.start_node == 3
    assert vehicle.end_node == 3  # None → start
    assert vehicle.allowed_depot_nodes == ()


def test_build_vehicle_maps_end_depot_index_to_end_node():
    v = _vehicle(
        depot_index=3,
        allowed_depot_indices=(3, 7),
        end_depot_index=7,
    )
    vehicle = _build_vehicle(v, idx=0)
    assert vehicle.start_node == 3
    assert vehicle.end_node == 7
    assert vehicle.allowed_depot_nodes == (3, 7)


def test_remap_vehicle_remaps_every_depot_index():
    v = _vehicle(
        depot_index=2,
        allowed_depot_indices=(2, 5, 8),
        end_depot_index=5,
    )
    # Imagine the location list shrank: 2→0, 5→1, 8→2 in the reduced
    # sub-problem (zone solve).
    index_map = {2: 0, 5: 1, 8: 2}
    remapped = remap_vehicle(v, index_map)
    assert remapped.depot_index == 0
    assert remapped.allowed_depot_indices == (0, 1, 2)
    assert remapped.end_depot_index == 1


def test_remap_vehicle_preserves_single_depot_defaults():
    v = _vehicle(depot_index=4)
    index_map = {4: 0}
    remapped = remap_vehicle(v, index_map)
    assert remapped.depot_index == 0
    assert remapped.allowed_depot_indices == ()
    assert remapped.end_depot_index is None


def test_replace_vehicle_data_preserves_new_fields():
    """`replace()` (used widely in zone-decomposition) must not drop the new fields."""
    v = _vehicle(allowed_depot_indices=(0, 1), end_depot_index=1)
    bumped = replace(v, depot_index=0)
    assert bumped.allowed_depot_indices == (0, 1)
    assert bumped.end_depot_index == 1
