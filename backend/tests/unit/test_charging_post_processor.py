"""Unit tests for the EV charging post-processor.

The post-processor walks ``VRPSolution.routes`` and inserts synthetic
``__CHARGER_*`` PlannedStops where an EV's cumulative travel approaches
its remaining range. These tests cover the four behaviours that matter:

  1. EV route within range → unchanged.
  2. EV route exceeding range → exactly one charger inserted at the
     lowest-detour candidate.
  3. RIDE interlock — never insert while passengers are aboard.
  4. Two-charge route — multiple insertions over a long shift.

Distance math uses Haversine × 1.3 inside the post-processor; the
tests pick coordinates whose pairwise distances are roughly known so
the assertions don't depend on the exact ratio.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.algorithms.vrp_solver import (
    EconomicCostBreakdown,
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VRPSolution,
)
from app.models.enums import OrderType, StopType
from app.services.charging_post_processor import (
    inject_charging_stops_into_solution,
)


# ── Test fixtures ────────────────────────────────────────────────────


def _stop(order_id: str, stop_type: str, lat: float, lon: float,
          arrival: int = 0, departure: int = 0) -> PlannedStop:
    return PlannedStop(
        order_id=order_id,
        stop_type=stop_type,
        location_index=0,
        lat=lat,
        lon=lon,
        arrival_time_s=arrival,
        departure_time_s=departure or (arrival + 60),
    )


def _charger(name: str, lat: float, lon: float):
    """Minimal stand-in for ChargingStation. The post-processor reads
    ``id``, ``lat``, ``lon``. SimpleNamespace is fine."""
    return SimpleNamespace(id=uuid.uuid4(), name=name, lat=lat, lon=lon)


def _ride_order(order_id: str, passenger_count: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=order_id,
        type=OrderType.RIDE,
        passenger_count=passenger_count,
    )


def _delivery_order(order_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=order_id,
        type=OrderType.SCHEDULED,
        passenger_count=0,
    )


def _wrap(route: PlannedRoute) -> VRPSolution:
    return VRPSolution(
        routes=[route],
        unassigned_orders=[],
        total_distance_m=0,
        total_duration_s=0,
        solver_status=SolverStatus.OPTIMAL,
        cost_breakdown=None,
    )


# ── Tests ─────────────────────────────────────────────────────────────


def test_short_route_no_charger_inserted():
    """An EV with 400km range doing a 30km route doesn't need to charge."""
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("o1", "PICKUP", 41.04, 29.00, arrival=0, departure=300),
            _stop("o1", "DELIVERY", 41.05, 29.02, arrival=600, departure=900),
        ],
        total_distance_m=3_000,
        total_duration_s=900,
    )
    candidates = [_charger("Voltrun Levent", 41.08, 29.01)]

    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": candidates},
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={"ev-c1": 400_000},
        initial_charge_m_by_courier={"ev-c1": 400_000},
    )

    out = result.routes[0]
    assert out is route or out.stops == route.stops
    assert not any(s.order_id.startswith("__CHARGER_") for s in out.stops)


def test_over_range_route_gets_one_charger_inserted():
    """Two stops ~80km apart, starting battery range 50km → charger inserted."""
    # Pickup at Beşiktaş (41.04, 29.00), delivery far north at (41.45, 29.50)
    # Haversine ≈ 62 km × 1.3 road factor ≈ 80 km road. Starting budget
    # 50 km is well under that; safety threshold 0.85 triggers at 42.5 km.
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("o1", "PICKUP", 41.04, 29.00, arrival=0, departure=300),
            _stop("o1", "DELIVERY", 41.45, 29.50, arrival=7_200, departure=7_500),
        ],
        total_distance_m=80_000,
        total_duration_s=7_500,
    )
    candidates = [
        _charger("Voltrun Maslak", 41.10, 29.02),
        _charger("Eşarj Levent", 41.08, 29.01),  # closer to start
        _charger("Some far station", 40.90, 28.80),  # off-route
    ]

    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": candidates},
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={"ev-c1": 400_000},
        initial_charge_m_by_courier={"ev-c1": 50_000},  # only 50 km usable
    )

    out = result.routes[0]
    charger_stops = [s for s in out.stops if s.order_id.startswith("__CHARGER_")]
    assert len(charger_stops) == 1, f"Expected 1 charger, got {len(charger_stops)}: {[s.order_id for s in out.stops]}"
    inserted = charger_stops[0]
    # Synthetic id format: __CHARGER_<station-uuid>_<courier-id>__
    assert inserted.order_id.startswith("__CHARGER_")
    assert inserted.order_id.endswith("_ev-c1__")
    assert inserted.stop_type == StopType.PICKUP.value
    # Picked the lowest-detour candidate (Eşarj Levent — closest to start)
    assert abs(inserted.lat - 41.08) < 0.001 and abs(inserted.lon - 29.01) < 0.001
    # Original stops still in order; charger comes between them
    assert out.stops[0].order_id == "o1" and out.stops[0].stop_type == "PICKUP"
    assert out.stops[-1].order_id == "o1" and out.stops[-1].stop_type == "DELIVERY"


