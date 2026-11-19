"""A/B verification: do traffic priors actually change the solver's choices?

Constructs a real VRPInput for an Istanbul delivery problem (depot + 15
stops in cells with priors), gets a real OSRM duration matrix, then
runs `solve()` twice:
  - Run BASELINE: OSRM durations as-is (prior bypassed)
  - Run WITH-PRIOR: durations adjusted via `adjust_duration_matrix`

Reports the diff between solutions:
  * total generalized cost
  * total duration / distance
  * route assignment (which orders ended up on which vehicle)
  * stop sequences
  * unassigned orders

Runs the comparison at two times of day to demonstrate the prior's
direction-of-effect — rush hour adds cost, quiet hour leaves it alone.

This is the headline evidence that the prior is wired all the way
through the solver's objective and decision points, not just stored
in a table that nothing consumes.

Usage (in api container):
    python -m scripts.verify_traffic_prior_ab
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import app.models.vehicle_tag  # noqa: F401
from app.algorithms import osrm_client
from app.algorithms.vrp_solver import (
    OrderData,
    SolverStatus,
    VRPInput,
    VRPSolution,
    VehicleData,
    solve,
)
from app.config import settings
from app.database import async_session
from app.models.enums import VehicleType
from app.services import traffic_profile_service

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")

# Depot near Eyüp on the European side.
DEPOT_LAT, DEPOT_LON = 41.0500, 28.9333

# 15 real Istanbul delivery coordinates — mix of European + Asian side,
# core (high prior) + outskirts (lower prior).
DELIVERY_COORDS: list[tuple[float, float]] = [
    (41.0082, 28.9784),  # Sultanahmet
    (41.0431, 29.0275),  # Beşiktaş
    (41.0900, 29.0500),  # Levent
    (40.9826, 29.0286),  # Üsküdar
    (40.9885, 29.0260),  # Kadıköy
    (41.0700, 28.9000),  # Mecidiyeköy
    (40.9530, 29.1200),  # Bostancı
    (41.1170, 29.0500),  # Maslak
    (41.0185, 28.9647),  # Eminönü
    (41.0364, 28.9869),  # Karaköy
    (40.9923, 29.0273),  # Salacak
    (41.0633, 28.9881),  # Şişli core
    (41.0136, 29.0414),  # Beylerbeyi (Asian)
    (41.0850, 28.8767),  # Cendere area
    (41.1050, 29.0273),  # Etiler
]

# Working day window: 09:00 - 21:00 local (in seconds from midnight)
SHIFT_START_S = 9 * 3600
SHIFT_END_S = 21 * 3600


def _build_input(
    durations: list[list[int]],
    distances: list[list[int]],
    locations: list[tuple[float, float]],
) -> VRPInput:
    num_orders = len(DELIVERY_COORDS)
    orders = [
        OrderData(
            order_id=f"ord-{i:02d}",
            pickup_index=0,            # all from depot
            delivery_index=i + 1,
            pickup_is_depot=True,
            weight_grams=3_000,
            volume_cm3=10_000,
            package_count=1,
            earliest_time_s=SHIFT_START_S,
            latest_time_s=SHIFT_END_S,
            hard_latest_time_s=SHIFT_END_S,
            service_time_s=300,        # 5 min service
            unserved_penalty=10_000,
        )
        for i in range(num_orders)
    ]
    vehicles = [
        VehicleData(
            courier_id=f"crr-{v}",
            vehicle_type=VehicleType.MOTORCYCLE if v == 0 else VehicleType.CAR_VAN,
            capacity_grams=100_000,
            depot_index=0,
            start_time_s=SHIFT_START_S,
            end_time_s=SHIFT_END_S,
            hard_end_time_s=SHIFT_END_S,
            max_distance_m=0,
            max_stops=15,
            fixed_route_cost=0,
            distance_cost_per_km=10,
            labor_cost_per_minute=30,
            fuel_cost_per_km=15,
            osrm_profile="driving",
        )
        for v in range(3)
    ]
    return VRPInput(
        locations=locations,
        orders=orders,
        vehicles=vehicles,
        distance_matrix=distances,
        duration_matrix=durations,
        duration_matrices={"driving": durations},
    )


def _solution_assignments(sol: VRPSolution) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for route in sol.routes:
        out[route.courier_id] = [stop.order_id for stop in route.stops]
    return out


def _summarize(sol: VRPSolution, label: str) -> dict:
    return {
        "label": label,
        "solver_status": sol.solver_status.value if isinstance(sol.solver_status, SolverStatus) else str(sol.solver_status),
        "total_distance_m": sol.total_distance_m,
        "total_duration_s": sol.total_duration_s,
        "assigned_orders": sum(len(r.stops) for r in sol.routes),
        "unassigned_orders": list(sol.unassigned_orders),
        "route_count": len(sol.routes),
        "assignments": _solution_assignments(sol),
    }


def _diff_assignments(a: dict[str, list[str]], b: dict[str, list[str]]) -> dict:
    """Per-courier diff between the two solutions' stop sequences."""
    couriers = sorted(set(a.keys()) | set(b.keys()))
    out = {}
    for c in couriers:
        seq_a = a.get(c, [])
        seq_b = b.get(c, [])
        out[c] = {
            "baseline_sequence": seq_a,
            "with_prior_sequence": seq_b,
            "same": seq_a == seq_b,
            "added": [s for s in seq_b if s not in seq_a],
            "removed": [s for s in seq_a if s not in seq_b],
        }
    return out


