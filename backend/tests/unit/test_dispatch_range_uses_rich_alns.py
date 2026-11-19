"""Phase B-1 guard — `dispatch_range` calls the Rich ALNS adapter, not
raw OR-Tools `solve()`. Source-level invariant tests, because a full live
solve is heavy and belongs in the integration suite.
"""
from __future__ import annotations

import inspect


def test_dispatch_range_imports_solver_registry():
    """The new path uses `get_solver("rich_alns")`. The old raw `solve`
    import must be gone so a stale call site can't accidentally regress."""
    from app.services import dispatch_range_service

    src = inspect.getsource(dispatch_range_service)
    assert "from app.algorithms.solver_registry import get_solver" in src, (
        "dispatch_range_service must import get_solver from solver_registry"
    )
    assert "from app.algorithms.solver_adapter import SolveOptions" in src, (
        "dispatch_range_service must import SolveOptions"
    )
    # The bare OR-Tools `solve` import must NOT be in the import list any
    # more. (It might appear in comments or docstrings, so look for the
    # import line exactly.)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("from app.algorithms.vrp_solver import"):
            assert "solve" not in stripped.split("import", 1)[1], (
                "dispatch_range_service still imports the bare OR-Tools "
                "`solve` function; should call the rich_alns adapter instead."
            )


def test_dispatch_range_calls_get_solver_rich_alns():
    """The solve call must route through the adapter registry with the
    `rich_alns` kind, not call the bare OR-Tools function."""
    from app.services import dispatch_range_service

    src = inspect.getsource(dispatch_range_service)
    assert 'get_solver("rich_alns")' in src, (
        "dispatch_range must explicitly request the rich_alns adapter"
    )
    # Sanity: ensure the bare `solve(` call is gone too.
    # (`solve_result`, `solve_in_chunks`, etc. are fine — only the literal
    # `solve(` lookup should be absent.)
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("solve(") or stripped.startswith("await solve("):
            raise AssertionError(
                f"Found a bare `solve(` call in dispatch_range_service: {line!r}"
            )


def test_dispatch_range_surfaces_alns_telemetry_in_result():
    """The run result must carry the ALNS metrics so the optimization-
    gains dashboard and audit logs can read them."""
    from app.services import dispatch_range_service

    src = inspect.getsource(dispatch_range_service)
    assert '"solver_kind": "rich_alns"' in src
    assert '"solver_metrics":' in src
    assert '"solver_elapsed_s":' in src


def test_resolve_range_time_limit_tier_table_present():
    """Phase B-1 introduces a tier table that scales with problem size.
    Lock the table's existence so a refactor can't quietly revert to a
    single-line heuristic."""
    from app.services import dispatch_range_service as svc

    assert hasattr(svc, "_RANGE_TIME_BUDGET_TIERS"), (
        "Phase B-1 introduced `_RANGE_TIME_BUDGET_TIERS`; missing now."
    )
    tiers = svc._RANGE_TIME_BUDGET_TIERS
    # 4 tiers: quick-small, full-small, medium, large fallback.
    assert len(tiers) >= 3
    # Each row is (max_span, max_orders, base_s, ceiling_s).
    for row in tiers:
        assert len(row) == 4
        max_span, max_orders, base, ceiling = row
        assert max_span >= 1
        assert max_orders >= 1
        assert base >= 1
        assert ceiling >= base