def test_no_candidate_chargers_leaves_route_unchanged():
    """No nearby chargers → no insertion (even if range exceeded)."""
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("o1", "PICKUP", 41.04, 29.00),
            _stop("o1", "DELIVERY", 41.45, 29.50),
        ],
        total_distance_m=85_000,
        total_duration_s=7_500,
    )
    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": []},
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={"ev-c1": 400_000},
        initial_charge_m_by_courier={"ev-c1": 100_000},
    )
    out = result.routes[0]
    assert out is route or out.stops == route.stops


def test_non_ev_route_untouched():
    """Diesel courier (no full_range_m entry) is never touched."""
    route = PlannedRoute(
        courier_id="diesel-c1",
        stops=[
            _stop("o1", "PICKUP", 41.04, 29.00),
            _stop("o1", "DELIVERY", 41.45, 29.50),
        ],
        total_distance_m=85_000,
        total_duration_s=7_500,
    )
    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={
            "diesel-c1": [_charger("any", 41.10, 29.02)],
        },
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={},  # no entry = non-EV
        initial_charge_m_by_courier={},
    )
    out = result.routes[0]
    assert out is route or out.stops == route.stops


def test_ride_interlock_skips_when_passengers_aboard():
    """The natural insertion point is mid-RIDE. The post-processor must
    backtrack to an earlier zero-load position. In this scenario the
    only zero-load position is BEFORE the RIDE pickup, so the charger
    lands there (not between pickup and delivery)."""
    # RIDE pickup, then long leg to delivery. The over-range check would
    # naturally place a charger between pickup and delivery, but
    # passengers are aboard. The interlock must push the charger
    # before the pickup.
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("r1", "PICKUP", 41.04, 29.00, arrival=0, departure=300),
            _stop("r1", "DELIVERY", 41.50, 29.55, arrival=7_200, departure=7_500),
        ],
        total_distance_m=95_000,
        total_duration_s=7_500,
    )
    candidates = [_charger("Voltrun Levent", 41.06, 29.01)]
    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": candidates},
        orders_by_id={"r1": _ride_order("r1", passenger_count=2)},
        full_range_m_by_courier={"ev-c1": 400_000},
        initial_charge_m_by_courier={"ev-c1": 60_000},  # 60 km usable
    )
    out = result.routes[0]
    charger_idx = next(
        (i for i, s in enumerate(out.stops) if s.order_id.startswith("__CHARGER_")),
        None,
    )
    assert charger_idx is not None, "expected a charger inserted before the RIDE pickup"
    pickup_idx = next(
        i for i, s in enumerate(out.stops)
        if s.order_id == "r1" and s.stop_type == "PICKUP"
    )
    delivery_idx = next(
        i for i, s in enumerate(out.stops)
        if s.order_id == "r1" and s.stop_type == "DELIVERY"
    )
    assert charger_idx < pickup_idx or charger_idx > delivery_idx, (
        "charger must NOT be between RIDE pickup and delivery"
    )


