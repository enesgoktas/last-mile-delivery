"""Phase 5 — end-at-different-depot support.

A `VehicleData` with `end_depot_index != depot_index` should:
  - Produce an OR-Tools layout with `starts[i] = 2*i`, `ends[i] = 2*i+1`
    where the end slot resolves to the end-depot's physical location.
  - Drive the human-dispatcher baseline and `route_plan.simulate_route_plan`
    return-leg distance/duration to the end depot (not home).
  - Propagate to ONLY the last virtual trip when `expand_virtual_vehicles`
    splits the courier for multi-trip dispatch — intermediate trips
    return to the home depot for reload.

Single-depot vehicles (`end_depot_index is None`) keep pre-Phase-5
behavior bit-identically: start = end = home depot.
"""
from __future__ import annotations

from app.algorithms.rich_vrp.domain.builders import _build_vehicle
from app.algorithms.vrp_solver import VehicleData
from app.models.enums import VehicleType
from app.services.dispatch_vrp_helpers import expand_virtual_vehicles


def _vehicle(**overrides) -> VehicleData:
    defaults = dict(
        courier_id="c1",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=1000,
        depot_index=0,
        start_time_s=0,
        end_time_s=10 * 3600,
    )
    defaults.update(overrides)
    return VehicleData(**defaults)


def test_single_depot_vehicle_end_node_equals_start_node():
    v = _vehicle(depot_index=4)
    internal = _build_vehicle(v, idx=0)
    assert internal.start_node == 4
    assert internal.end_node == 4


def test_multi_depot_end_node_differs_from_start_node():
    v = _vehicle(
        depot_index=4,
        allowed_depot_indices=(4, 9),
        end_depot_index=9,
    )
    internal = _build_vehicle(v, idx=0)
    assert internal.start_node == 4
    assert internal.end_node == 9


def test_expand_virtual_vehicles_propagates_end_depot_to_last_trip_only():
    """Multi-trip expansion: only the FINAL virtual trip carries the
    end_depot_index. Earlier trips have end_depot_index=None so the
    inter-trip DEPOT_RETURN can reload at the home depot."""
    physical = _vehicle(
        depot_index=4,
        allowed_depot_indices=(4, 9),
        end_depot_index=9,
        start_time_s=8 * 3600,
        end_time_s=20 * 3600,
    )
    global_constraints = {
        "max_trips_per_day": 3,
        "depot_reload_time_minutes": 15,
    }
    expanded = expand_virtual_vehicles([physical], global_constraints)

    # With a 12h window, max_trips=3, reload=15min, slice = (12h - 2×15min) / 3
    # ≈ 3.83h per trip → 3 effective trips.
    assert len(expanded) >= 2  # at least one split (3 trips expected)
    # Earlier trips must NOT propagate end_depot.
    for trip in expanded[:-1]:
        assert trip.end_depot_index is None, (
            f"Trip {trip.trip_index}: end_depot_index leaked to non-last trip"
        )
    # The last trip MUST carry end_depot_index.
    last_trip = expanded[-1]
    assert last_trip.end_depot_index == 9
    # All trips share the allowed-depot tuple.
    for trip in expanded:
        assert trip.allowed_depot_indices == (4, 9)


def test_expand_virtual_vehicles_with_no_end_depot_preserves_none_for_all_trips():
    """Sanity: a single-depot vehicle (end_depot_index=None) stays None
    across every virtual trip — no spurious end-depot value gets injected."""
    physical = _vehicle(
        depot_index=4,
        start_time_s=8 * 3600,
        end_time_s=20 * 3600,
    )
    expanded = expand_virtual_vehicles(
        [physical],
        {"max_trips_per_day": 3, "depot_reload_time_minutes": 15},
    )
    for trip in expanded:
        assert trip.end_depot_index is None
        # `allowed_depot_indices` also stays empty for back-compat.
        assert trip.allowed_depot_indices == ()
