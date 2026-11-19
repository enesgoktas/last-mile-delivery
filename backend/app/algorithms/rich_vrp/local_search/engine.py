"""Variable Neighborhood Descent (VND) local search engine.

Applies a fixed sequence of move neighborhoods. After each successful improving
move, restarts from the first neighborhood (VND / first-improving strategy).
Stops when no neighborhood yields improvement or when the time budget expires.
"""
from __future__ import annotations

import logging
import time

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.models import Problem
from app.algorithms.rich_vrp.local_search.candidate_lists import CandidateLists
from app.algorithms.rich_vrp.local_search.evaluator import DeltaEvaluator
from app.algorithms.rich_vrp.local_search.moves import (
    compat_repair,
    cross_exchange,
    exchange,
    optional_drop,
    relocate,
    relocate_pair,
    reload_insertion,
    two_opt,
    two_opt_star,
)

logger = logging.getLogger(__name__)

# VND neighborhood order: cheapest to evaluate first; restart on improvement.
# `reload_insertion` runs first because it's the only neighborhood that can
# rescue a capacity-infeasible route — the cheaper relocation/2-opt moves
# can't add capacity-resetting visits, so running them on an infeasible
# route is wasted work.
_NEIGHBORHOODS = [
    ("reload_insertion", reload_insertion.search),
    ("relocate", relocate.search),
    ("two_opt", two_opt.search),
    ("relocate_pair", relocate_pair.search),
    ("two_opt_star", two_opt_star.search),
    ("exchange", exchange.search),
    ("cross_exchange", cross_exchange.search),
    ("optional_drop", optional_drop.search),
    ("compat_repair", compat_repair.search),
]


def improve(
    state: ALNSState,
    problem: Problem,
    candidates: CandidateLists,
    evaluator: DeltaEvaluator,
    max_seconds: float = 5.0,
) -> ALNSState:
    """Run VND until no improvement or time budget exhausted. Mutates state in place."""
    deadline = time.perf_counter() + max_seconds
    improved = True
    while improved and time.perf_counter() < deadline:
        improved = False
        for nbh_name, move_fn in _NEIGHBORHOODS:
            if time.perf_counter() >= deadline:
                break
            _t = time.perf_counter()
            try:
                n = move_fn(state, problem, candidates, evaluator, max_moves=1, deadline_s=deadline)
            except TypeError:
                # Neighborhoods that don't yet accept deadline_s fall back to
                # the older signature; the outer-loop deadline still bounds us.
                n = move_fn(state, problem, candidates, evaluator, max_moves=1)
            elapsed = time.perf_counter() - _t
            if elapsed > 1.0:
                logger.warning(
                    "ls.slow_neighborhood %s elapsed=%.2fs n_moves=%d (budget remaining %.2fs)",
                    nbh_name, elapsed, n, deadline - time.perf_counter(),
                )
            if n > 0:
                improved = True
                break  # restart from first neighborhood
    state.recompute_objective()
    return state