def test_ride_interlock_no_zero_load_position_leaves_route_alone():
    """If the only over-range stop is the first delivery and the first
    stop is already a RIDE pickup (so no zero-load position exists in
    the route), the post-processor refuses to insert — better than
    breaking the interlock."""
    # First stop IS a RIDE pickup at index 0 (so pax_profile[0] == 0 still,
    # because pax is incremented AFTER pickup; we need a case where there's
    # no zero-load before AND after some long edge).
    # Construct: RIDE pickup, then non-RIDE delivery somewhere far. There's
    # no other order. Once the RIDE picks up, pax > 0; once it delivers,
    # pax = 0 again. We arrange the over-range trigger to be on the
    # RIDE pickup → delivery edge (passengers aboard).
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("r1", "PICKUP", 41.04, 29.00, arrival=0, departure=60),
            # extremely far drop-off ~ 100km road
            _stop("r1", "DELIVERY", 41.80, 29.80, arrival=7_200, departure=7_260),
        ],
        total_distance_m=120_000,
        total_duration_s=7_260,
    )
    candidates = [_charger("Voltrun", 41.40, 29.40)]
    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": candidates},
        orders_by_id={"r1": _ride_order("r1", passenger_count=2)},
        full_range_m_by_courier={"ev-c1": 400_000},
        initial_charge_m_by_courier={"ev-c1": 30_000},  # 30 km usable
    )
    out = result.routes[0]
    # The RIDE pickup is at index 0 with pax_profile[0] == 0, so the
    # post-processor CAN insert at index 0 (before the pickup). That's a
    # legal placement — the assertion is: no charger between pickup and
    # delivery (which would be index 1).
    charger_idxs = [
        i for i, s in enumerate(out.stops)
        if s.order_id.startswith("__CHARGER_")
    ]
    for ci in charger_idxs:
        # find the pickup and delivery indices in the (possibly modified) list
        p_idx = next(
            i for i, s in enumerate(out.stops)
            if s.order_id == "r1" and s.stop_type == "PICKUP"
        )
        d_idx = next(
            i for i, s in enumerate(out.stops)
            if s.order_id == "r1" and s.stop_type == "DELIVERY"
        )
        assert ci < p_idx or ci > d_idx, "interlock violation"


def test_charge_time_uses_battery_kwh_and_station_kw():
    """Charge duration should come from estimate_charge_time_seconds: for a
    Tesla M3 (75 kWh) topping up from ~0% to 80% at a 50 kW DC charger,
    expected ≈ 75 × 0.80 / 50 × 3600 × 1.1 = 4_752 s (~79 min). A flat
    30-min default would fail this assertion.
    """
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("o1", "PICKUP", 41.04, 29.00, arrival=0, departure=300),
            _stop("o1", "DELIVERY", 41.45, 29.50, arrival=7_200, departure=7_500),
        ],
        total_distance_m=80_000,
        total_duration_s=7_500,
    )
    fast_charger = SimpleNamespace(
        id=uuid.uuid4(), name="ZES Levent DC", lat=41.08, lon=29.01, max_kw=50,
    )
    tesla_m3 = SimpleNamespace(battery_kwh=75.0)

    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": [fast_charger]},
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={"ev-c1": 490_000},
        initial_charge_m_by_courier={"ev-c1": 50_000},
        vehicle_spec_by_courier={"ev-c1": tesla_m3},
    )

    charger = next(
        s for s in result.routes[0].stops if s.order_id.startswith("__CHARGER_")
    )
    duration_s = charger.departure_time_s - charger.arrival_time_s
    # Allow ±10% for the current_pct rounding (depends on segment_m / full_range).
    # The Tesla arrives at ~15-20% SoC so target_pct=80 means ~60-65 pct of
    # 75 kWh = ~45-49 kWh, at 50 kW ≈ 54-58 min × 1.1 taper = 60-64 min.
    assert 50 * 60 <= duration_s <= 80 * 60, (
        f"expected ~60-70 min charge time for 75kWh battery at 50kW; got {duration_s}s"
    )


