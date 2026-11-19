"""Persist a `VRPInput` snapshot for every solve so historical runs can
be re-played with a future solver version.

Layer 2b. The adapter swap pattern only works in production when you
can prove the new version isn't worse than the old on the same problems.
Without the replay journal you'd have to wait for new orders to test
solver improvements; with it, a single offline batch can validate a
release against months of history.

The serialization is a JSONB dump of the dataclass fields. Postgres
TOASTs the row past ~8 KB, so large matrices land in TOAST storage
without app-layer compression — Postgres's pglz is fine for matrix data.
"""
from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.algorithms.vrp_solver import VRPInput
from app.models.optimization_run_replay import OptimizationRunReplay

logger = logging.getLogger(__name__)


def _vrp_input_to_payload(vrp_input: VRPInput) -> dict[str, Any]:
    """Convert a `VRPInput` into a JSON-serializable dict.

    `dataclasses.asdict` walks nested dataclasses (OrderData, VehicleData)
    automatically. Tuples in distances/durations become lists; the
    replay reader can reconstruct them. Enums get coerced to their string
    value via the same StrEnum serialization the rest of the API uses.
    """

    def _coerce(value: Any) -> Any:
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {k: _coerce(v) for k, v in dataclasses.asdict(value).items()}
        if isinstance(value, dict):
            return {k: _coerce(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            # frozenset / set aren't JSON-serializable; coerce to list
            return [_coerce(v) for v in value]
        if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
            # StrEnum / SAEnum
            return value.value
        return value

    return _coerce(vrp_input)


async def persist_replay(
    db: AsyncSession,
    *,
    optimization_run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    vrp_input: VRPInput,
    adapter_version: str,
    random_seed: int | None = None,
    time_budget_allowed_s: float | None = None,
    time_budget_used_s: float | None = None,
) -> OptimizationRunReplay | None:
    """Persist the replay row for one solve. Idempotent on
    `optimization_run_id` via ON CONFLICT — re-running over the same run
    refreshes the payload instead of duplicating."""
    try:
        payload = _vrp_input_to_payload(vrp_input)
    except Exception:
        logger.warning(
            "replay journal: failed to serialize VRPInput for run %s",
            optimization_run_id,
            exc_info=True,
        )
        return None

    values = {
        "optimization_run_id": optimization_run_id,
        "tenant_id": tenant_id,
        "adapter_version": adapter_version,
        "random_seed": random_seed,
        "time_budget_allowed_s": time_budget_allowed_s,
        "time_budget_used_s": time_budget_used_s,
        "vrp_input_payload": payload,
    }
    stmt = (
        pg_insert(OptimizationRunReplay)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["optimization_run_id"],
            set_={k: v for k, v in values.items() if k != "optimization_run_id"},
        )
        .returning(OptimizationRunReplay.id)
    )
    row_id = (await db.execute(stmt)).scalar_one()
    return await db.get(OptimizationRunReplay, row_id)
