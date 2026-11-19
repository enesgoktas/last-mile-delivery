from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from redis import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.enums import OptimizationRunStatus, RouteStatus
from app.models.optimization_run import OptimizationRun
from app.models.route import Route
from app.models.route_scenario import RouteScenarioComparison
from app.utils.compat import utcnow_naive
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

BATCH_OPTIMIZATION_LOCK_KEY = "optimization:batch:lock"
BATCH_OPTIMIZATION_LOCK_META_KEY = f"{BATCH_OPTIMIZATION_LOCK_KEY}:meta"
BATCH_OPTIMIZATION_ACTIVE_TASK_KEY = "optimization:batch:active"
BATCH_OPTIMIZATION_ACTIVE_TASK_META_KEY = f"{BATCH_OPTIMIZATION_ACTIVE_TASK_KEY}:meta"
SUPERSEDED_OPTIMIZATION_MESSAGE = "Superseded by a newer optimization request"
_CANCELLED_TASK_TTL_SECONDS = 60 * 60
_ACTIVE_TASK_TTL_SECONDS = 60 * 60


@dataclass(frozen=True)
class BatchOptimizationLockMetadata:
    owner: str
    task_id: str
    tenant_id: str | None
    operation: str
    comparison_id: str | None = None


@dataclass(frozen=True)
class SupersedeActiveOptimizationResult:
    cancelled: bool
    task_id: str | None = None
    operation: str | None = None
    comparison_id: str | None = None
    blocked: bool = False
    detail: str | None = None


def task_cancellation_key(task_id: str) -> str:
    return f"optimization:task:{task_id}:cancelled"


def record_active_batch_optimization_task(
    *,
    task_id: str,
    tenant_id: str | None,
    operation: str,
    comparison_id: str | None = None,
    ttl_seconds: int = _ACTIVE_TASK_TTL_SECONDS,
) -> None:
    redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        record_active_batch_optimization_task_with_client(
            redis_client,
            task_id=task_id,
            tenant_id=tenant_id,
            operation=operation,
            comparison_id=comparison_id,
            ttl_seconds=ttl_seconds,
        )
    finally:
        redis_client.close()


def record_active_batch_optimization_task_with_client(
    redis_client: Redis,
    *,
    task_id: str,
    tenant_id: str | None,
    operation: str,
    comparison_id: str | None = None,
    ttl_seconds: int = _ACTIVE_TASK_TTL_SECONDS,
) -> None:
    redis_client.set(BATCH_OPTIMIZATION_ACTIVE_TASK_KEY, task_id, ex=ttl_seconds)
    redis_client.set(
        BATCH_OPTIMIZATION_ACTIVE_TASK_META_KEY,
        _metadata_json(
            owner=None,
            task_id=task_id,
            tenant_id=tenant_id,
            operation=operation,
            comparison_id=comparison_id,
        ),
        ex=ttl_seconds,
    )


def clear_active_batch_optimization_task(redis_client: Redis, *, task_id: str) -> None:
    release_script = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        redis.call('del', KEYS[2])
        return redis.call('del', KEYS[1])
    else
        return 0
    end
    """
    redis_client.eval(
        release_script,
        2,
        BATCH_OPTIMIZATION_ACTIVE_TASK_KEY,
        BATCH_OPTIMIZATION_ACTIVE_TASK_META_KEY,
        task_id,
    )


def acquire_batch_optimization_lock(
    redis_client: Redis,
    *,
    owner: str,
    task_id: str,
    tenant_id: str | None,
    operation: str,
    ttl_seconds: int,
    comparison_id: str | None = None,
) -> bool:
    acquired = redis_client.set(
        BATCH_OPTIMIZATION_LOCK_KEY,
        owner,
        nx=True,
        ex=ttl_seconds,
    )
    if not acquired:
        return False

    try:
        redis_client.set(
            BATCH_OPTIMIZATION_LOCK_META_KEY,
            _metadata_json(
                owner=owner,
                task_id=task_id,
                tenant_id=tenant_id,
                operation=operation,
                comparison_id=comparison_id,
            ),
            ex=ttl_seconds,
        )
    except Exception:
        release_owned_batch_optimization_lock(redis_client, owner=owner)
        raise
    return True


def release_owned_batch_optimization_lock(redis_client: Redis, *, owner: str) -> None:
    release_script = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        redis.call('del', KEYS[2])
        return redis.call('del', KEYS[1])
    else
        return 0
    end
    """
    redis_client.eval(
        release_script,
        2,
        BATCH_OPTIMIZATION_LOCK_KEY,
        BATCH_OPTIMIZATION_LOCK_META_KEY,
        owner,
    )