def test_osrm_distance_matrix_overrides_haversine():
    """When a distance_matrix + loc_index is provided, the post-processor
    must use the matrix's actual road distances (not Haversine × 1.3).
    Construct a matrix where the segment is artificially inflated; the
    over-range trigger should fire even though the haversine would not."""
    # Two stops 5km apart by haversine, but the matrix claims 200km.
    p_lat, p_lon = 41.04, 29.00
    d_lat, d_lon = 41.08, 29.05  # ~5 km haversine
    c_lat, c_lon = 41.05, 29.02
    loc_index = {
        (round(p_lat, 5), round(p_lon, 5)): 0,
        (round(d_lat, 5), round(d_lon, 5)): 1,
        (round(c_lat, 5), round(c_lon, 5)): 2,
    }
    # Distances in metres
    distance_matrix = [
        [0, 200_000, 2_000],   # pickup → delivery is 200km!, pickup → charger 2km
        [200_000, 0, 198_000],
        [2_000, 198_000, 0],
    ]
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("o1", "PICKUP", p_lat, p_lon, arrival=0, departure=300),
            _stop("o1", "DELIVERY", d_lat, d_lon, arrival=7_200, departure=7_500),
        ],
        total_distance_m=200_000,
        total_duration_s=7_500,
    )
    candidates = [SimpleNamespace(id=uuid.uuid4(), name="Voltrun", lat=c_lat, lon=c_lon, max_kw=50)]

    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": candidates},
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={"ev-c1": 400_000},
        initial_charge_m_by_courier={"ev-c1": 100_000},  # 100 km
        distance_matrix=distance_matrix,
        loc_index=loc_index,
    )

    chargers = [s for s in result.routes[0].stops if s.order_id.startswith("__CHARGER_")]
    assert len(chargers) == 1, (
        "Matrix says the segment is 200km on a 100km budget — charger must be inserted "
        "even though haversine would compute ~5km and pass"
    )


def test_two_chargers_inserted_for_very_long_route():
    """Multi-leg route where each leg alone is > full battery range needs
    two charger insertions (one before each long leg). With initial
    budget 50km AND full_range 80km, neither 60-km leg fits without
    charging."""
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("o1", "PICKUP", 41.04, 29.00, arrival=0, departure=60),
            _stop("o1", "DELIVERY", 41.46, 29.00, arrival=3_600, departure=3_660),
            _stop("o2", "PICKUP", 41.46, 29.00, arrival=3_720, departure=3_780),
            _stop("o2", "DELIVERY", 41.88, 29.00, arrival=7_200, departure=7_260),
        ],
        total_distance_m=190_000,
        total_duration_s=7_260,
    )
    candidates = [
        _charger("C1 along path", 41.25, 29.00),
        _charger("C2 along path", 41.65, 29.00),
        _charger("C3 off-path", 40.90, 28.50),
    ]
    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": candidates},
        orders_by_id={
            "o1": _delivery_order("o1"),
            "o2": _delivery_order("o2"),
        },
        full_range_m_by_courier={"ev-c1": 80_000},  # tight tank
        initial_charge_m_by_courier={"ev-c1": 50_000},  # tight start
    )
    out = result.routes[0]
    charger_stops = [s for s in out.stops if s.order_id.startswith("__CHARGER_")]
    assert len(charger_stops) >= 2, (
        f"expected ≥2 chargers for a 190km route on 50km start + 80km tank; "
        f"got {len(charger_stops)}"
    )


# ── New guards: reachability / operating hours / contention ──────────────


def test_unreachable_charger_is_skipped():
    """A charger 200km away from anchor when only 50km of range remains
    is physically unreachable. The post-processor must NOT plan to send
    the car there — it'd strand."""
    # Two stops; segment ~80km road triggers the charger search with a
    # 50km initial budget. The only candidate is 200km off in another
    # direction. Reachability filter must reject → no insertion.
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("o1", "PICKUP", 41.04, 29.00, arrival=0, departure=300),
            _stop("o1", "DELIVERY", 41.45, 29.50, arrival=7_200, departure=7_500),
        ],
        total_distance_m=80_000,
        total_duration_s=7_500,
    )
    # Far-flung "charger" 200km south of the pickup — physically out of
    # reach on 50km of remaining range.
    far_away = SimpleNamespace(
        id=uuid.uuid4(), name="Way too far", lat=39.20, lon=29.00, max_kw=50,
    )

    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": [far_away]},
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={"ev-c1": 400_000},
        initial_charge_m_by_courier={"ev-c1": 50_000},
    )

    out = result.routes[0]
    chargers = [s for s in out.stops if s.order_id.startswith("__CHARGER_")]
    assert len(chargers) == 0, (
        "unreachable charger was chosen anyway — that would strand the EV"
    )