async def _ab_at_time(
    db,
    *,
    base_durations: list[list[int]],
    base_distances: list[list[int]],
    coords: list[tuple[float, float]],
    at_time: datetime,
    label: str,
) -> dict:
    # Force prior ON, compute adjusted matrix
    settings.TRAFFIC_PRIOR_ENABLED = True
    traffic_profile_service.invalidate_lookup_cache()
    adjusted = await traffic_profile_service.adjust_duration_matrix(
        db, coords=coords, durations=base_durations, at_time=at_time,
    )

    # Build VRPInputs
    baseline_input = _build_input(base_durations, base_distances, coords)
    with_prior_input = _build_input(adjusted.durations, base_distances, coords)

    # Run solver on both — small time limit since the problem is small
    baseline_sol = solve(baseline_input, time_limit_seconds=3)
    with_prior_sol = solve(with_prior_input, time_limit_seconds=3)

    base_summary = _summarize(baseline_sol, "BASELINE")
    prior_summary = _summarize(with_prior_sol, "WITH_PRIOR")
    diff = _diff_assignments(
        _solution_assignments(baseline_sol),
        _solution_assignments(with_prior_sol),
    )

    duration_delta_pct = (
        (with_prior_sol.total_duration_s - baseline_sol.total_duration_s)
        / baseline_sol.total_duration_s * 100.0
        if baseline_sol.total_duration_s else 0.0
    )
    distance_delta_pct = (
        (with_prior_sol.total_distance_m - baseline_sol.total_distance_m)
        / baseline_sol.total_distance_m * 100.0
        if baseline_sol.total_distance_m else 0.0
    )

    return {
        "label": label,
        "at_time_local": at_time.isoformat(),
        "adjust_metrics": {
            "applied": adjusted.applied,
            "fleet_hits": adjusted.fleet_hits,
            "prior_hits": adjusted.prior_hits,
            "profile_misses": adjusted.profile_misses,
            "avg_multiplier": adjusted.avg_multiplier,
        },
        "baseline": base_summary,
        "with_prior": prior_summary,
        "delta": {
            "total_duration_s_delta": with_prior_sol.total_duration_s - baseline_sol.total_duration_s,
            "total_duration_pct": round(duration_delta_pct, 2),
            "total_distance_m_delta": with_prior_sol.total_distance_m - baseline_sol.total_distance_m,
            "total_distance_pct": round(distance_delta_pct, 2),
            "assignments_differ": diff,
            "any_route_resequenced": any(not d["same"] for d in diff.values()),
        },
    }


async def main() -> None:
    coords = [(DEPOT_LAT, DEPOT_LON)] + DELIVERY_COORDS

    async with async_session() as db:
        # One OSRM call for both runs — matrix is independent of the time.
        print(f"Fetching OSRM matrix for {len(coords)} points...")
        matrix = await osrm_client.get_distance_matrix(coords, profile="driving")
        base_durations = matrix.durations
        base_distances = matrix.distances
        print(f"  matrix source: {matrix.source}")

        # Tuesday 18:00 local — rush hour
        rush = datetime(2026, 5, 26, 18, 0, tzinfo=ISTANBUL_TZ)
        # Sunday 04:00 local — quiet
        quiet = datetime(2026, 5, 24, 4, 0, tzinfo=ISTANBUL_TZ)

        rush_result = await _ab_at_time(
            db,
            base_durations=base_durations,
            base_distances=base_distances,
            coords=coords,
            at_time=rush,
            label="Tuesday 18:00 Istanbul (rush hour)",
        )
        quiet_result = await _ab_at_time(
            db,
            base_durations=base_durations,
            base_distances=base_distances,
            coords=coords,
            at_time=quiet,
            label="Sunday 04:00 Istanbul (quiet)",
        )

        # Restore default
        settings.TRAFFIC_PRIOR_ENABLED = True
        traffic_profile_service.invalidate_lookup_cache()

    payload = {
        "rush_hour": rush_result,
        "quiet_hour": quiet_result,
    }
    print()
    print(json.dumps(payload, indent=2, default=str))

    # Verdict
    print()
    rush_pct = rush_result["delta"]["total_duration_pct"]
    quiet_pct = quiet_result["delta"]["total_duration_pct"]
    rush_resequence = rush_result["delta"]["any_route_resequenced"]
    rush_pass = rush_pct >= 5.0 or rush_resequence
    quiet_pass = abs(quiet_pct) <= 5.0
    print(f"VERDICT: rush_hour duration Δ = {rush_pct:+.1f}% (≥5% or resequenced expected) → {'PASS' if rush_pass else 'FAIL'}")
    print(f"         rush_hour route resequenced = {rush_resequence}")
    print(f"         quiet_hour duration Δ = {quiet_pct:+.1f}% (within ±5% expected) → {'PASS' if quiet_pass else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
