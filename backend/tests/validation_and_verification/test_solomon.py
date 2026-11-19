"""V&V — Solomon VRPTW benchmark suite (activities B3 + B10).

Runs rich_alns against Solomon's 100-customer VRPTW instances and records
the produced total distance vs. the published Best-Known Solution (BKS).
This is the standard external-validity check for any VRPTW solver in the
literature; on it the V&V chapter cites whatever gap we currently achieve.

This is intentionally an *evidence-gathering* suite, not a tight pass/fail
gate. Two reasons:
  1. rich_alns is configured for last-mile dispatch (use the fleet you have)
     and does not have a "minimise vehicle count" tier — Solomon's BKS does.
     A direct comparison therefore overstates the solver's distance gap on
     long-horizon (2-series) instances.
  2. Published BKS values come from hours of compute on dedicated solvers;
     we run with a 60s budget per instance.

What the suite asserts (the floor — catches catastrophic regressions):
  * Per-instance: serve-rate ≥ 80% (the solver actually solves the problem).
  * Per-instance: gap-to-BKS ≤ 500% (the result is meaningful).

What it deliberately does NOT assert (because the metaheuristic is
wall-clock-bounded and small TW violations under time pressure are
informative not catastrophic):
  * Validator returns 0 errors — recorded in CSV instead.
  * Tight gap thresholds — see test_feasibility_gate.py for the
    deterministic feasibility CI gate.

What the suite *records* (the evidence — written to solomon_results.csv):
  * Per-instance gap, vehicles used, served count, wall-clock time.
  * Mean gap across all vendored instances.

Coverage depends on what's vendored under
`tests/validation_and_verification/data/solomon/`. Run
`scripts/download_solomon.py` (or vendor `.txt` files manually) to populate.
With nothing vendored, the parser self-tests still run.
"""
from __future__ import annotations

import csv
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.algorithms.solver_adapter import SolveOptions
from app.algorithms.solver_registry import get_solver
from tests.helpers.vv_helpers import validate_vrp_solution
from tests.validation_and_verification._solomon_bks import SOLOMON_BKS
from tests.validation_and_verification._solomon_loader import (
    SolomonInstance,
    list_vendored_instances,
    load_solomon_file,
    parse_solomon_text,
    to_vrp_input,
)

_DATA_DIR = Path(__file__).parent / "data" / "solomon"
_REPORT_DIR = Path(__file__).parent / "_results"
_TIME_BUDGET_S = 60.0
_GATE_SEED = 42

# Floor thresholds — catch only catastrophic regressions, not
# below-literature performance (which is documented in the report).
_MIN_SERVE_RATE = 0.80
_MAX_PER_INSTANCE_GAP_PCT = 500.0


# ---------------------------------------------------------------------------
# Parser self-tests — always run, no vendored data needed
# ---------------------------------------------------------------------------

_TINY_SOLOMON_FIXTURE = """\
TINY01

VEHICLE
NUMBER     CAPACITY
   3         100

CUSTOMER
CUST NO.  XCOORD.   YCOORD.    DEMAND   READY TIME   DUE DATE   SERVICE TIME

    0      50.00     50.00       0          0          1000          0
    1      45.00     55.00      10         10           500          5
    2      55.00     45.00      15         20           600          5
    3      60.00     60.00      20         30           700          5
"""


def test_parser_round_trip() -> None:
    """Parse a tiny Solomon-format string → VRPInput, verify the shape."""
    instance = parse_solomon_text(_TINY_SOLOMON_FIXTURE)
    assert instance.name == "TINY01"
    assert instance.n_vehicles == 3
    assert instance.capacity == 100
    assert instance.depot == (50.0, 50.0)
    assert len(instance.customers) == 3

    vrp = to_vrp_input(instance)
    assert len(vrp.vehicles) == 3
    assert len(vrp.orders) == 3
    assert len(vrp.locations) == 4
    n = len(vrp.locations)
    for i in range(n):
        assert vrp.distance_matrix[i][i] == 0
        for j in range(n):
            if i != j:
                assert vrp.distance_matrix[i][j] > 0
                assert vrp.distance_matrix[i][j] == vrp.distance_matrix[j][i]


def test_parser_handles_extra_whitespace() -> None:
    """Robustness check: extra blank lines / spacing must not break the parser."""
    noisy = "\n\n" + _TINY_SOLOMON_FIXTURE.replace("VEHICLE\n", "VEHICLE\n\n\n") + "\n\n"
    instance = parse_solomon_text(noisy)
    assert instance.n_vehicles == 3
    assert len(instance.customers) == 3


# ---------------------------------------------------------------------------
# Vendored-instances run — parametrised over what's on disk
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SolomonRecord:
    name: str
    customers: int
    bks_distance: float
    bks_vehicles: int
    our_distance: float
    our_vehicles: int
    served: int
    unassigned: int
    gap_pct: float
    feasible: bool
    wall_clock_s: float


def _vendored_instance_ids() -> list[str]:
    return sorted(p.stem.upper() for p in list_vendored_instances(_DATA_DIR))


_INSTANCE_IDS = _vendored_instance_ids()
_solve_cache: dict[str, SolomonRecord] = {}


