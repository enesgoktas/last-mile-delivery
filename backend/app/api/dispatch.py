"""Dispatch API — trigger and monitor route optimization."""
import uuid
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_write_db
from app.models.enums import OrderStatus, UserRole
from app.models.order import Order
from app.models.order_history import OrderStatusHistory
from app.models.route import Route, RouteStop
from app.models.sla_alert import SLAAlert
from app.models.user import User
from app.schemas.route import RunPreviewResponse
from app.schemas.route_scenario import (
    RouteScenarioComparisonResponse,
    RouteScenarioCreateResponse,
    RouteScenarioSelectRequest,
    RouteScenarioSelectResponse,
)
from app.services import (
    operations_policy_service,
    optimization_control_service,
    route_scenario_service,
)
from app.utils.auth import require_role
from app.utils.tenant_context import tenant_filter
from app.workers.celery_app import celery_app
from app.workers.optimization_tasks import (
    run_batch_optimization,
    run_range_batch_optimization,
    run_reoptimization,
    run_route_scenario_comparison,
)
from app.workers.task_dispatch import enqueue_task

router = APIRouter(prefix="/dispatch", tags=["dispatch"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class OptimizeResponse(BaseModel):
    task_id: str = Field(..., description="Celery task ID for polling")
    message: str


class UnassignResponse(BaseModel):
    unassigned_count: int
    message: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: dict | None = None


class ReoptimizeRequest(BaseModel):
    courier_id: uuid.UUID | None = Field(
        None, description="Specific courier to re-optimize for. "
                          "If omitted, re-runs full batch."
    )


class DispatchNowResponse(BaseModel):
    assigned: bool
    result: str = Field(..., description="'inserted' | 'new_route' | rejection reason")
    courier_id: str | None = None
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _normalize_task_result(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, BaseException):
        return {"error": str(value)}
    return {"value": str(value)}


async def _supersede_active_optimization(
    db: AsyncSession,
    current_user: User,
) -> optimization_control_service.SupersedeActiveOptimizationResult:
    cancellation = await optimization_control_service.supersede_active_batch_optimization_for_tenant(
        db,
        tenant_id=current_user.tenant_id,
    )
    if cancellation.blocked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=cancellation.detail or "Another optimization is already running",
        )
    return cancellation


def _restart_message(
    base_message: str,
    cancellation: optimization_control_service.SupersedeActiveOptimizationResult,
) -> str:
    if cancellation.cancelled:
        return f"{base_message}; previous optimization was cancelled"
    return base_message


def _record_active_optimization_task(
    *,
    task_id: str,
    current_user: User,
    operation: str,
    comparison_id: uuid.UUID | None = None,
) -> None:
    optimization_control_service.record_active_batch_optimization_task(
        task_id=task_id,
        tenant_id=str(current_user.tenant_id),
        operation=operation,
        comparison_id=str(comparison_id) if comparison_id is not None else None,
    )

@router.post(
    "/optimize",
    response_model=OptimizeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger batch route optimization",
)
async def trigger_batch_optimization(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
    departure_time: datetime | None = Query(
        None,
        description="Target departure time (ISO-8601) for traffic-aware routing. "
                    "Defaults to current time if omitted.",
    ),
) -> OptimizeResponse:
    """Enqueue a batch optimization run for all PENDING scheduled orders.

    Returns a task ID that can be polled via GET /optimize/{task_id}/status.
    """
    cancellation = await _supersede_active_optimization(db, current_user)
    kwargs: dict = {}
    if departure_time is not None:
        kwargs["departure_time_iso"] = departure_time.isoformat()
    operations_policy = await operations_policy_service.get_operations_policy(db, current_user)
    kwargs["draft"] = operations_policy_service.should_require_manual_route_approval(operations_policy)
    kwargs["tenant_id"] = str(current_user.tenant_id)
    task = enqueue_task(run_batch_optimization, kwargs=kwargs)
    _record_active_optimization_task(
        task_id=task.id,
        current_user=current_user,
        operation="BATCH",
    )
    return OptimizeResponse(
        task_id=task.id,
        message=_restart_message("Batch optimization task enqueued", cancellation),
    )


@router.get(
    "/optimize/{task_id}/status",
    response_model=TaskStatusResponse,
    summary="Poll optimization task status",
)
async def get_optimization_status(
    task_id: str,
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
) -> TaskStatusResponse:
    """Query the status and result of an optimization task."""
    result = celery_app.AsyncResult(task_id)
    response = TaskStatusResponse(
        task_id=task_id,
        status=result.status,
        result=None,
    )
    if result.ready():
        try:
            response.result = _normalize_task_result(result.result)
        except Exception as exc:
            response.result = {"error": str(exc)}
    elif result.status == "PROGRESS":
        # Range chunking emits in-flight progress meta via update_state().
        # Surface it as the polled result so the UI can render "chunk X / N"
        # without waiting for the whole run to finish.
        info = result.info
        if isinstance(info, dict):
            response.result = info
    return response


@router.post(
    "/reoptimize",
    response_model=OptimizeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger re-optimization",
)
async def trigger_reoptimization(
    body: ReoptimizeRequest,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
) -> OptimizeResponse:
    """Re-optimize routes.

    If a courier_id is provided, handles that specific courier going offline
    (aborts their routes, re-queues orders). Otherwise, runs a full batch
    re-optimization.
    """
    if body.courier_id:
        task = enqueue_task(
            run_reoptimization,
            args=[str(body.courier_id), str(current_user.tenant_id)],
        )
        msg = f"Re-optimization enqueued for courier {body.courier_id}"
    else:
        cancellation = await _supersede_active_optimization(db, current_user)
        operations_policy = await operations_policy_service.get_operations_policy(db, current_user)
        task = enqueue_task(
            run_batch_optimization,
            kwargs={
                "tenant_id": str(current_user.tenant_id),
                "draft": operations_policy_service.should_require_manual_route_approval(operations_policy),
            },
        )
        _record_active_optimization_task(
            task_id=task.id,
            current_user=current_user,
            operation="BATCH",
        )
        msg = _restart_message("Full batch re-optimization enqueued", cancellation)

    return OptimizeResponse(task_id=task.id, message=msg)


@router.post(
    "/assign-optimal",
    response_model=OptimizeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Optimally assign all pending orders for a specific date",
)
async def assign_optimal(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
    target_date: date = Query(
        ...,
        description="Target date (ISO-8601, e.g. 2026-04-12). All PENDING orders with "
                    "earliest_delivery on this date will be assigned to online couriers.",
    ),
) -> OptimizeResponse:
    """Directly assign all PENDING orders for target_date to couriers via VRP solver.

    Unlike /optimize and /optimize/preview, this endpoint:
    - Always runs with draft=False (orders go straight to ASSIGNED — no review step)
    - Includes all order types (ON_DEMAND, SCHEDULED, BULK, REVERSE)
    - Scopes to the given calendar day only
    - Re-running is safe: already-assigned orders are untouched
    """
    cancellation = await _supersede_active_optimization(db, current_user)
    date_from = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
    date_to = date_from + timedelta(days=1)

    task = enqueue_task(
        run_batch_optimization,
        kwargs={
            "tenant_id": str(current_user.tenant_id),
            "draft": False,
            "date_from_iso": date_from.isoformat(),
            "date_to_iso": date_to.isoformat(),
        },
    )
    _record_active_optimization_task(
        task_id=task.id,
        current_user=current_user,
        operation="BATCH",
    )
    return OptimizeResponse(
        task_id=task.id,
        message=_restart_message(
            f"Optimal assignment started for {target_date.isoformat()}",
            cancellation,
        ),
    )


@router.post(
    "/scenario-comparisons",
    response_model=RouteScenarioCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate four route scenario drafts for one day",
)
async def create_route_scenario_comparison(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
    target_date: date = Query(..., description="Daily comparison target date (YYYY-MM-DD)."),
) -> RouteScenarioCreateResponse:
    cancellation = await _supersede_active_optimization(db, current_user)
    comparison = await route_scenario_service.create_comparison(
        db,
        principal=current_user,
        target_date=target_date,
    )
    task = enqueue_task(
        run_route_scenario_comparison,
        kwargs={
            "comparison_id": str(comparison.id),
            "tenant_id": str(current_user.tenant_id),
            "target_date_iso": target_date.isoformat(),
        },
    )
    _record_active_optimization_task(
        task_id=task.id,
        current_user=current_user,
        operation="ROUTE_SCENARIO_COMPARISON",
        comparison_id=comparison.id,
    )
    comparison = await route_scenario_service.set_comparison_task_id(
        db,
        comparison_id=comparison.id,
        task_id=task.id,
        principal=current_user,
    )
    return RouteScenarioCreateResponse(
        comparison=RouteScenarioComparisonResponse.model_validate(comparison),
        task_id=task.id,
        message=_restart_message(
            f"Route comparison started for {target_date.isoformat()}",
            cancellation,
        ),
    )


@router.get(
    "/scenario-comparisons/{comparison_id}",
    response_model=RouteScenarioComparisonResponse,
    summary="Get a route scenario comparison",
)
async def get_route_scenario_comparison(
    comparison_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
) -> RouteScenarioComparisonResponse:
    return await route_scenario_service.get_comparison(db, comparison_id, current_user)


@router.get(
    "/scenario-comparisons/{comparison_id}/scenarios/{scenario_id}/preview",
    response_model=RunPreviewResponse,
    summary="Preview draft routes for one scenario",
)
async def get_route_scenario_preview(
    comparison_id: uuid.UUID,
    scenario_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
) -> RunPreviewResponse:
    return await route_scenario_service.get_scenario_preview(
        db,
        comparison_id=comparison_id,
        scenario_id=scenario_id,
        principal=current_user,
    )


@router.post(
    "/scenario-comparisons/{comparison_id}/select",
    response_model=RouteScenarioSelectResponse,
    summary="Promote one route scenario as the day's plan",
)
async def select_route_scenario(
    comparison_id: uuid.UUID,
    body: RouteScenarioSelectRequest,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
) -> RouteScenarioSelectResponse:
    comparison, approved_routes, deleted_routes = await route_scenario_service.select_scenario(
        db,
        comparison_id=comparison_id,
        scenario_id=body.scenario_id,
        principal=current_user,
    )
    return RouteScenarioSelectResponse(
        comparison=comparison,
        approved_routes=approved_routes,
        deleted_draft_routes=deleted_routes,
    )


@router.delete(
    "/scenario-comparisons/{comparison_id}",
    summary="Discard a route scenario comparison and its draft routes",
)
async def delete_route_scenario_comparison(
    comparison_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
) -> dict[str, int | str]:
    deleted_routes = await route_scenario_service.delete_comparison(
        db,
        comparison_id=comparison_id,
        principal=current_user,
    )
    return {
        "deleted_draft_routes": deleted_routes,
        "message": "Scenario comparison discarded",
    }


class AssignRangeResponse(BaseModel):
    task_id: str = Field(..., description="Celery task ID for polling")
    message: str
    span_days: int


# The range planner has no quality cap on horizon length — only a sanity
# upper bound to guard against obvious typos (e.g. "year 9999"). The
# underlying service handles small / medium / large tiers internally; the
# `range_chunking` orchestrator kicks in beyond the medium tier so very
# long ranges are still served, just chunked.
RANGE_MAX_SPAN_DAYS = 180


async def _enqueue_range_optimization(
    db: AsyncSession,
    current_user: User,
    date_from: date,
    date_to: date,
    draft: bool = False,
) -> AssignRangeResponse:
    """Shared body for the `/assign-range` and `/assign-week` (alias) handlers."""
    if date_to < date_from:
        raise HTTPException(
            status_code=422, detail="date_to must be on or after date_from"
        )
    span_days = (date_to - date_from).days + 1
    if span_days > RANGE_MAX_SPAN_DAYS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Range planning safety net is {RANGE_MAX_SPAN_DAYS} days "
                f"(requested {span_days}). Split into multiple smaller requests."
            ),
        )

    cancellation = await _supersede_active_optimization(db, current_user)
    date_from_dt = datetime(date_from.year, date_from.month, date_from.day)
    date_to_dt = datetime(date_to.year, date_to.month, date_to.day) + timedelta(days=1)

    task = enqueue_task(
        run_range_batch_optimization,
        kwargs={
            "tenant_id": str(current_user.tenant_id),
            "date_from_iso": date_from_dt.isoformat(),
            "date_to_iso": date_to_dt.isoformat(),
            "draft": draft,
        },
    )
    _record_active_optimization_task(
        task_id=task.id,
        current_user=current_user,
        operation="WEEKLY_BATCH",  # kept for back-compat with persisted run records
    )
    mode_label = "preview" if draft else "commit"
    return AssignRangeResponse(
        task_id=task.id,
        message=_restart_message(
            f"Range planner enqueued ({mode_label}) for "
            f"{date_from.isoformat()}..{date_to.isoformat()} ({span_days} days)",
            cancellation,
        ),
        span_days=span_days,
    )


