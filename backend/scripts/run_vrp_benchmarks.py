from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def main() -> None:
    from tests.benchmarks.vrp_benchmark_suite import (
        evaluate_benchmark_result,
        get_vrp_benchmark_scenarios,
        run_benchmark_scenario,
    )

    scenarios = get_vrp_benchmark_scenarios()
    evaluated_results = []
    has_failures = False
    for scenario in scenarios:
        result = run_benchmark_scenario(scenario)
        failures = evaluate_benchmark_result(scenario, result)
        evaluated_results.append((scenario, result, failures))
        has_failures = has_failures or bool(failures)

    payload = {
        "scenario_count": len(evaluated_results),
        "all_passed": not has_failures,
        "results": [
            {
                "description": scenario.description,
                "name": result.name,
                "elapsed_ms": round(result.elapsed_ms, 2),
                "solver_status": result.solver_status,
                "total_distance_m": result.total_distance_m,
                "total_duration_s": result.total_duration_s,
                "assigned_orders": result.assigned_orders,
                "unassigned_orders": result.unassigned_orders,
                "route_assignments": result.route_assignments,
                "thresholds": {
                    "max_total_distance_m": scenario.expectation.max_total_distance_m,
                    "max_unassigned_orders": scenario.expectation.max_unassigned_orders,
                    "max_elapsed_ms": scenario.expectation.max_elapsed_ms,
                },
                "passed": not failures,
                "failures": failures,
            }
            for scenario, result, failures in evaluated_results
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if has_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
