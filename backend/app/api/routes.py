import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_write_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.route import (
    DispatcherNoteRequest,
    OptimizationRunResponse,
    PodUploadUrlRequest,
    PodUploadUrlResponse,
    RouteGeometryResponse,
    RouteResponse,
    RouteStopResponse,
    RunPreviewResponse,
    StopCompleteRequest,
    StopFailRequest,
)
from app.services import (
    dispatch_service,
    operations_policy_service,
    optimization_control_service,
    optimization_run_service,
    route_execution_service,
    route_service,
)
from app.utils.auth import get_current_user, require_role
from app.workers.optimization_tasks import run_batch_optimization
from app.workers.task_dispatch import enqueue_task

router = APIRouter()

DispatcherDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))]


class OptimizeTaskResponse(BaseModel):
    task_id: str
    message: str


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
) -> None:
    optimization_control_service.record_active_batch_optimization_task(
        task_id=task_id,
        tenant_id=str(current_user.tenant_id),
        operation=operation,
    )


class StopReorderRequest(BaseModel):
    stop_ids: list[uuid.UUID]


@router.get("", response_model=list[RouteResponse])
async def list_routes(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
    response: Response,
    courier_id: uuid.UUID | None = None,
    status: str | None = Query(None, description="Comma-separated route statuses, e.g. PLANNED,ACTIVE"),
    created_after: datetime | None = Query(None, description="Only routes created on or after this timestamp"),
    route_date: date | None = Query(None, description="Only routes with at least one stop planned on this operational date"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """List routes, optionally filtered by courier, status, and creation date."""
    from app.models.enums import RouteStatus as RS
    statuses = None
    if status:
        statuses = [RS(s.strip()) for s in status.split(",")]
    total = await route_service.count_routes(
        db, current_user, courier_id=courier_id, statuses=statuses,
        created_after=created_after, route_date=route_date,
    )
    response.headers["X-Total-Count"] = str(total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    return await route_service.list_routes(
        db, current_user, courier_id=courier_id, statuses=statuses,
        created_after=created_after, route_date=route_date, skip=skip, limit=limit,
    )


@router.get("/optimization-runs", response_model=list[OptimizationRunResponse])
async def list_optimization_runs(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
    limit: int = Query(50, ge=1, le=200),
):
    """List recent optimization runs for dispatcher monitoring."""
    return await optimization_run_service.list_runs(db, current_user, limit=limit)


@router.get("/optimization-runs/{run_id}", response_model=OptimizationRunResponse)
async def get_optimization_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Get a single optimization run by ID."""
    return await optimization_run_service.get_run(db, run_id, current_user)


@router.get("/optimization-runs/{run_id}/preview", response_model=RunPreviewResponse)
async def preview_optimization_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Preview draft routes from an optimization run.

    Returns enriched route data with per-order details, ETA summaries,
    and constraint violation flags so dispatchers can review before
    approving or rejecting the run.
    """
    return await optimization_run_service.get_run_preview(db, run_id, current_user)


@router.post(
    "/optimize",
    response_model=OptimizeTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def optimize_routes(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Compatibility endpoint to trigger batch optimization."""
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
    return OptimizeTaskResponse(
        task_id=task.id,
        message=_restart_message("Batch optimization task enqueued", cancellation),
    )


@router.get("/{route_id}", response_model=RouteResponse)
async def get_route(
    route_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get route details with stops."""
    return await route_service.get_route(db, route_id, current_user=current_user)


@router.get("/{route_id}/geometry", response_model=RouteGeometryResponse)
async def get_route_geometry(
    route_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Get road-following route geometry as GeoJSON for dispatcher map overlays."""
    return await route_service.get_route_geometry(db, route_id, current_user)


@router.post("/{route_id}/start", response_model=RouteResponse)
async def start_route(
    route_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Start a planned route."""
    return await route_execution_service.start_route(db, route_id, current_user)


@router.post("/{route_id}/stops/{stop_id}/arrive", response_model=RouteStopResponse)
async def arrive_at_stop(
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Mark arrived at a route stop."""
    return await route_execution_service.arrive_at_stop(db, route_id, stop_id, current_user)


@router.post("/{route_id}/stops/{stop_id}/complete", response_model=RouteStopResponse)
async def complete_stop(
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    data: StopCompleteRequest | None = None,
):
    """Mark a route stop as completed."""
    return await route_execution_service.complete_stop(
        db,
        route_id,
        stop_id,
        current_user,
        photo_urls=data.photo_urls if data else None,
        photo_url=data.photo_url if data else None,
        signature_url=data.signature_url if data else None,
        receiver_name=data.receiver_name if data else None,
        otp_code=data.otp_code if data else None,
        pod_notes=data.notes if data else None,
    )


@router.post(
    "/{route_id}/stops/{stop_id}/pod/upload-url",
    response_model=PodUploadUrlResponse,
)
async def get_pod_upload_url(
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    data: PodUploadUrlRequest,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Issue a pre-signed URL for POD asset upload."""
    return await route_execution_service.create_pod_upload_url(
        db,
        route_id=route_id,
        stop_id=stop_id,
        current_user=current_user,
        file_type=data.file_type,
        content_type=data.content_type,
    )


@router.post("/{route_id}/stops/{stop_id}/fail", response_model=RouteStopResponse)
async def fail_stop(
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    data: StopFailRequest,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Mark a route stop as failed with reason."""
    return await route_execution_service.fail_stop(
        db,
        route_id,
        stop_id,
        current_user,
        data.failure_reason,
        data.custom_reason_label,
        data.notes,
    )


@router.patch("/{route_id}/stops/{stop_id}/dispatcher-note", response_model=RouteStopResponse)
async def set_dispatcher_note(
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    data: DispatcherNoteRequest,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Set or clear a dispatcher note on a route stop."""
    return await route_service.set_dispatcher_note(db, route_id, stop_id, data.dispatcher_note, current_user)


@router.put("/{route_id}/stops/reorder", response_model=list[RouteStopResponse])
async def reorder_stops(
    route_id: uuid.UUID,
    data: StopReorderRequest,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Reorder route stops. Accepts an ordered list of stop IDs."""
    return await route_service.reorder_stops(db, route_id, data.stop_ids, current_user)


@router.post("/optimize/preview", response_model=OptimizeTaskResponse)
async def optimize_preview(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Trigger batch optimization in preview (draft) mode."""
    cancellation = await _supersede_active_optimization(db, current_user)
    task = enqueue_task(
        run_batch_optimization,
        kwargs={"draft": True, "tenant_id": str(current_user.tenant_id)},
    )
    _record_active_optimization_task(
        task_id=task.id,
        current_user=current_user,
        operation="BATCH",
    )
    return OptimizeTaskResponse(
        task_id=task.id,
        message=_restart_message("Preview optimization queued", cancellation),
    )


@router.post("/optimization-runs/{run_id}/approve")
async def approve_run(
    run_id: uuid.UUID,
    current_user: DispatcherDep,
):
    """Approve a pending optimization run — promote DRAFT routes to PLANNED."""
    return await dispatch_service.approve_optimization_run(run_id, current_user)


@router.post("/optimization-runs/{run_id}/reject")
async def reject_run(
    run_id: uuid.UUID,
    current_user: DispatcherDep,
):
    """Reject a pending optimization run — delete DRAFT routes."""
    return await dispatch_service.reject_optimization_run(run_id, current_user)