def is_task_cancelled(redis_client: Redis, *, task_id: str) -> bool:
    return bool(redis_client.get(task_cancellation_key(task_id)))


def supersede_active_batch_optimization(
    *,
    tenant_id: str,
    reason: str = SUPERSEDED_OPTIMIZATION_MESSAGE,
) -> SupersedeActiveOptimizationResult:
    redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        return supersede_active_batch_optimization_with_client(
            redis_client,
            tenant_id=tenant_id,
            reason=reason,
        )
    finally:
        redis_client.close()


def supersede_active_batch_optimization_with_client(
    redis_client: Redis,
    *,
    tenant_id: str,
    reason: str = SUPERSEDED_OPTIMIZATION_MESSAGE,
) -> SupersedeActiveOptimizationResult:
    lock_owner = redis_client.get(BATCH_OPTIMIZATION_LOCK_KEY)
    if lock_owner:
        metadata = _load_lock_metadata(redis_client, expected_owner=lock_owner)
        if metadata is None:
            return SupersedeActiveOptimizationResult(
                cancelled=False,
                blocked=True,
                detail="An optimization is running, but its task metadata is unavailable.",
            )
    else:
        metadata = _load_active_task_metadata(redis_client)
    if metadata is None:
        return SupersedeActiveOptimizationResult(cancelled=False)

    if metadata.tenant_id is not None and metadata.tenant_id != tenant_id:
        return SupersedeActiveOptimizationResult(
            cancelled=False,
            blocked=True,
            task_id=metadata.task_id,
            operation=metadata.operation,
            comparison_id=metadata.comparison_id,
            detail="Another tenant already has an optimization running.",
        )

    redis_client.set(
        task_cancellation_key(metadata.task_id),
        reason,
        ex=_CANCELLED_TASK_TTL_SECONDS,
    )
    celery_app.control.revoke(metadata.task_id, terminate=True, signal="SIGTERM")
    if lock_owner:
        release_owned_batch_optimization_lock(redis_client, owner=metadata.owner)
    clear_active_batch_optimization_task(redis_client, task_id=metadata.task_id)
    return SupersedeActiveOptimizationResult(
        cancelled=True,
        task_id=metadata.task_id,
        operation=metadata.operation,
        comparison_id=metadata.comparison_id,
    )


async def mark_superseded_optimization_artifacts(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cancellation: SupersedeActiveOptimizationResult,
    reason: str = SUPERSEDED_OPTIMIZATION_MESSAGE,
) -> None:
    if not cancellation.cancelled or cancellation.task_id is None:
        return

    await _mark_optimization_run_superseded(
        db,
        tenant_id=tenant_id,
        task_id=cancellation.task_id,
        reason=reason,
    )
    await _mark_route_comparison_superseded(
        db,
        tenant_id=tenant_id,
        task_id=cancellation.task_id,
        comparison_id=cancellation.comparison_id,
        reason=reason,
    )


