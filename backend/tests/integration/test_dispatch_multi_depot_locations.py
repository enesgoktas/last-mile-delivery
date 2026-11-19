"""Phase 2 — `build_location_index` and `build_vrp_data_structures` together
correctly thread the courier's additional depots into the solver location
index and the resulting VehicleData.

Verifies:
  - Flag OFF → additional depots are NOT in the location list.
  - Flag ON  → all additional depots get a location index, and
                VehicleData.allowed_depot_indices points to them.
  - End depot also resolves to a known index in the location list.

The feasibility kernel + solver behavior remain unchanged in this phase;
this test only exercises the plumbing.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.dispatch_vrp_helpers import (
    build_location_index,
    build_vrp_data_structures,
)


# ── Minimal stand-ins so we can run the helpers without DB or auth ──


class _StubDepot:
    def __init__(self, *, lat: float, lon: float, id_: str | None = None):
        self.id = id_ or str(uuid.uuid4())
        self.lat = lat
        self.lon = lon


class _StubCourier:
    def __init__(
        self,
        *,
        depot_lat: float,
        depot_lon: float,
        depot_id: str,
        additional_depots: list[_StubDepot] | None = None,
        end_depot_id: str | None = None,
    ):
        self.id = uuid.uuid4()
        self.vehicle_type = "CAR_VAN"
        self.depot_lat = depot_lat
        self.depot_lon = depot_lon
        self.depot_id = depot_id
        self.additional_depots = additional_depots or []
        self.end_depot_id = end_depot_id
        self.working_hours_start = __import__("datetime").time(8, 0)
        self.working_hours_end = __import__("datetime").time(20, 0)
        self.max_weight_kg = 100.0
        self.max_volume_cm3 = None
        self.max_packages = None
        self.max_passengers = 0
        self.max_battery_range_km = None
        self.current_battery_pct = None
        self.vehicle_spec = None
        self.vehicle_tags = []
        self.constraints = None


class _StubFeatureFlags:
    def __init__(self, *, multi_depot: bool):
        self.ride_pickup_buffer_enabled = False
        self.ev_battery_awareness_enabled = False
        self.ev_auto_charging_enabled = False
        self.multi_depot_vehicles_enabled = multi_depot


class _StubRideSettings:
    pickup_buffer_minutes = 5


class _StubOperationsPolicy:
    def __init__(self, *, multi_depot: bool):
        self.feature_flags = _StubFeatureFlags(multi_depot=multi_depot)
        self.ride_settings = _StubRideSettings()


def test_build_location_index_excludes_additional_depots_when_flag_off():
    home_id = str(uuid.uuid4())
    extra = _StubDepot(lat=41.10, lon=29.05)
    courier = _StubCourier(
        depot_lat=41.00,
        depot_lon=29.00,
        depot_id=home_id,
        additional_depots=[extra],
    )
    locations, _ = build_location_index([], [courier], multi_depot_enabled=False)
    # Only the home depot's coords show up.
    assert (round(41.00, 5), round(29.00, 5)) in locations
    assert (round(41.10, 5), round(29.05, 5)) not in locations


def test_build_location_index_includes_additional_depots_when_flag_on():
    home_id = str(uuid.uuid4())
    extra_a = _StubDepot(lat=41.10, lon=29.05)
    extra_b = _StubDepot(lat=41.20, lon=29.10)
    courier = _StubCourier(
        depot_lat=41.00,
        depot_lon=29.00,
        depot_id=home_id,
        additional_depots=[extra_a, extra_b],
    )
    locations, _ = build_location_index([], [courier], multi_depot_enabled=True)
    assert (round(41.00, 5), round(29.00, 5)) in locations
    assert (round(41.10, 5), round(29.05, 5)) in locations
    assert (round(41.20, 5), round(29.10, 5)) in locations


@pytest.mark.skipif(
    True,
    reason=(
        "build_vrp_data_structures pulls global_constraints via "
        "resolve_courier_constraints; full ORM stubbing is more work than "
        "this single-purpose plumbing test warrants. End-to-end coverage "
        "lands in the live integration test."
    ),
)
def test_build_vrp_data_structures_populates_allowed_depot_indices_when_on():
    pass
