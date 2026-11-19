"""V&V — sensitivity sweep CLI (activity B4 in the V&V plan).

Sweeps one input parameter across a range of values, solves the VRP at
each point, and emits a CSV + PNG so you can confirm the system responds
to parameter changes in the expected direction. The output of the V&V
chapter cites this plot.

Supported parameters (one per run, via --param):
  fleet_size            — number of available vehicles (subset of base fleet)
  fuel_price            — multiplier on every vehicle's fuel_cost_per_km
  time_window_width     — multiplier on (latest - earliest) per order,
                          keeping the midpoint fixed

Usage:
  python backend/scripts/sensitivity_sweep.py \\
      --param fleet_size \\
      --range 1,2,3,4,5,6 \\
      --dataset synthetic_60 \\
      --time-budget 8 \\
      --output-dir .v_and_v/sensitivity

Output (in --output-dir):
  <param>_<dataset>_<timestamp>.csv
  <param>_<dataset>_<timestamp>.png

Exit codes:
  0  — sweep completed; all points produced a feasibility-valid plan.
  1  — one or more points produced an infeasible plan (validator errors).
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.algorithms.rich_vrp.bench.datasets import load_synthetic  # noqa: E402
from app.algorithms.solver_adapter import SolveOptions  # noqa: E402
from app.algorithms.solver_registry import get_solver  # noqa: E402
from app.algorithms.vrp_solver import OrderData, VRPInput  # noqa: E402
from tests.helpers.vv_helpers import validate_vrp_solution  # noqa: E402


# ---------------------------------------------------------------------------
# Parameter perturbations
# ---------------------------------------------------------------------------

def _perturb(vrp: VRPInput, param: str, value: float) -> VRPInput:
    """Return a copy of vrp with `param` set to `value`."""
    if param == "fleet_size":
        n = int(value)
        if n < 1:
            raise ValueError(f"fleet_size must be >= 1, got {n}")
        if n > len(vrp.vehicles):
            raise ValueError(
                f"fleet_size {n} exceeds available vehicles ({len(vrp.vehicles)})"
            )
        return dataclasses.replace(vrp, vehicles=vrp.vehicles[:n])

    if param == "fuel_price":
        # Scale fuel cost per km on every vehicle.
        new_vehicles = [
            dataclasses.replace(
                v, fuel_cost_per_km=int(round(v.fuel_cost_per_km * value))
            )
            for v in vrp.vehicles
        ]
        return dataclasses.replace(vrp, vehicles=new_vehicles)

    if param == "time_window_width":
        # Scale (latest - earliest) per order, keeping the midpoint fixed.
        new_orders: list[OrderData] = []
        for o in vrp.orders:
            mid = (o.earliest_time_s + o.latest_time_s) // 2
            half_width = int(round((o.latest_time_s - o.earliest_time_s) / 2 * value))
            new_orders.append(
                dataclasses.replace(
                    o,
                    earliest_time_s=max(0, mid - half_width),
                    latest_time_s=mid + half_width,
                )
            )
        return dataclasses.replace(vrp, orders=new_orders)

    raise ValueError(f"unknown --param: {param!r}")


# ---------------------------------------------------------------------------
# Sweep driver
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class SweepPoint:
    param_value: float
    total_distance_m: int
    total_duration_s: int
    active_vehicles: int
    served_count: int
    unassigned_count: int
    total_generalized_cost: int
    validation_error_count: int
    wall_clock_s: float


def run_sweep(
    *,
    param: str,
    values: list[float],
    dataset: str,
    time_budget_s: float,
    seed: int,
    solver_kind: str,
) -> list[SweepPoint]:
    base_vrp = load_synthetic(dataset)
    adapter = get_solver(solver_kind)
    points: list[SweepPoint] = []

    for value in values:
        vrp = _perturb(base_vrp, param, value)
        t0 = time.perf_counter()
        result = adapter.solve(vrp, SolveOptions(time_budget_s=time_budget_s, seed=seed))
        elapsed = time.perf_counter() - t0

        validation = validate_vrp_solution(result.solution, vrp)
        sol = result.solution
        cb = sol.cost_breakdown

        points.append(SweepPoint(
            param_value=value,
            total_distance_m=sol.total_distance_m,
            total_duration_s=sol.total_duration_s,
            active_vehicles=sum(1 for r in sol.routes if r.stops),
            served_count=sum(1 for r in sol.routes for s in r.stops if s.stop_type == "DELIVERY"),
            unassigned_count=len(sol.unassigned_orders),
            total_generalized_cost=cb.total_generalized_cost if cb else 0,
            validation_error_count=validation.error_count,
            wall_clock_s=elapsed,
        ))
        print(
            f"  {param}={value:<8} "
            f"dist={sol.total_distance_m / 1000:.1f}km "
            f"served={points[-1].served_count}/{len(vrp.orders)} "
            f"vehicles={points[-1].active_vehicles} "
            f"errors={validation.error_count} "
            f"({elapsed:.1f}s)"
        )

    return points


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_csv(points: list[SweepPoint], out_path: Path, *, param_name: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            param_name, "total_distance_m", "total_duration_s",
            "active_vehicles", "served_count", "unassigned_count",
            "total_generalized_cost", "validation_error_count", "wall_clock_s",
        ])
        for p in points:
            writer.writerow([
                p.param_value, p.total_distance_m, p.total_duration_s,
                p.active_vehicles, p.served_count, p.unassigned_count,
                p.total_generalized_cost, p.validation_error_count,
                f"{p.wall_clock_s:.3f}",
            ])


def write_png(
    points: list[SweepPoint],
    out_path: Path,
    *,
    param_name: str,
    dataset: str,
    metric: str,
) -> None:
    """Render a single chart of `metric` vs the swept parameter."""
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt  # noqa: PLC0415

    metric_extractors = {
        "total_distance_km": lambda p: p.total_distance_m / 1000.0,
        "total_duration_min": lambda p: p.total_duration_s / 60.0,
        "active_vehicles": lambda p: p.active_vehicles,
        "served_count": lambda p: p.served_count,
        "unassigned_count": lambda p: p.unassigned_count,
        "total_generalized_cost": lambda p: p.total_generalized_cost,
    }
    if metric not in metric_extractors:
        raise ValueError(f"unknown metric {metric!r}; known: {list(metric_extractors)}")

    xs = [p.param_value for p in points]
    ys = [metric_extractors[metric](p) for p in points]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(xs, ys, marker="o", linewidth=2)
    ax.set_xlabel(param_name)
    ax.set_ylabel(metric)
    ax.set_title(f"Sensitivity: {metric} vs {param_name}\ndataset={dataset}")
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_range(text: str) -> list[float]:
    return [float(x.strip()) for x in text.split(",") if x.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--param",
        required=True,
        choices=("fleet_size", "fuel_price", "time_window_width"),
        help="The parameter to sweep.",
    )
    parser.add_argument(
        "--range",
        required=True,
        type=_parse_range,
        help='Comma-separated values, e.g. "1,2,3,4,5,6".',
    )
    parser.add_argument(
        "--dataset",
        default="synthetic_60",
        choices=("synthetic_30", "synthetic_60", "synthetic_120", "synthetic_300"),
    )
    parser.add_argument("--time-budget", type=float, default=8.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--solver", default="rich_alns")
    parser.add_argument("--metric", default="total_distance_km")
    parser.add_argument("--output-dir", default=".v_and_v/sensitivity")

    args = parser.parse_args()

    print(
        f"Sweeping {args.param} over {args.range} on {args.dataset} "
        f"({args.solver}, {args.time_budget}s/point, seed={args.seed})"
    )

    points = run_sweep(
        param=args.param,
        values=args.range,
        dataset=args.dataset,
        time_budget_s=args.time_budget,
        seed=args.seed,
        solver_kind=args.solver,
    )

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    stem = f"{args.param}_{args.dataset}_{timestamp}"
    out_dir = Path(args.output_dir)
    csv_path = out_dir / f"{stem}.csv"
    png_path = out_dir / f"{stem}.png"

    write_csv(points, csv_path, param_name=args.param)
    write_png(points, png_path, param_name=args.param, dataset=args.dataset, metric=args.metric)

    print(f"\nWrote {csv_path}")
    print(f"Wrote {png_path}")

    total_errors = sum(p.validation_error_count for p in points)
    if total_errors > 0:
        print(
            f"\nFAIL: {total_errors} feasibility violation(s) across the sweep "
            "— see CSV column `validation_error_count` for the affected points.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