@router.post(
    "/assign-range",
    response_model=AssignRangeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Optimally assign all pending orders across any date range",
)
async def assign_range(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
    date_from: date = Query(
        ...,
        description="Inclusive start date (ISO-8601). The solver may schedule "
                    "eligible work from this day onwards.",
    ),
    date_to: date = Query(
        ...,
        description="Inclusive end date (ISO-8601). No quality cap; a safety net "
                    "of 180 days rejects obvious typos.",
    ),
    draft: bool = Query(
        False,
        description="When true, create routes with status=DRAFT and leave "
                    "orders in PENDING. Dispatchers can inspect the proposed "
                    "plan and Approve via `/optimization-runs/{id}/approve` "
                    "to promote the routes to PLANNED.",
    ),
) -> AssignRangeResponse:
    """Run the range planner across [date_from, date_to] inclusive.

    Unlike `/dispatch/assign-optimal`, this endpoint:

      * Solves the entire range as **one** VRP, so the optimiser is free
        to swap delivery dates across days when an order's
        `[earliest_delivery, latest_delivery]` window allows it.
      * Handles short (3-day), medium (30-day), and long (3-month) horizons
        — the underlying service tiers the strategy automatically.
      * Honours every order constraint: time windows, weight/volume,
        fragile/temp-sensitive flags, and OSRM vehicle profiles.
      * Returns a celery task id you can poll via
        `GET /dispatch/optimize/{task_id}/status`, identical to the daily flow.

    Re-running for an overlapping range is safe: existing PLANNED/DRAFT route
    assignments inside the range are reset first, while work already in
    progress or completed is left untouched.
    """
    return await _enqueue_range_optimization(
        db, current_user, date_from, date_to, draft=draft
    )


