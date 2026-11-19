"""
Celery tasks for route optimization.

These tasks run on the Celery worker process (not the FastAPI event loop).
Each task creates its own synchronous DB session for database access.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from redis import Redis

from app import database
from app.config import settings
from app.models.enums import OptimizationRunStatus, OptimizationRunType
from app.services import optimization_run_service
from app.services.optimization_control_service import (
    SUPERSEDED_OPTIMIZATION_MESSAGE,
    acquire_batch_optimization_lock,
    clear_active_batch_optimization_task,
    is_task_cancelled,
    release_owned_batch_optimization_lock,
)
from app.utils.compat import UTC
from app.workers.celery_app import celery_app
from app.workers.task_request_context import bind_task_request_id

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from a sync Celery task.

    Celery workers are synchronous; this helper creates a fresh event loop
    to execute async service code (DB queries, Redis, OSRM calls).

    The session-factory contextvar is set to ``celery_async_session``
    (NullPool engine) so every async DB call gets a connection that belongs
    to *this* event loop, avoiding the "Future attached to a different loop"
    error that occurs when pooled connections are reused across loops.
    """
    database._session_factory_override.set(database.celery_async_session)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _run_with_task_request_context(task_request, fn):
    with bind_task_request_id(task_request):
        return fn()


def _exception_message(exc: BaseException, fallback: str) -> str:
    return str(exc) or exc.__class__.__name__ or fallback