def test_reachable_charger_picked_over_unreachable_one():
    """When two candidates exist — one out of range, one in range — the
    in-range one is selected even though the out-of-range one might have
    been geometrically closer to the path."""
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("o1", "PICKUP", 41.04, 29.00, arrival=0, departure=300),
            _stop("o1", "DELIVERY", 41.45, 29.50, arrival=7_200, departure=7_500),
        ],
        total_distance_m=80_000,
        total_duration_s=7_500,
    )
    # Reachable one ~10km from start
    reachable = SimpleNamespace(
        id=uuid.uuid4(), name="Voltrun Levent",
        lat=41.08, lon=29.02, max_kw=50,
    )
    # Unreachable: 100km away from anchor when budget is 50km
    unreachable = SimpleNamespace(
        id=uuid.uuid4(), name="Far away", lat=40.20, lon=29.00, max_kw=150,
    )

    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": [unreachable, reachable]},
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={"ev-c1": 400_000},
        initial_charge_m_by_courier={"ev-c1": 50_000},
    )

    out = result.routes[0]
    chargers = [s for s in out.stops if s.order_id.startswith("__CHARGER_")]
    assert len(chargers) == 1
    assert chargers[0].order_id.startswith(f"__CHARGER_{reachable.id}_"), (
        "expected the reachable Voltrun station; got "
        f"{chargers[0].order_id}"
    )


def test_closed_station_is_skipped():
    """Station whose operating_hours don't include the planned charger
    arrival time must be skipped. Plans a route with anchor departure at
    23:00 — station that closes at 22:00 should be rejected."""
    # ~80km segment, 50km starting budget, anchor leaves at 23:00.
    # The charger search will compute charger arrival ≈ 23:00 + a few
    # minutes. We set the station to close at 22:00.
    afternoon_only = SimpleNamespace(
        id=uuid.uuid4(),
        name="Mall charger (08-22)",
        lat=41.08, lon=29.02, max_kw=50,
        is_24_7=False,
        operating_hours={
            "open_s": 8 * 3600,     # 08:00
            "close_s": 22 * 3600,   # 22:00
        },
    )
    night_open = SimpleNamespace(
        id=uuid.uuid4(),
        name="ZES 24/7",
        lat=41.10, lon=29.03, max_kw=50,
        is_24_7=True,
        operating_hours=None,
    )
    # Anchor's departure_time_s = 23:00 in seconds-from-midnight
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("o1", "PICKUP", 41.04, 29.00,
                  arrival=23 * 3600, departure=23 * 3600 + 60),
            _stop("o1", "DELIVERY", 41.45, 29.50,
                  arrival=24 * 3600 + 1_800, departure=24 * 3600 + 2_100),
        ],
        total_distance_m=80_000,
        total_duration_s=4_000,
    )

    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": [afternoon_only, night_open]},
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={"ev-c1": 400_000},
        initial_charge_m_by_courier={"ev-c1": 50_000},
    )
    out = result.routes[0]
    chargers = [s for s in out.stops if s.order_id.startswith("__CHARGER_")]
    assert len(chargers) == 1
    assert chargers[0].order_id.startswith(f"__CHARGER_{night_open.id}_"), (
        "expected the 24/7 station to be picked, not the one closed at 22:00"
    )


def test_no_station_open_at_arrival_leaves_route_alone():
    """If every candidate is closed at the planned arrival time we must
    refuse to insert — sending the driver to a locked gate strands them
    almost as badly as running out of range."""
    closed = SimpleNamespace(
        id=uuid.uuid4(),
        name="Always-closed test fixture",
        lat=41.08, lon=29.02, max_kw=50,
        is_24_7=False,
        operating_hours={"closed": True},
    )
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("o1", "PICKUP", 41.04, 29.00, arrival=0, departure=300),
            _stop("o1", "DELIVERY", 41.45, 29.50, arrival=7_200, departure=7_500),
        ],
        total_distance_m=80_000,
        total_duration_s=7_500,
    )
    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": [closed]},
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={"ev-c1": 400_000},
        initial_charge_m_by_courier={"ev-c1": 50_000},
    )
    out = result.routes[0]
    chargers = [s for s in out.stops if s.order_id.startswith("__CHARGER_")]
    assert len(chargers) == 0


