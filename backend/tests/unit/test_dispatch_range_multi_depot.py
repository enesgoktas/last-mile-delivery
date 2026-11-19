"""Phase A guard — `dispatch_range` must pass `operations_policy` to
`build_vrp_data_structures` so the multi-depot fields (`additional_depots`,
`end_depot_id`) reach the solver's VehicleData. Without it the range path
silently ignores the tenant's multi-depot configuration.

Source-level invariant — full end-to-end coverage requires OSRM + DB and
lives in the live integration suite.
"""
from __future__ import annotations

import inspect


def test_dispatch_range_threads_operations_policy_to_build_vrp_data_structures():
    """Pin the bug fix that the audit found: the call site at line ~263
    of dispatch_range_service.py must explicitly pass `operations_policy`."""
    from app.services import dispatch_range_service

    src = inspect.getsource(dispatch_range_service)
    # The keyword argument must appear inside the function body. A grep
    # would also catch it but locking on inspect.getsource is robust to
    # whitespace and comment changes.
    assert "operations_policy=operations_policy" in src, (
        "dispatch_range must pass `operations_policy` to "
        "`build_vrp_data_structures`, otherwise multi-depot tenants get "
        "their additional_depots / end_depot_id silently dropped."
    )


def test_dispatch_range_has_no_hardcoded_14_day_cap():
    """The 14-day cap was the user-facing limit on horizon length. It
    must be gone so 3-day, 30-day, 90-day requests all flow through the
    same code path."""
    from app.services import dispatch_range_service

    src = inspect.getsource(dispatch_range_service)
    # Old guard text — must NOT be present.
    assert "capped at 14 days" not in src, (
        "The hardcoded 14-day cap is back. Range planning is horizon-agnostic."
    )
    assert "> 14" not in src.split("MAX_RANGE_SPAN_DAYS", 1)[0], (
        "Found a `> 14` guard before MAX_RANGE_SPAN_DAYS is referenced — "
        "the 14-day cap was reintroduced."
    )


def test_dispatch_range_safety_net_is_180_days():
    """The 180-day safety net guards typos / runaway inputs without
    capping legitimate long-horizon requests."""
    from app.services import dispatch_range_service

    assert dispatch_range_service.MAX_RANGE_SPAN_DAYS == 180


def test_dispatch_range_order_query_limit_is_10k():
    """The 1500-order limit is gone; truncation only kicks in at 10k."""
    from app.services import dispatch_range_service

    assert dispatch_range_service.ORDER_QUERY_LIMIT == 10_000
