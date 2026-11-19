"""Pure unit tests for the range bulk dispatch helpers.

The full `dispatch_range` flow needs OSRM + DB and is exercised by the live
integration test. These pure tests pin the small but easy-to-misuse helpers
that translate between absolute datetimes and range-relative seconds, and the
courier-per-day expansion that gives the solver one virtual vehicle per
(courier, day).
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.algorithms.vrp_solver import PlannedRoute, PlannedStop, VehicleData
from app.models.enums import VehicleType
from app.services import dispatch_range_service as svc


def test_datetime_to_range_seconds_clamps_negative_to_zero():
    anchor = datetime(2026, 4, 27)
    before_anchor = datetime(2026, 4, 26, 23, 59, 0)
    assert svc.datetime_to_range_seconds(before_anchor, anchor) == 0


def test_datetime_to_range_seconds_round_trips_through_helper():
    anchor = datetime(2026, 4, 27)
    target = datetime(2026, 4, 30, 14, 30, 15)
    seconds = svc.datetime_to_range_seconds(target, anchor)
    # 3 days × 86400 + 14×3600 + 30×60 + 15 = 311_415
    assert seconds == 3 * 86_400 + 14 * 3600 + 30 * 60 + 15
    assert svc.range_seconds_to_datetime(seconds, anchor) == target


def _vehicle(start_h: int, end_h: int) -> VehicleData:
    return VehicleData(
        courier_id="courier-uuid-abc",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=20_000,
        depot_index=0,
        start_time_s=start_h * 3600,
        end_time_s=end_h * 3600,
        hard_end_time_s=(end_h + 1) * 3600,
        physical_courier_id="courier-uuid-abc",
        fixed_route_cost=2_500,
        break_earliest_s=12 * 3600,
        break_latest_s=14 * 3600,
        break_duration_s=30 * 60,
    )


def test_expand_couriers_per_day_emits_one_virtual_vehicle_per_day():
    vehicles = svc._expand_couriers_per_day(
        [_vehicle(start_h=8, end_h=18)],
        span_days=3,
        anchor=datetime(2026, 4, 27),
    )
    assert len(vehicles) == 3
    assert [v.trip_index for v in vehicles] == [0, 1, 2]
    # Day 0: shift = 8h..18h relative to anchor (32_400..64_800)
    assert vehicles[0].start_time_s == 8 * 3600
    assert vehicles[0].end_time_s == 18 * 3600
    # Day 2: shift = (2*86400 + 8*3600)..(2*86400 + 18*3600)
    assert vehicles[2].start_time_s == 2 * 86_400 + 8 * 3600
    assert vehicles[2].end_time_s == 2 * 86_400 + 18 * 3600
    # `physical_courier_id` is preserved; `courier_id` carries the day suffix.
    assert vehicles[0].physical_courier_id == "courier-uuid-abc"
    assert vehicles[2].courier_id == "courier-uuid-abc__day2"


def test_expand_couriers_only_charges_fixed_cost_on_first_day():
    """A courier should not pay the activation cost N times across the range —
    if the solver already commits to using them on day 0 the rest of the range
    is free of fixed_route_cost."""
    vehicles = svc._expand_couriers_per_day(
        [_vehicle(start_h=8, end_h=18)],
        span_days=4,
        anchor=datetime(2026, 4, 27),
    )
    assert vehicles[0].fixed_route_cost == 2_500
    assert all(v.fixed_route_cost == 0 for v in vehicles[1:])


def test_expand_couriers_offsets_break_window_per_day():
    vehicles = svc._expand_couriers_per_day(
        [_vehicle(start_h=8, end_h=18)],
        span_days=2,
        anchor=datetime(2026, 4, 27),
    )
    # Day 1 break window must fall on day 1, not day 0 again.
    assert vehicles[1].break_earliest_s == 86_400 + 12 * 3600
    assert vehicles[1].break_latest_s == 86_400 + 14 * 3600


def test_route_service_date_uses_virtual_vehicle_day_suffix():
    route = PlannedRoute(
        courier_id="11111111-1111-1111-1111-111111111111__day3",
        stops=[
            PlannedStop(
                order_id="ord-1",
                stop_type="DELIVERY",
                location_index=1,
                lat=41.0,
                lon=29.0,
                arrival_time_s=8 * 3600,
                departure_time_s=9 * 3600,
            )
        ],
        total_distance_m=1_000,
        total_duration_s=600,
        route_start_time_s=8 * 3600,
        route_end_time_s=9 * 3600,
    )

    assert svc._route_service_date(route, datetime(2026, 4, 27)) == datetime(2026, 4, 30).date()


def test_resolve_range_time_limit_scales_with_span_days_within_tier():
    one_day = svc._resolve_range_time_limit(50, span_days=1)
    seven_days = svc._resolve_range_time_limit(50, span_days=7)
    fourteen_days = svc._resolve_range_time_limit(50, span_days=14)
    # Same tier, more days → more search budget (until the ceiling clamps).
    assert seven_days >= one_day
    assert fourteen_days >= seven_days
    # The premium is bounded by the small-tier ceiling.
    assert fourteen_days <= 240


def test_resolve_range_time_limit_jumps_across_tiers():
    # Small tier: <=14d AND <=100 orders.
    quick_small = svc._resolve_range_time_limit(50, span_days=3)
    # Full small tier: <=14d AND <=500 orders.
    full_small = svc._resolve_range_time_limit(300, span_days=3)
    # Medium tier: <=30d AND <=1500 orders.
    medium = svc._resolve_range_time_limit(1000, span_days=20)
    # Large fallback (the chunker would normally take over before here).
    large = svc._resolve_range_time_limit(5000, span_days=60)
    # Each tier transition gives the solver strictly more budget.
    assert quick_small < full_small < medium < large


def test_resolve_range_time_limit_respects_tier_ceiling():
    # 14-day span × 1500 orders sits at the medium-tier ceiling.
    medium_max = svc._resolve_range_time_limit(1500, span_days=30)
    assert medium_max <= 900
    # 90-day × 5000 orders falls into the large fallback ceiling.
    large_max = svc._resolve_range_time_limit(5000, span_days=90)
    assert large_max <= 1800


def test_re_anchor_orders_clamps_degenerate_windows():
    """An order whose latest_delivery is exactly the range start needs a tiny
    feasible window so the solver can attempt it (rather than implicitly
    rejecting it because earliest >= latest)."""
    from app.algorithms.vrp_solver import OrderData

    anchor = datetime(2026, 4, 27)
    fake_orders = [
        SimpleNamespace(
            id="ord-1",
            earliest_delivery=anchor,
            latest_delivery=anchor,  # same instant
            earliest_pickup=None,
            latest_pickup=None,
        )
    ]
    src = OrderData(
        order_id="ord-1",
        pickup_index=0,
        delivery_index=1,
        weight_grams=1000,
        earliest_time_s=0,
        latest_time_s=0,
    )
    rebuilt = svc._re_anchor_orders(fake_orders, [src], anchor=anchor, span_days=2)
    assert rebuilt[0].earliest_time_s == 0
    # 1 hour clamp
    assert rebuilt[0].latest_time_s == 3600