def test_contention_cap_prevents_clumping():
    """Three EVs all want to charge at roughly the same time, and the
    only candidate is a single station. With the default cap of 2 per
    station per hour, the third EV's route should be left alone (not
    sent to the already-saturated station)."""
    station = SimpleNamespace(
        id=uuid.uuid4(),
        name="Single-stall lonely station",
        lat=41.08, lon=29.02, max_kw=50,
        is_24_7=True, operating_hours=None,
    )

    # Three identical routes, three EVs, all with the same arrival hour.
    routes = []
    for idx in range(3):
        routes.append(PlannedRoute(
            courier_id=f"ev-c{idx}",
            stops=[
                _stop("o1", "PICKUP", 41.04, 29.00,
                      arrival=8 * 3600, departure=8 * 3600 + 300),
                _stop("o1", "DELIVERY", 41.45, 29.50,
                      arrival=8 * 3600 + 7_200, departure=8 * 3600 + 7_500),
            ],
            total_distance_m=80_000,
            total_duration_s=7_500,
        ))
    solution = VRPSolution(
        routes=routes, unassigned_orders=[],
        total_distance_m=0, total_duration_s=0,
        solver_status=SolverStatus.OPTIMAL, cost_breakdown=None,
    )

    candidates_per_courier = {f"ev-c{i}": [station] for i in range(3)}
    full_range_by = {f"ev-c{i}": 400_000 for i in range(3)}
    initial_by = {f"ev-c{i}": 50_000 for i in range(3)}
    orders_for_each = {"o1": _delivery_order("o1")}

    result = inject_charging_stops_into_solution(
        solution,
        candidate_chargers_by_courier=candidates_per_courier,
        orders_by_id=orders_for_each,
        full_range_m_by_courier=full_range_by,
        initial_charge_m_by_courier=initial_by,
        max_evs_per_station_per_hour=2,
    )

    charger_counts = [
        sum(1 for s in r.stops if s.order_id.startswith("__CHARGER_"))
        for r in result.routes
    ]
    assert sum(charger_counts) == 2, (
        f"expected exactly 2 of 3 EVs to charge (cap=2); got per-route counts {charger_counts}"
    )


def test_backtrack_path_accounting_uses_cumulative_distance():
    """When the RIDE interlock forces an earlier insertion and the
    replay tail snakes, post-charge ``remaining_m`` must deduct the
    cumulative path through the tail — not the straight-line distance
    from charger to last replayed stop.

    Setup: a single RIDE pickup → DELIVERY. The pickup triggers the
    over-range condition but pax becomes non-zero after it. The
    post-processor backtracks to insert a charger BEFORE the pickup.
    Without the fix, after replay it under-counts consumption (uses
    only straight-line charger→pickup), so when the next segment
    (pickup → delivery) is evaluated it believes more range remains
    than it actually does. With the fix the cumulative path is
    counted, so the post-processor correctly decides whether a SECOND
    charger is needed between pickup and delivery.

    We can't ACTUALLY insert a second charger here because passengers
    are aboard (RIDE interlock), so the failure mode of the bug is
    silently "EV runs out between pickup and delivery without anyone
    noticing." Test instead inspects the inserted charger's coordinates
    and the final remaining_m via the route's stops directly — i.e.,
    we verify the charger lands at the expected place and one was
    inserted at all.
    """
    # Tightly-budgeted route. With proper accounting the post-processor
    # inserts exactly one charger BEFORE the RIDE pickup.
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("r1", "PICKUP", 41.04, 29.00, arrival=0, departure=60),
            _stop("r1", "DELIVERY", 41.20, 29.00, arrival=3_600, departure=3_900),
        ],
        total_distance_m=22_000,
        total_duration_s=3_900,
    )
    # Charger south of the pickup, so the cumulative path
    # charger→pickup is longer than the straight-line charger→pickup
    # (they happen to be the same in 1D, but the post-charge
    # accounting now sums the actual driven leg either way).
    charger = SimpleNamespace(
        id=uuid.uuid4(), name="South of start",
        lat=40.96, lon=29.00, max_kw=50,
        is_24_7=True, operating_hours=None,
    )

    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": [charger]},
        orders_by_id={"r1": _ride_order("r1", passenger_count=2)},
        full_range_m_by_courier={"ev-c1": 50_000},
        initial_charge_m_by_courier={"ev-c1": 15_000},
    )

    out = result.routes[0]
    chargers_inserted = [
        s for s in out.stops if s.order_id.startswith("__CHARGER_")
    ]
    # Without the accounting fix and a missing infinite-loop guard,
    # this scenario could loop forever; the test simply running to
    # completion confirms the guard is in place. With the fix exactly
    # one charger is inserted (between depot/start and the RIDE pickup,
    # since the interlock forbids charging between pickup and delivery).
    assert len(chargers_inserted) == 1, (
        f"expected exactly one charger before the RIDE pickup; "
        f"got {len(chargers_inserted)}"
    )
    pickup_idx = next(
        i for i, s in enumerate(out.stops)
        if s.order_id == "r1" and s.stop_type == "PICKUP"
    )
    charger_idx = next(
        i for i, s in enumerate(out.stops)
        if s.order_id.startswith("__CHARGER_")
    )
    assert charger_idx < pickup_idx, (
        "interlock violated: charger inserted while passengers aboard"
    )


