from tests.benchmarks.vrp_benchmark_suite import (
    evaluate_benchmark_result,
    get_vrp_benchmark_scenarios,
    run_benchmark_scenario,
)


def test_vrp_benchmark_regression_thresholds_hold():
    for scenario in get_vrp_benchmark_scenarios():
        result = run_benchmark_scenario(scenario)
        failures = evaluate_benchmark_result(scenario, result)
        assert not failures, f"{scenario.name} regressions: {'; '.join(failures)}"