@router.post(
    "/assign-week",
    response_model=AssignRangeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
    summary="Deprecated alias for /assign-range (kept for back-compat)",
)
async def assign_week(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
    date_from: date = Query(...),
    date_to: date = Query(...),
    draft: bool = Query(False),
) -> AssignRangeResponse:
    """Deprecated alias for `/dispatch/assign-range`. The "week" framing is
    historical; the underlying planner accepts any horizon up to the
    `RANGE_MAX_SPAN_DAYS` safety net. New callers should use `/assign-range`.
    """
    return await _enqueue_range_optimization(
        db, current_user, date_from, date_to, draft=draft
    )


@router.post(
    "/unassign",
    response_model=UnassignResponse,
    status_code=status.HTTP_200_OK,
    summary="Unassign all assigned orders for a specific date",
)
async def unassign_date(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
    target_date: date = Query(
        ...,
        description="Target date (ISO-8601). All ASSIGNED orders with earliest_delivery "
                    "on this date are reset to PENDING. If scheduled_for_date is set, "
                    "that operational day takes precedence. IN_TRANSIT and DELIVERED "
                    "orders are never touched.",
    ),
) -> UnassignResponse:
    """Reset ASSIGNED orders for target_date back to PENDING.

    Safe to call at any time — only touches orders in ASSIGNED status.
    Orders already picked up (IN_TRANSIT) or delivered are left untouched.
    Routes that become empty after unassignment are deleted.
    """
    date_from = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
    date_to = date_from + timedelta(days=1)
    service_day_filter = or_(
        Order.scheduled_for_date == target_date,
        and_(
            Order.scheduled_for_date.is_(None),
            Order.earliest_delivery < date_to,
            Order.latest_delivery >= date_from,
        ),
    )

    # 1. Find ASSIGNED orders for this date belonging to this tenant
    order_rows = (await db.execute(
        select(Order.id, Order.route_id).where(
            Order.status == OrderStatus.ASSIGNED,
            service_day_filter,
            tenant_filter(Order, str(current_user.tenant_id)),
        )
    )).all()

    if not order_rows:
        return UnassignResponse(unassigned_count=0, message=f"No assigned orders found for {target_date.isoformat()}")

    order_ids = [r[0] for r in order_rows]
    route_ids = list({r[1] for r in order_rows if r[1] is not None})

    # 2. Delete SLA alerts tied to stops in those routes
    if route_ids:
        stop_ids = (await db.execute(
            select(RouteStop.id).where(
                RouteStop.route_id.in_(route_ids),
                RouteStop.order_id.in_(order_ids),
            )
        )).scalars().all()
        if stop_ids:
            await db.execute(delete(SLAAlert).where(SLAAlert.stop_id.in_(stop_ids)))

    # 3. Remove ASSIGNED status history entries for these orders
    await db.execute(
        delete(OrderStatusHistory).where(
            OrderStatusHistory.order_id.in_(order_ids),
            OrderStatusHistory.to_status == "ASSIGNED",
        )
    )

    # 4. Reset orders to PENDING
    await db.execute(
        update(Order)
        .where(Order.id.in_(order_ids))
        .values(
            status=OrderStatus.PENDING,
            assigned_courier_id=None,
            route_id=None,
            scheduled_for_date=None,
        )
    )

    # 5. Delete selected stops, then routes that are now fully empty. This
    # prevents stale planned geometry from lingering after a day/range is
    # replanned.
    if route_ids:
        await db.execute(delete(RouteStop).where(RouteStop.order_id.in_(order_ids)))
        for route_id in route_ids:
            active_stops = (await db.execute(
                select(RouteStop.id).where(
                    RouteStop.route_id == route_id,
                ).limit(1)
            )).scalar()
            if active_stops is None:
                await db.execute(delete(Route).where(Route.id == route_id))

    await db.commit()

    return UnassignResponse(
        unassigned_count=len(order_ids),
        message=f"Unassigned {len(order_ids)} order{'s' if len(order_ids) != 1 else ''} for {target_date.isoformat()}",
    )