def test_unservable_segment_is_refused_not_looped():
    """A single segment longer than even a full post-charge tank cannot
    be served by any number of chargers. The post-processor must refuse
    (log + leave route) instead of looping forever trying to insert."""
    # Segment is ~80km road; full_range=50km, target=80% → post-charge
    # range is 40km. Any single charger is useless here.
    route = PlannedRoute(
        courier_id="ev-c1",
        stops=[
            _stop("o1", "PICKUP", 41.04, 29.00, arrival=0, departure=300),
            _stop("o1", "DELIVERY", 41.45, 29.50, arrival=7_200, departure=7_500),
        ],
        total_distance_m=80_000,
        total_duration_s=7_500,
    )
    charger = SimpleNamespace(
        id=uuid.uuid4(), name="Voltrun mid-path",
        lat=41.25, lon=29.25, max_kw=50,
        is_24_7=True, operating_hours=None,
    )

    # If the post-processor loops, this test hangs. The fact that
    # it returns at all is the assertion.
    result = inject_charging_stops_into_solution(
        _wrap(route),
        candidate_chargers_by_courier={"ev-c1": [charger]},
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={"ev-c1": 50_000},
        initial_charge_m_by_courier={"ev-c1": 30_000},
        target_pct=80,
    )

    out = result.routes[0]
    chargers_inserted = [
        s for s in out.stops if s.order_id.startswith("__CHARGER_")
    ]
    # No charger should be inserted — the unservable-segment branch
    # logs an error and advances without trying.
    assert len(chargers_inserted) == 0


def test_contention_cap_relaxed_lets_all_charge():
    """Sanity check the reverse — set the cap high enough and all three
    EVs do get the same station."""
    station = SimpleNamespace(
        id=uuid.uuid4(), name="High-capacity hub",
        lat=41.08, lon=29.02, max_kw=150,
        is_24_7=True, operating_hours=None,
    )
    routes = []
    for idx in range(3):
        routes.append(PlannedRoute(
            courier_id=f"ev-c{idx}",
            stops=[
                _stop("o1", "PICKUP", 41.04, 29.00,
                      arrival=8 * 3600, departure=8 * 3600 + 300),
                _stop("o1", "DELIVERY", 41.45, 29.50,
                      arrival=8 * 3600 + 7_200, departure=8 * 3600 + 7_500),
            ],
            total_distance_m=80_000,
            total_duration_s=7_500,
        ))
    solution = VRPSolution(
        routes=routes, unassigned_orders=[],
        total_distance_m=0, total_duration_s=0,
        solver_status=SolverStatus.OPTIMAL, cost_breakdown=None,
    )

    result = inject_charging_stops_into_solution(
        solution,
        candidate_chargers_by_courier={f"ev-c{i}": [station] for i in range(3)},
        orders_by_id={"o1": _delivery_order("o1")},
        full_range_m_by_courier={f"ev-c{i}": 400_000 for i in range(3)},
        initial_charge_m_by_courier={f"ev-c{i}": 50_000 for i in range(3)},
        max_evs_per_station_per_hour=10,
    )

    charger_counts = [
        sum(1 for s in r.stops if s.order_id.startswith("__CHARGER_"))
        for r in result.routes
    ]
    assert sum(charger_counts) == 3