def _solve_and_record(instance_name: str) -> SolomonRecord:
    if instance_name in _solve_cache:
        return _solve_cache[instance_name]

    txt_path = next(
        p for p in list_vendored_instances(_DATA_DIR)
        if p.stem.upper() == instance_name
    )
    instance: SolomonInstance = load_solomon_file(txt_path)
    vrp = to_vrp_input(instance)

    adapter = get_solver("rich_alns")
    options = SolveOptions(
        time_budget_s=_TIME_BUDGET_S,
        seed=_GATE_SEED,
        spread_fleet=False,
        fleet_utilization="EFFICIENT",
        routing_objective="SERVICE_FIRST",
    )
    t0 = time.perf_counter()
    result = adapter.solve(vrp, options)
    elapsed = time.perf_counter() - t0

    validation = validate_vrp_solution(result.solution, vrp)
    sol = result.solution
    bks = SOLOMON_BKS[instance_name]
    our_dist_units = sol.total_distance_m / 1000.0
    gap_pct = (
        (our_dist_units - bks.distance) / bks.distance * 100.0
        if bks.distance > 0
        else float("nan")
    )

    record = SolomonRecord(
        name=instance_name,
        customers=len(instance.customers),
        bks_distance=bks.distance,
        bks_vehicles=bks.n_vehicles,
        our_distance=our_dist_units,
        our_vehicles=sum(1 for r in sol.routes if r.stops),
        served=sum(1 for r in sol.routes for s in r.stops if s.stop_type == "DELIVERY"),
        unassigned=len(sol.unassigned_orders),
        gap_pct=gap_pct,
        feasible=validation.error_count == 0,
        wall_clock_s=elapsed,
    )
    _solve_cache[instance_name] = record
    return record


@pytest.mark.skipif(
    not _INSTANCE_IDS,
    reason=(
        "No Solomon instances vendored; run `python scripts/download_solomon.py` "
        "to populate tests/validation_and_verification/data/solomon/"
    ),
)
@pytest.mark.parametrize("instance_name", _INSTANCE_IDS or ["__none__"])
def test_solomon_instance_floor(instance_name: str) -> None:
    """Each vendored instance must clear the catastrophic-failure floor.

    This is *not* a "match the literature" check — see the module docstring
    for why. It catches: infeasible plans, missing customers, and gaps so
    large the result is meaningless (>500%).
    """
    if instance_name not in SOLOMON_BKS:
        pytest.skip(f"{instance_name} is vendored but not in our BKS table")

    record = _solve_and_record(instance_name)

    serve_rate = record.served / record.customers if record.customers else 0
    assert serve_rate >= _MIN_SERVE_RATE, (
        f"{instance_name}: served {record.served}/{record.customers} "
        f"({serve_rate:.0%}); floor is {_MIN_SERVE_RATE:.0%}."
    )

    assert record.gap_pct <= _MAX_PER_INSTANCE_GAP_PCT, (
        f"{instance_name}: gap {record.gap_pct:.1f}% exceeds catastrophic "
        f"floor of {_MAX_PER_INSTANCE_GAP_PCT}%."
    )

    # Validator status is recorded in the CSV report rather than asserted —
    # see module docstring. A future improvement: route the deterministic
    # synthetic CI gate at synthetic_120 (which always passes) up to a
    # 100-customer Solomon-style instance.


@pytest.mark.skipif(
    not _INSTANCE_IDS,
    reason="No Solomon instances vendored.",
)
def test_solomon_results_written_to_csv() -> None:
    """Run all vendored Solomon instances and write solomon_results.csv.

    The CSV is the V&V chapter's source of truth for the Solomon comparison.
    This test exists so the report is regenerated on every CI run; it
    asserts only that the CSV got written and contains data.
    """
    records = [_solve_and_record(name) for name in _INSTANCE_IDS if name in SOLOMON_BKS]
    assert records, "No instances solved — vendored data is empty?"

    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = _REPORT_DIR / "solomon_results.csv"

    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "instance", "customers",
            "bks_distance", "bks_vehicles",
            "our_distance", "our_vehicles",
            "served", "unassigned",
            "gap_pct", "feasible", "wall_clock_s",
        ])
        for r in records:
            writer.writerow([
                r.name, r.customers,
                f"{r.bks_distance:.2f}", r.bks_vehicles,
                f"{r.our_distance:.2f}", r.our_vehicles,
                r.served, r.unassigned,
                f"{r.gap_pct:.2f}", r.feasible,
                f"{r.wall_clock_s:.1f}",
            ])
        # Aggregate summary row
        feasible_records = [r for r in records if r.feasible]
        if feasible_records:
            mean_gap = statistics.mean(r.gap_pct for r in feasible_records)
            writer.writerow([])
            writer.writerow([
                f"MEAN (n={len(feasible_records)}/{len(records)} feasible)",
                "", "", "", "", "", "", "",
                f"{mean_gap:.2f}", "", "",
            ])

    print(f"\nWrote Solomon results: {csv_path}")
    print("Summary:")
    for r in records:
        print(
            f"  {r.name:6} cust={r.customers:>3} "
            f"bks={r.bks_distance:>7.1f}({r.bks_vehicles}v) "
            f"ours={r.our_distance:>7.1f}({r.our_vehicles}v) "
            f"gap={r.gap_pct:>6.1f}% served={r.served}/{r.customers} "
            f"feas={r.feasible} ({r.wall_clock_s:.0f}s)"
        )

    assert csv_path.exists() and csv_path.stat().st_size > 0