@router.post(
    "/dispatch-now/{order_id}",
    response_model=DispatchNowResponse,
    status_code=status.HTTP_200_OK,
    summary="Insert a single PENDING order into the best available existing route",
)
async def dispatch_now(
    order_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[
        User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
    ],
) -> DispatchNowResponse:
    """Slot a single PENDING order into the best available route using the
    insertion heuristic — without touching any already-assigned orders.

    Unlike /assign-optimal this does NOT re-run the full VRP solver and does
    NOT disturb existing routes.  It finds the cheapest feasible insertion
    point across all active courier routes and slots the order in.  If no
    existing route can absorb the order a new single-order route is created
    for the closest available courier.

    Returns synchronously (no task polling needed).
    """
    # Verify the order exists and belongs to this tenant before dispatching.
    row = await db.execute(
        select(Order).where(
            Order.id == order_id,
            tenant_filter(Order, str(current_user.tenant_id)),
        )
    )
    order = row.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if order.status.value != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order is {order.status.value}, not PENDING — cannot dispatch",
        )

    from app.services.dispatch_service import dispatch_on_demand
    result = await dispatch_on_demand(str(order_id))

    dispatch_result = result.get("result", "")
    assigned = dispatch_result in ("inserted", "new_route")
    courier = result.get("courier_id")

    if assigned:
        message = (
            f"Order slotted into an existing route for courier {str(courier)[:8]}"
            if dispatch_result == "inserted"
            else f"New route created for courier {str(courier)[:8]}"
        )
    else:
        reason_map = {
            "OSRM_UNAVAILABLE": "Routing service is unavailable — try again shortly",
            "ORDER_NOT_FOUND": "Order not found",
            "ORDER_NOT_PENDING": "Order is no longer pending",
            "NO_ONLINE_COURIERS": "No couriers are online right now",
            "NO_ELIGIBLE_COURIERS_IN_ZONE": "No couriers available in this delivery zone",
            "no_eligible_courier": "No courier could fit this order within their capacity and time window",
        }
        message = reason_map.get(dispatch_result, f"Could not assign: {dispatch_result}")

    return DispatchNowResponse(
        assigned=assigned,
        result=dispatch_result,
        courier_id=str(courier) if courier else None,
        message=message,
    )