def _release_owned_lock(redis_client: Redis, *, key: str, owner: str) -> None:
    release_script = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    else
        return 0
    end
    """
    redis_client.eval(release_script, 1, key, owner)


def _try_acquire_reoptimization_guard(
    redis_client: Redis,
    *,
    courier_id: str,
    lock_owner: str,
) -> tuple[bool, str | None, str]:
    """Return (allowed, skip_reason, lock_key)."""
    lock_key = f"optimization:reopt:{courier_id}:lock"
    cooldown_key = f"optimization:reopt:{courier_id}:cooldown"

    if redis_client.get(cooldown_key):
        return False, "COOLDOWN_ACTIVE", lock_key

    acquired = redis_client.set(
        lock_key,
        lock_owner,
        nx=True,
        ex=settings.REOPTIMIZATION_LOCK_SECONDS,
    )
    if not acquired:
        return False, "LOCKED", lock_key
    return True, None, lock_key


def _set_reoptimization_cooldown(redis_client: Redis, *, courier_id: str) -> None:
    cooldown_key = f"optimization:reopt:{courier_id}:cooldown"
    redis_client.set(
        cooldown_key,
        "1",
        ex=settings.REOPTIMIZATION_COOLDOWN_SECONDS,
    )


@celery_app.task(
    name="app.workers.optimization_tasks.run_batch_optimization",
    bind=True,
    max_retries=1,
    default_retry_delay=30,
)
def run_batch_optimization(
    self,
    draft: bool = False,
    departure_time_iso: str | None = None,
    tenant_id: str | None = None,
    date_from_iso: str | None = None,
    date_to_iso: str | None = None,
) -> dict:
    """Pull PENDING orders + ONLINE couriers, run VRP solver.

    When draft=True, creates DRAFT routes for dispatcher review.
    departure_time_iso: ISO-8601 string for target departure time (traffic profile).
    date_from_iso / date_to_iso: when provided, scope to a specific date range
      and include all order types (not just SCHEDULED/BULK).
    Returns a summary dict with route counts and solver status.
    """
    def run() -> dict:
        start = time.monotonic()
        task_id = self.request.id
        logger.info("Batch optimization started [task_id=%s, draft=%s]", task_id, draft)
        run_id = _run_async(_start_run(OptimizationRunType.BATCH, task_id, tenant_id)) if tenant_id else None

        lock_owner = str(uuid.uuid4())
        redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        if is_task_cancelled(redis_client, task_id=task_id):
            logger.info("Batch optimization skipped because it was superseded [task_id=%s]", task_id)
            result = {
                "routes_created": 0,
                "unassigned_count": 0,
                "solver_status": "SUPERSEDED",
            }
            if run_id:
                _run_async(
                    _finish_run(
                        run_id=run_id,
                        status_value=OptimizationRunStatus.FAILED,
                        result=result,
                        error_message=SUPERSEDED_OPTIMIZATION_MESSAGE,
                    )
                )
            clear_active_batch_optimization_task(redis_client, task_id=task_id)
            return result

        acquired = acquire_batch_optimization_lock(
            redis_client,
            owner=lock_owner,
            task_id=task_id,
            tenant_id=tenant_id,
            operation="BATCH",
            ttl_seconds=300,
        )
        if not acquired:
            logger.warning("Batch optimization skipped due to active lock")
            result = {
                "routes_created": 0,
                "unassigned_count": 0,
                "solver_status": "LOCKED",
            }
            _run_async(
                _finish_run(
                    run_id=run_id,
                    status_value=OptimizationRunStatus.LOCKED,
                    result=result,
                )
            )
            clear_active_batch_optimization_task(redis_client, task_id=task_id)
            return result

        try:
            result = _run_async(_do_batch_optimization(
                run_id,
                draft=draft,
                departure_time_iso=departure_time_iso,
                tenant_id=tenant_id,
                date_from_iso=date_from_iso,
                date_to_iso=date_to_iso,
            ))
            elapsed = time.monotonic() - start
            logger.info(
                "Batch optimization completed [task_id=%s] in %.1fs: "
                "%d routes, %d unassigned, status=%s",
                task_id, elapsed,
                result.get("routes_created", 0),
                result.get("unassigned_count", 0),
                result.get("solver_status", "UNKNOWN"),
            )
            final_status = (
                OptimizationRunStatus.PENDING_APPROVAL if draft
                else OptimizationRunStatus.SUCCEEDED
            )
            if run_id:
                _run_async(
                    _finish_run(
                        run_id=run_id,
                        status_value=final_status,
                        result=result,
                    )
                )
            return result
        except Exception as exc:
            logger.exception("Batch optimization failed [task_id=%s]", task_id)
            if run_id:
                _run_async(
                    _finish_run(
                        run_id=run_id,
                        status_value=OptimizationRunStatus.FAILED,
                        error_message=str(exc),
                    )
                )
            raise self.retry(exc=exc) if self.request.retries < self.max_retries else exc
        finally:
            try:
                release_owned_batch_optimization_lock(redis_client, owner=lock_owner)
            except Exception:
                logger.exception("Failed to release optimization lock")
            try:
                clear_active_batch_optimization_task(redis_client, task_id=task_id)
            except Exception:
                logger.exception("Failed to clear active optimization task")

    return _run_with_task_request_context(self.request, run)


@celery_app.task(
    name="app.workers.optimization_tasks.run_route_scenario_comparison",
    bind=True,
    max_retries=0,
    soft_time_limit=900,
    time_limit=960,
)
def run_route_scenario_comparison(
    self,
    *,
    comparison_id: str,
    tenant_id: str,
    target_date_iso: str,
) -> dict:
    """Generate the four daily draft route scenarios for dispatcher comparison."""
    def run() -> dict:
        from datetime import date

        task_id = self.request.id
        lock_owner = str(uuid.uuid4())
        redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        if is_task_cancelled(redis_client, task_id=task_id):
            logger.info("Route scenario comparison skipped because it was superseded [task_id=%s]", task_id)
            _run_async(
                _mark_route_scenario_comparison_failed(
                    comparison_id=comparison_id,
                    tenant_id=tenant_id,
                    error_message=SUPERSEDED_OPTIMIZATION_MESSAGE,
                )
            )
            clear_active_batch_optimization_task(redis_client, task_id=task_id)
            return {
                "status": "SUPERSEDED",
                "comparison_id": comparison_id,
                "solver_status": "SUPERSEDED",
            }

        acquired = acquire_batch_optimization_lock(
            redis_client,
            owner=lock_owner,
            task_id=task_id,
            tenant_id=tenant_id,
            operation="ROUTE_SCENARIO_COMPARISON",
            ttl_seconds=960,
            comparison_id=comparison_id,
        )
        if not acquired:
            logger.warning("Route scenario comparison skipped due to active batch lock")
            result = {
                "status": "LOCKED",
                "comparison_id": comparison_id,
                "solver_status": "LOCKED",
            }
            _run_async(
                _mark_route_scenario_comparison_locked(
                    comparison_id=comparison_id,
                    tenant_id=tenant_id,
                )
            )
            clear_active_batch_optimization_task(redis_client, task_id=task_id)
            return result

        try:
            from app.services.route_scenario_service import run_comparison

            logger.info("Route scenario comparison started [task_id=%s]", task_id)
            return _run_async(
                run_comparison(
                    comparison_id=uuid.UUID(comparison_id),
                    tenant_id=uuid.UUID(tenant_id),
                    target_date=date.fromisoformat(target_date_iso),
                )
            )
        except Exception as exc:
            error_message = _exception_message(exc, "Route scenario comparison failed")
            logger.exception(
                "Route scenario comparison failed [task_id=%s, comparison_id=%s]",
                task_id,
                comparison_id,
            )
            _run_async(
                _mark_route_scenario_comparison_failed(
                    comparison_id=comparison_id,
                    tenant_id=tenant_id,
                    error_message=error_message,
                )
            )
            raise
        finally:
            try:
                release_owned_batch_optimization_lock(redis_client, owner=lock_owner)
            except Exception:
                logger.exception("Failed to release route scenario comparison lock")
            try:
                clear_active_batch_optimization_task(redis_client, task_id=task_id)
            except Exception:
                logger.exception("Failed to clear active route scenario comparison task")

    return _run_with_task_request_context(self.request, run)


# Time limits must be wide enough to cover the worst legitimate case:
# a 180-day chunked solve = ~9 chunks × 900s medium-tier ceiling + overhead,
# plus a ~10% slack between soft and hard so the soft signal lands first
# and we get a clean shutdown path instead of SIGKILL mid-write.
#
# Daily batch uses 300s/360s and route-scenario-comparison uses 900s/960s;
# range is the longest-running task in the worker by design. Don't raise
# these globally — only this task gets the long ceiling.
_RANGE_TASK_SOFT_TIME_LIMIT_S = 10_800   # 3 h
_RANGE_TASK_HARD_TIME_LIMIT_S = 11_400   # 3 h 10 min
_RANGE_LOCK_TTL_S = _RANGE_TASK_SOFT_TIME_LIMIT_S


@celery_app.task(
    name="app.workers.optimization_tasks.run_range_batch_optimization",
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    soft_time_limit=_RANGE_TASK_SOFT_TIME_LIMIT_S,
    time_limit=_RANGE_TASK_HARD_TIME_LIMIT_S,
)
def run_range_batch_optimization(
    self,
    *,
    tenant_id: str,
    date_from_iso: str,
    date_to_iso: str,
    draft: bool = False,
    departure_time_iso: str | None = None,
) -> dict:
    """Range bulk dispatch — solve any date range as one VRP.

    Same locking discipline as run_batch_optimization (single global lock),
    but tracked under the WEEKLY_BATCH run type so reporting can distinguish
    the two. (The enum value is kept as `WEEKLY_BATCH` for historical
    consistency with already-persisted run records; the planner itself is
    horizon-agnostic.) The actual algorithm lives in
    `dispatch_range_service.dispatch_range`, so this entry-point stays
    focused on celery + lock + run lifecycle.
    """
    def run() -> dict:
        start = time.monotonic()
        task_id = self.request.id
        logger.info(
            "Range bulk optimization started [task_id=%s, draft=%s, range=%s..%s]",
            task_id, draft, date_from_iso, date_to_iso,
        )
        run_id = _run_async(
            _start_run(OptimizationRunType.WEEKLY_BATCH, task_id, tenant_id)
        )

        lock_owner = str(uuid.uuid4())
        # Use the same lock key as the daily flow — running both at once
        # would race for the same orders.
        redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        if is_task_cancelled(redis_client, task_id=task_id):
            logger.info("Range bulk optimization skipped because it was superseded [task_id=%s]", task_id)
            result = {
                "routes_created": 0,
                "unassigned_count": 0,
                "solver_status": "SUPERSEDED",
                "scope": "RANGE",
            }
            _run_async(
                _finish_run(
                    run_id=run_id,
                    status_value=OptimizationRunStatus.FAILED,
                    result=result,
                    error_message=SUPERSEDED_OPTIMIZATION_MESSAGE,
                )
            )
            clear_active_batch_optimization_task(redis_client, task_id=task_id)
            return result

        acquired = acquire_batch_optimization_lock(
            redis_client,
            owner=lock_owner,
            task_id=task_id,
            tenant_id=tenant_id,
            operation="WEEKLY_BATCH",
            ttl_seconds=_RANGE_LOCK_TTL_S,
        )
        if not acquired:
            logger.warning("Range bulk optimization skipped due to active lock")
            result = {
                "routes_created": 0,
                "unassigned_count": 0,
                "solver_status": "LOCKED",
                "scope": "RANGE",
            }
            _run_async(
                _finish_run(
                    run_id=run_id,
                    status_value=OptimizationRunStatus.LOCKED,
                    result=result,
                )
            )
            clear_active_batch_optimization_task(redis_client, task_id=task_id)
            return result

        try:
            result = _run_async(_do_range_optimization(
                run_id,
                tenant_id=tenant_id,
                date_from_iso=date_from_iso,
                date_to_iso=date_to_iso,
                draft=draft,
                departure_time_iso=departure_time_iso,
            ))
            elapsed = time.monotonic() - start
            logger.info(
                "Range bulk optimization completed [task_id=%s] in %.1fs: "
                "%d routes, %d unassigned, status=%s",
                task_id, elapsed,
                result.get("routes_created", 0),
                result.get("unassigned_count", 0),
                result.get("solver_status", "UNKNOWN"),
            )
            final_status = (
                OptimizationRunStatus.PENDING_APPROVAL if draft
                else OptimizationRunStatus.SUCCEEDED
            )
            _run_async(
                _finish_run(
                    run_id=run_id,
                    status_value=final_status,
                    result=result,
                )
            )
            return result
        except Exception as exc:
            logger.exception("Range bulk optimization failed [task_id=%s]", task_id)
            _run_async(
                _finish_run(
                    run_id=run_id,
                    status_value=OptimizationRunStatus.FAILED,
                    error_message=str(exc),
                )
            )
            raise self.retry(exc=exc) if self.request.retries < self.max_retries else exc
        finally:
            try:
                release_owned_batch_optimization_lock(redis_client, owner=lock_owner)
            except Exception:
                logger.exception("Failed to release optimization lock")
            try:
                clear_active_batch_optimization_task(redis_client, task_id=task_id)
            except Exception:
                logger.exception("Failed to clear active range optimization task")

    return _run_with_task_request_context(self.request, run)


async def _do_range_optimization(
    run_id: str,
    *,
    tenant_id: str,
    date_from_iso: str,
    date_to_iso: str,
    draft: bool = False,
    departure_time_iso: str | None = None,
) -> dict:
    from datetime import datetime

    from app.services.dispatch_range_service import dispatch_range

    return await dispatch_range(
        tenant_id=uuid.UUID(tenant_id),
        date_from=datetime.fromisoformat(date_from_iso),
        date_to=datetime.fromisoformat(date_to_iso),
        optimization_run_id=uuid.UUID(run_id),
        draft=draft,
        departure_time=(
            datetime.fromisoformat(departure_time_iso) if departure_time_iso else None
        ),
    )


@celery_app.task(
    name="app.workers.optimization_tasks.run_insertion_dispatch",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
)
def run_insertion_dispatch(self, order_id: str, tenant_id: str | None = None) -> dict:
    """Dispatch a single on-demand order via insertion heuristic."""
    def run() -> dict:
        logger.info("Insertion dispatch started for order %s", order_id)
        task_id = self.request.id
        run_id = _run_async(_start_run(OptimizationRunType.INSERTION, task_id, tenant_id)) if tenant_id else None

        try:
            result = _run_async(_do_insertion_dispatch(order_id))
            logger.info(
                "Insertion dispatch completed for order %s: %s",
                order_id, result.get("result", "UNKNOWN"),
            )
            status_value = OptimizationRunStatus.SUCCEEDED
            if result.get("result") in {"ORDER_NOT_FOUND", "ORDER_NOT_PENDING", "NO_ONLINE_COURIERS"}:
                status_value = OptimizationRunStatus.NO_OP
            if run_id:
                _run_async(
                    _finish_run(
                        run_id=run_id,
                        status_value=status_value,
                        result=result,
                    )
                )
            return result
        except Exception as exc:
            logger.exception("Insertion dispatch failed for order %s", order_id)
            if run_id:
                _run_async(
                    _finish_run(
                        run_id=run_id,
                        status_value=OptimizationRunStatus.FAILED,
                        error_message=str(exc),
                    )
                )
            raise self.retry(exc=exc) if self.request.retries < self.max_retries else exc

    return _run_with_task_request_context(self.request, run)


@celery_app.task(
    name="app.workers.optimization_tasks.run_reoptimization",
    bind=True,
    max_retries=1,
    default_retry_delay=10,
)
def run_reoptimization(self, courier_id: str, tenant_id: str | None = None) -> dict:
    """Handle courier going offline: abort route, re-dispatch orders."""
    def run() -> dict:
        logger.info("Re-optimization started for courier %s", courier_id)
        task_id = self.request.id
        run_id = _run_async(_start_run(OptimizationRunType.REOPTIMIZATION, task_id, tenant_id)) if tenant_id else None
        lock_owner = str(uuid.uuid4())
        redis_client: Redis | None = None
        lock_key: str | None = None

        try:
            redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
            allowed, skip_reason, lock_key = _try_acquire_reoptimization_guard(
                redis_client,
                courier_id=courier_id,
                lock_owner=lock_owner,
            )
            if not allowed:
                result = {
                    "routes_aborted": 0,
                    "orders_requeued": 0,
                    "result": skip_reason,
                }
                if run_id:
                    _run_async(
                        _finish_run(
                            run_id=run_id,
                            status_value=OptimizationRunStatus.NO_OP,
                            result=result,
                        )
                    )
                return result
        except Exception:
            logger.warning(
                "Re-optimization guardrails unavailable for courier %s; proceeding without lock/cooldown",
                courier_id,
            )
            redis_client = None

        try:
            result = _run_async(_do_reoptimization(courier_id, tenant_id))
            logger.info(
                "Re-optimization completed for courier %s: %s",
                courier_id, result,
            )
            if redis_client is not None and (
                result.get("routes_aborted", 0) > 0 or result.get("orders_requeued", 0) > 0
            ):
                try:
                    _set_reoptimization_cooldown(redis_client, courier_id=courier_id)
                except Exception:
                    logger.exception("Failed to set re-optimization cooldown for courier %s", courier_id)
            status_value = OptimizationRunStatus.SUCCEEDED
            if result.get("routes_aborted", 0) == 0 and result.get("orders_requeued", 0) == 0:
                status_value = OptimizationRunStatus.NO_OP
            if run_id:
                _run_async(
                    _finish_run(
                        run_id=run_id,
                        status_value=status_value,
                        result=result,
                    )
                )
            return result
        except Exception as exc:
            logger.exception("Re-optimization failed for courier %s", courier_id)
            if run_id:
                _run_async(
                    _finish_run(
                        run_id=run_id,
                        status_value=OptimizationRunStatus.FAILED,
                        error_message=str(exc),
                    )
                )
            raise self.retry(exc=exc) if self.request.retries < self.max_retries else exc
        finally:
            if redis_client is not None and lock_key:
                try:
                    _release_owned_lock(redis_client, key=lock_key, owner=lock_owner)
                except Exception:
                    logger.exception("Failed to release re-optimization lock for courier %s", courier_id)

    return _run_with_task_request_context(self.request, run)


@celery_app.task(
    name="app.workers.optimization_tasks.retry_stale_pending_orders",
    bind=True,
    max_retries=0,
)
def retry_stale_pending_orders(self) -> dict:
    """Re-attempt dispatch for PENDING orders that have been waiting too long."""
    def run() -> dict:
        logger.info("Stale pending order retry started")
        return _run_async(_do_retry_stale_pending())

    return _run_with_task_request_context(self.request, run)


# ---------------------------------------------------------------------------
# Async implementations (called via _run_async)
# ---------------------------------------------------------------------------

async def _do_batch_optimization(
    run_id: str | None,
    *,
    draft: bool = False,
    departure_time_iso: str | None = None,
    tenant_id: str | None = None,
    date_from_iso: str | None = None,
    date_to_iso: str | None = None,
) -> dict:
    """Core batch optimization logic."""
    from datetime import datetime

    from app.services.dispatch_service import dispatch_batch

    departure_time = (
        datetime.fromisoformat(departure_time_iso)
        if departure_time_iso
        else None
    )
    date_from = datetime.fromisoformat(date_from_iso) if date_from_iso else None
    date_to = datetime.fromisoformat(date_to_iso) if date_to_iso else None
    return await dispatch_batch(
        optimization_run_id=uuid.UUID(run_id) if run_id else None,
        draft=draft,
        departure_time=departure_time,
        tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
        date_from=date_from,
        date_to=date_to,
    )


async def _do_insertion_dispatch(order_id: str) -> dict:
    """Core insertion dispatch logic."""
    from app.services.dispatch_service import dispatch_on_demand
    return await dispatch_on_demand(order_id)


async def _do_reoptimization(courier_id: str, tenant_id: str | None = None) -> dict:
    """Core re-optimization logic."""
    from app.services.dispatch_service import handle_courier_offline
    return await handle_courier_offline(
        courier_id,
        tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
    )


async def _start_run(run_type: OptimizationRunType, task_id: str | None, tenant_id: str | None) -> str:
    if tenant_id is None:
        raise ValueError("tenant_id is required to create an optimization run")
    async with database.celery_async_session() as db:
        run = await optimization_run_service.create_run(
            db,
            tenant_id=uuid.UUID(tenant_id),
            run_type=run_type,
            task_id=task_id,
        )
        await db.commit()
        return str(run.id)


async def _finish_run(
    *,
    run_id: str,
    status_value: OptimizationRunStatus,
    result: dict | None = None,
    error_message: str | None = None,
) -> None:
    async with database.celery_async_session() as db:
        await optimization_run_service.complete_run(
            db,
            run_id=uuid.UUID(run_id),
            status_value=status_value,
            result=result,
            error_message=error_message,
        )
        await db.commit()


async def _mark_route_scenario_comparison_failed(
    *,
    comparison_id: str,
    tenant_id: str,
    error_message: str,
) -> None:
    from app.services.route_scenario_service import _mark_comparison_failed

    await _mark_comparison_failed(
        uuid.UUID(comparison_id),
        uuid.UUID(tenant_id),
        error_message,
        scenario_status="FAILED",
    )


async def _mark_route_scenario_comparison_locked(
    *,
    comparison_id: str,
    tenant_id: str,
) -> None:
    await _mark_route_scenario_comparison_failed(
        comparison_id=comparison_id,
        tenant_id=tenant_id,
        error_message="Another optimization is already running",
    )


async def _do_retry_stale_pending() -> dict:
    """Find PENDING orders older than the configured threshold and re-attempt dispatch.

    Only retries ON_DEMAND and REVERSE orders via insertion heuristic.
    SCHEDULED and BULK orders are intentionally excluded — they may be waiting
    for their delivery date and will be picked up by the batch optimizer once
    their earliest_delivery falls within DISPATCH_PLANNING_HORIZON_HOURS.
    """
    from datetime import datetime, timedelta

    from sqlalchemy import select as sa_select

    from app.models.enums import OrderStatus, OrderType
    from app.models.order import Order as OrderModel
    from app.services.dispatch_service import dispatch_on_demand

    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=settings.PENDING_RETRY_STALE_MINUTES)
    async with database.celery_async_session() as db:
        result = await db.execute(
            sa_select(OrderModel).where(
                OrderModel.status == OrderStatus.PENDING,
                OrderModel.type.in_([OrderType.ON_DEMAND, OrderType.REVERSE]),
                OrderModel.created_at < cutoff,
                OrderModel.earliest_delivery <= now,
                OrderModel.latest_delivery > now,
            ).limit(50)
        )
        stale_orders = list(result.scalars().all())

    retried = 0
    succeeded = 0
    for order in stale_orders:
        try:
            dispatch_result = await dispatch_on_demand(str(order.id))
            retried += 1
            if dispatch_result.get("courier_id"):
                succeeded += 1
        except Exception:
            logger.exception("Retry dispatch failed for order %s", order.id)

    logger.info(
        "Stale pending retry: %d eligible, %d retried, %d assigned",
        len(stale_orders), retried, succeeded,
    )
    return {
        "eligible": len(stale_orders),
        "retried": retried,
        "assigned": succeeded,
    }
