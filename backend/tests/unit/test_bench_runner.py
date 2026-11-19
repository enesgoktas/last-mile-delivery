"""Tests for the benchmark harness (M1.6)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import app.algorithms.rich_vrp.bench.runner as runner_mod
from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.rich_vrp.bench.reports import format_table
from app.algorithms.rich_vrp.bench.runner import SuiteResults, run_suite
from app.algorithms.solver_adapter import SolveResult
from app.algorithms.vrp_solver import SolverStatus, VRPSolution


def _stub_solution() -> VRPSolution:
    return VRPSolution(
        routes=[],
        unassigned_orders=[],
        total_distance_m=1000,
        total_duration_s=600,
        solver_status=SolverStatus.FEASIBLE,
    )


def _stub_adapter() -> MagicMock:
    stub = MagicMock()
    stub.solve.return_value = SolveResult(
        solution=_stub_solution(),
        solver_kind="stub",
        elapsed_s=0.1,
        metrics={},
    )
    return stub


def test_load_synthetic_returns_vrp_input():
    for name in ("synthetic_30", "synthetic_60", "synthetic_120", "synthetic_300"):
        vrp = load_synthetic(name)
        assert len(vrp.orders) == int(name.split("_")[1])
        assert len(vrp.vehicles) > 0
        assert len(vrp.locations) > 0


def test_load_synthetic_unknown_raises():
    with pytest.raises(KeyError):
        load_synthetic("synthetic_9999")


def test_run_suite_produces_cells(monkeypatch):
    """run_suite should produce one cell per (adapter × dataset × seed)."""
    stub = _stub_adapter()
    monkeypatch.setattr(runner_mod, "get_solver", lambda _: stub)

    results = run_suite(
        adapters=["stub"],
        dataset_names=["synthetic_30"],
        seeds=[0, 1],
        time_budget_s=1.0,
    )
    assert len(results.cells) == 2  # 1 adapter × 1 dataset × 2 seeds
    for cell in results.cells:
        assert cell.adapter == "stub"
        assert cell.dataset == "synthetic_30"
        assert cell.error is None


def test_run_suite_cells_have_expected_shape(monkeypatch):
    stub = _stub_adapter()
    monkeypatch.setattr(runner_mod, "get_solver", lambda _: stub)

    results = run_suite(["stub"], ["synthetic_30"], [0])
    cell = results.cells[0]
    assert cell.wall_clock_s > 0
    assert cell.total_distance_m == 1000
    assert cell.total_duration_s == 600


def test_format_table_renders_markdown(monkeypatch):
    stub = _stub_adapter()
    monkeypatch.setattr(runner_mod, "get_solver", lambda _: stub)

    results = run_suite(["stub"], ["synthetic_30"], [0])
    table = format_table(results)

    assert "##" in table
    assert "synthetic_30" in table
    assert "|" in table


def test_format_table_empty():
    results = SuiteResults(cells=[], run_id="test")
    assert "(no results)" in format_table(results)