async def supersede_active_batch_optimization_for_tenant(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> SupersedeActiveOptimizationResult:
    cancellation = supersede_active_batch_optimization(tenant_id=str(tenant_id))
    if cancellation.cancelled:
        await mark_superseded_optimization_artifacts(
            db,
            tenant_id=tenant_id,
            cancellation=cancellation,
        )
        await db.commit()
    return cancellation


def _load_lock_metadata(
    redis_client: Redis,
    *,
    expected_owner: str,
) -> BatchOptimizationLockMetadata | None:
    raw_metadata = redis_client.get(BATCH_OPTIMIZATION_LOCK_META_KEY)
    if not raw_metadata:
        return None

    payload = _load_metadata_payload(raw_metadata)
    if payload is None:
        return None

    if payload.get("owner") != expected_owner:
        logger.warning("Ignoring stale optimization lock metadata")
        return None

    task_id = payload.get("task_id")
    operation = payload.get("operation")
    if not isinstance(task_id, str) or not task_id:
        return None
    if not isinstance(operation, str) or not operation:
        return None

    tenant_id = payload.get("tenant_id")
    comparison_id = payload.get("comparison_id")
    return BatchOptimizationLockMetadata(
        owner=expected_owner,
        task_id=task_id,
        tenant_id=tenant_id if isinstance(tenant_id, str) else None,
        operation=operation,
        comparison_id=comparison_id if isinstance(comparison_id, str) else None,
    )


def _load_active_task_metadata(redis_client: Redis) -> BatchOptimizationLockMetadata | None:
    active_task_id = redis_client.get(BATCH_OPTIMIZATION_ACTIVE_TASK_KEY)
    if not active_task_id:
        return None

    raw_metadata = redis_client.get(BATCH_OPTIMIZATION_ACTIVE_TASK_META_KEY)
    if not raw_metadata:
        return None

    payload = _load_metadata_payload(raw_metadata)
    if payload is None:
        return None
    if payload.get("task_id") != active_task_id:
        logger.warning("Ignoring stale optimization active-task metadata")
        return None

    operation = payload.get("operation")
    if not isinstance(operation, str) or not operation:
        return None

    tenant_id = payload.get("tenant_id")
    comparison_id = payload.get("comparison_id")
    return BatchOptimizationLockMetadata(
        owner=active_task_id,
        task_id=active_task_id,
        tenant_id=tenant_id if isinstance(tenant_id, str) else None,
        operation=operation,
        comparison_id=comparison_id if isinstance(comparison_id, str) else None,
    )


def _metadata_json(
    *,
    owner: str | None,
    task_id: str,
    tenant_id: str | None,
    operation: str,
    comparison_id: str | None,
) -> str:
    return json.dumps(
        {
            "version": 1,
            "owner": owner,
            "task_id": task_id,
            "tenant_id": tenant_id,
            "operation": operation,
            "comparison_id": comparison_id,
        },
        sort_keys=True,
    )


def _load_metadata_payload(raw_metadata: str) -> dict[str, Any] | None:
    try:
        payload: dict[str, Any] = json.loads(raw_metadata)
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed optimization task metadata")
        return None
    return payload


async def _mark_optimization_run_superseded(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    task_id: str,
    reason: str,
) -> None:
    result = await db.execute(
        select(OptimizationRun).where(
            OptimizationRun.tenant_id == tenant_id,
            OptimizationRun.task_id == task_id,
            OptimizationRun.finished_at.is_(None),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        return

    run.status = OptimizationRunStatus.FAILED
    run.finished_at = utcnow_naive()
    run.error_message = reason
    run.routes_created = run.routes_created or 0
    run.unassigned_count = run.unassigned_count or 0
    run.solver_status = "SUPERSEDED"
    run.metrics = {
        **(run.metrics or {}),
        "routes_created": run.routes_created,
        "unassigned_count": run.unassigned_count,
        "solver_status": "SUPERSEDED",
    }


async def _mark_route_comparison_superseded(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    task_id: str,
    comparison_id: str | None,
    reason: str,
) -> None:
    filters = [
        RouteScenarioComparison.tenant_id == tenant_id,
        RouteScenarioComparison.completed_at.is_(None),
    ]
    if comparison_id is not None:
        filters.append(RouteScenarioComparison.id == uuid.UUID(comparison_id))
    else:
        filters.append(RouteScenarioComparison.task_id == task_id)

    result = await db.execute(
        select(RouteScenarioComparison)
        .where(*filters)
        .options(selectinload(RouteScenarioComparison.scenarios))
    )
    comparison = result.scalar_one_or_none()
    if comparison is None:
        return

    scenario_run_ids = [
        scenario.optimization_run_id
        for scenario in comparison.scenarios
        if scenario.optimization_run_id is not None
    ]
    if scenario_run_ids:
        await db.execute(
            delete(Route).where(
                Route.tenant_id == tenant_id,
                Route.status == RouteStatus.DRAFT,
                Route.optimization_run_id.in_(scenario_run_ids),
            )
        )

    now = utcnow_naive()
    comparison.status = "FAILED"
    comparison.error_message = reason
    comparison.completed_at = now
    for scenario in comparison.scenarios:
        scenario.status = "FAILED"
        scenario.error_message = reason
        scenario.completed_at = now
