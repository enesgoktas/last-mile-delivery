"""Database query profiler — slow-query logging and N+1 detection (Phase 8.10).

Registers SQLAlchemy engine-level ``before_cursor_execute`` and
``after_cursor_execute`` events to:

  1. Log queries that exceed ``SLOW_QUERY_THRESHOLD_MS``.
  2. Detect potential N+1 patterns: raise a WARNING when more than
     ``N1_THRESHOLD`` identical statement shapes are executed in a single
     request context window.

Activate by calling ``install_query_profiler(engine)`` once at startup.
Set ``ENABLE_QUERY_PROFILER=True`` in the environment or config — it is
disabled by default to avoid overhead in production.

N+1 detection heuristic:
  - Strip bind parameters from the SQL statement to get its "shape".
  - Count shape repetitions within each request context (keyed by request_id).
  - If any shape exceeds the threshold, emit a WARNING with the count and a
    truncated version of the statement.
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter, defaultdict

from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# How long a single query must take before it is logged as slow
SLOW_QUERY_THRESHOLD_MS: float = 200.0
# How many times the same query shape must be executed per request before
# a potential N+1 is flagged
N1_THRESHOLD: int = 10

# In-memory counters keyed by (request_id, statement_shape)
_shape_counters: dict[str, Counter[str]] = defaultdict(Counter)
_active_request_ids: set[str] = set()


def _normalise_sql(statement: str) -> str:
    """Strip bind parameter values from a SQL statement to get its shape.

    This collapses all literal values into a canonical placeholder so that
    repeated queries differing only in their WHERE clause values compare equal.
    """
    # Replace numeric literals
    s = re.sub(r"\b\d+\b", "?", statement)
    # Replace string literals
    s = re.sub(r"'[^']*'", "?", s)
    # Collapse whitespace
    return re.sub(r"\s+", " ", s).strip()


def install_query_profiler(engine: Engine) -> None:
    """Attach query-timing and N+1-detection hooks to the synchronous engine."""
    from sqlalchemy import event  # noqa: PLC0415

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def before_execute(conn, cursor, statement, parameters, context, executemany):
        conn.info["_qp_start"] = time.perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def after_execute(conn, cursor, statement, parameters, context, executemany):
        start = conn.info.pop("_qp_start", None)
        if start is None:
            return

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        # Slow query logging
        if elapsed_ms >= SLOW_QUERY_THRESHOLD_MS:
            logger.warning(
                "SLOW QUERY (%.1f ms): %s",
                elapsed_ms,
                statement[:500].replace("\n", " "),
            )

        # N+1 detection
        try:
            from app.utils.request_context import get_request_id  # noqa: PLC0415
            request_id = get_request_id()
        except Exception:
            request_id = None

        if request_id:
            shape = _normalise_sql(statement)
            _shape_counters[request_id][shape] += 1
            count = _shape_counters[request_id][shape]
            if count == N1_THRESHOLD:
                logger.warning(
                    "POTENTIAL N+1: query shape executed %d times in request %s: %s",
                    count,
                    request_id,
                    shape[:300],
                )


def clear_request_query_counters(request_id: str) -> None:
    """Call at the end of each request to free memory."""
    _shape_counters.pop(request_id, None)
