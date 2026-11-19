"""Tests for M9 RichALNSAdapter failure handling (no OR-Tools fallback).

Phase C: the adapter never calls OR-Tools.  Failures surface as structured
SolveResult objects with engine_failed / engine_overtime / unserved_urgent
metrics so operations can respond via dashboards.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.algorithms.adapters.rich_alns import RichALNSAdapter
from app.algorithms.solver_adapter import SolveOptions, SolveResult
from app.algorithms.vrp_solver import SolverStatus
from app.models.enums import OrderPriority

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_order(order_id: str, priority: OrderPriority = OrderPriority.NORMAL):
    o = MagicMock()
    o.order_id = order_id
    o.priority = priority
    return o


def _mock_problem(urgent_id: str | None = None):
    problem = MagicMock()
    orders = [
        _mock_order("ord-0001", OrderPriority.NORMAL),
        _mock_order("ord-0002", OrderPriority.NORMAL),
    ]
    if urgent_id:
        orders.insert(0, _mock_order(urgent_id, OrderPriority.URGENT))
    problem.orders = orders
    return problem


def _make_solve_result(
    unassigned: list[str] | None = None,
    elapsed: float = 1.0,
    extra_metrics: dict | None = None,
) -> SolveResult:
    sol = MagicMock()
    sol.solver_status = SolverStatus.FEASIBLE
    sol.unassigned_orders = unassigned or []
    sol.routes = []
    sol.cost_breakdown = MagicMock(total_generalized_cost=0)
    return SolveResult(
        solution=sol,
        solver_kind="test_solver",
        elapsed_s=elapsed,
        metrics={**(extra_metrics or {}), "dummy": True},
    )


# ---------------------------------------------------------------------------
# Engine exception → NOT_FOUND result, no OR-Tools call
# ---------------------------------------------------------------------------

def test_engine_exception_returns_not_found_result():
    problem = _mock_problem()

    adapter = RichALNSAdapter()
    with patch("app.algorithms.adapters.rich_alns._run_real_engine",
               side_effect=RuntimeError("engine crash")):
        result = adapter.solve(problem, SolveOptions())

    assert result.solution.solver_status == SolverStatus.NOT_FOUND
    assert result.metrics.get("engine_failed") is True
    assert result.metrics.get("failure_reason") == "engine_exception"
    # All orders in unassigned
    assert set(result.solution.unassigned_orders) == {"ord-0001", "ord-0002"}


def test_engine_exception_no_ortools_import():
    """Confirm rich_alns module does not import ORToolsCVRPTWAdapter."""
    import ast
    import pathlib

    import app.algorithms.adapters.rich_alns as mod

    src = pathlib.Path(mod.__file__).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [n.name for n in getattr(node, "names", [])]
            module = getattr(node, "module", "") or ""
            assert "ORToolsCVRPTWAdapter" not in names, \
                "rich_alns must not import ORToolsCVRPTWAdapter"
            assert "ortools" not in module.lower() or "rich_vrp" in module.lower(), \
                f"rich_alns must not import from ortools adapter: {module}"


# ---------------------------------------------------------------------------
# Engine timeout → partial result returned, no OR-Tools call
# ---------------------------------------------------------------------------

def test_timeout_returns_partial_result_no_fallback():
    problem = _mock_problem()
    engine_result = _make_solve_result(elapsed=200.0)  # >> 1.2 × 60 = 72

    adapter = RichALNSAdapter()
    with patch("app.algorithms.adapters.rich_alns._run_real_engine", return_value=engine_result):
        result = adapter.solve(problem, SolveOptions(time_budget_s=60.0))

    assert result.metrics.get("engine_overtime") is True
    assert result.metrics.get("fallback_invoked") is False
    # Returns the engine's partial solution, not an empty one
    assert result.solution is engine_result.solution


def test_within_tolerance_no_overtime_flag():
    problem = _mock_problem()
    engine_result = _make_solve_result(elapsed=65.0)  # < 1.2 × 60 = 72

    adapter = RichALNSAdapter()
    with patch("app.algorithms.adapters.rich_alns._run_real_engine", return_value=engine_result):
        result = adapter.solve(problem, SolveOptions(time_budget_s=60.0))

    assert result.metrics.get("engine_overtime") is None
    assert result.metrics.get("fallback_invoked") is False


# ---------------------------------------------------------------------------
# Unserved URGENT → result returned with warning metrics, no OR-Tools call
# ---------------------------------------------------------------------------

def test_unserved_urgent_logs_and_returns_result():
    urgent_id = "ord-URGENT-0"
    problem = _mock_problem(urgent_id=urgent_id)
    engine_result = _make_solve_result(unassigned=[urgent_id], elapsed=5.0)

    adapter = RichALNSAdapter()
    with patch("app.algorithms.adapters.rich_alns._run_real_engine", return_value=engine_result):
        result = adapter.solve(problem, SolveOptions(time_budget_s=60.0))

    assert result.metrics.get("unserved_urgent_count") == 1
    assert urgent_id in result.metrics.get("unserved_urgent_ids", [])
    assert result.metrics.get("fallback_invoked") is False
    # Engine result returned as-is
    assert result.solution is engine_result.solution


def test_no_urgent_unserved_happy_path():
    problem = _mock_problem()
    engine_result = _make_solve_result(elapsed=10.0, extra_metrics={"total_iterations": 500})

    adapter = RichALNSAdapter()
    with patch("app.algorithms.adapters.rich_alns._run_real_engine", return_value=engine_result):
        result = adapter.solve(problem, SolveOptions(time_budget_s=60.0))

    assert result.metrics.get("fallback_invoked") is False
    assert result.metrics.get("total_iterations") == 500
    assert result.metrics.get("engine_failed") is None
    assert result.metrics.get("engine_overtime") is None
    assert result.metrics.get("unserved_urgent_count") is None
