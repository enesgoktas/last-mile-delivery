"""Benchmark harness: runs all adapters on a suite of datasets and emits JSON metrics.

Usage (as a script):
    python -m app.algorithms.rich_vrp.bench.runner \
        --dataset synthetic_30 --dataset synthetic_60 \
        --adapter ortools_cvrptw --adapter rich_alns \
        --seeds 0 1 2 --time-budget-s 30
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.solver_adapter import SolveOptions
from app.algorithms.solver_registry import get_solver

logger = logging.getLogger(__name__)

_BENCH_OUTPUT_DIR = Path("tmp/bench")


@dataclass
class RunCell:
    """Metrics for one (adapter × dataset × seed) combination."""
    adapter: str
    dataset: str
    seed: int
    served_count: int = 0
    unserved_count: int = 0
    total_distance_m: int = 0
    total_duration_s: int = 0
    late_minutes: int = 0
    overtime_minutes: int = 0
    generalized_cost: int = 0
    unserved_weighted: int = 0
    wall_clock_s: float = 0.0
    solver_status: str = "UNKNOWN"
    error: str | None = None
    objective_lex_tuple: tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass
class SuiteResults:
    cells: list[RunCell] = field(default_factory=list)
    run_id: str = ""


def run_suite(
    adapters: list[str],
    dataset_names: list[str],
    seeds: list[int],
    time_budget_s: float = 60.0,
) -> SuiteResults:
    """Run all (adapter × dataset × seed) combinations and collect metrics."""
    run_id = f"{int(time.time())}"
    results = SuiteResults(run_id=run_id)

    for dataset_name in dataset_names:
        vrp_input = load_synthetic(dataset_name)
        for adapter_name in adapters:
            adapter = get_solver(adapter_name)
            for seed in seeds:
                cell = RunCell(adapter=adapter_name, dataset=dataset_name, seed=seed)
                options = SolveOptions(time_budget_s=time_budget_s, seed=seed)
                t0 = time.perf_counter()
                try:
                    result = adapter.solve(vrp_input, options)
                    elapsed = time.perf_counter() - t0

                    sol = result.solution
                    n_served = sum(len(r.stops) for r in sol.routes if r.stops)
                    cb = sol.cost_breakdown

                    cell.served_count = n_served
                    cell.unserved_count = len(sol.unassigned_orders)
                    cell.total_distance_m = sol.total_distance_m
                    cell.total_duration_s = sol.total_duration_s
                    cell.late_minutes = cb.late_minutes if cb else 0
                    cell.overtime_minutes = cb.overtime_minutes if cb else 0
                    cell.generalized_cost = cb.total_generalized_cost if cb else 0
                    cell.wall_clock_s = elapsed
                    cell.solver_status = result.solution.solver_status.value if hasattr(result.solution, "solver_status") else "FEASIBLE"
                    cell.objective_lex_tuple = (
                        cell.unserved_weighted,
                        cell.late_minutes + cell.overtime_minutes,
                        cell.generalized_cost,
                        0,
                    )
                except Exception as exc:  # noqa: BLE001
                    cell.error = str(exc)
                    cell.wall_clock_s = time.perf_counter() - t0
                    logger.exception("Bench error: adapter=%s dataset=%s seed=%d", adapter_name, dataset_name, seed)

                results.cells.append(cell)
                logger.info(
                    "  %-20s %-14s seed=%d → %d served, cost=%d, %.1fs",
                    adapter_name, dataset_name, seed,
                    cell.served_count, cell.generalized_cost, cell.wall_clock_s,
                )

    _save_results(results)
    return results


def _save_results(results: SuiteResults) -> None:
    _BENCH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _BENCH_OUTPUT_DIR / f"{results.run_id}.json"
    with path.open("w") as f:
        json.dump(
            {
                "run_id": results.run_id,
                "cells": [
                    {
                        "adapter": c.adapter,
                        "dataset": c.dataset,
                        "seed": c.seed,
                        "served_count": c.served_count,
                        "unserved_count": c.unserved_count,
                        "total_distance_m": c.total_distance_m,
                        "total_duration_s": c.total_duration_s,
                        "late_minutes": c.late_minutes,
                        "overtime_minutes": c.overtime_minutes,
                        "generalized_cost": c.generalized_cost,
                        "wall_clock_s": c.wall_clock_s,
                        "solver_status": c.solver_status,
                        "objective_lex_tuple": list(c.objective_lex_tuple),
                        "error": c.error,
                    }
                    for c in results.cells
                ],
            },
            f,
            indent=2,
        )
    print(f"Bench results saved to {path}")


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Rich VRP benchmark runner")
    parser.add_argument("--dataset", dest="datasets", action="append", default=[])
    parser.add_argument("--adapter", dest="adapters", action="append", default=[])
    parser.add_argument("--seeds", nargs="+", type=int, default=[0])
    parser.add_argument("--time-budget-s", type=float, default=60.0)
    args = parser.parse_args()

    if not args.datasets:
        args.datasets = ["synthetic_30"]
    if not args.adapters:
        args.adapters = ["ortools_cvrptw"]

    results = run_suite(
        adapters=args.adapters,
        dataset_names=args.datasets,
        seeds=args.seeds,
        time_budget_s=args.time_budget_s,
    )

    from app.algorithms.rich_vrp.bench.reports import format_table
    print(format_table(results))


if __name__ == "__main__":
    _cli()
