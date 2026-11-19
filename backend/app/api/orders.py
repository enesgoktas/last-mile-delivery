import io
import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_write_db
from app.models.enums import (
    FailedOrderCustomerResponseStatus,
    OrderPriority,
    OrderStatus,
    OrderType,
    UserRole,
)
from app.models.order_leg import OrderLeg
from app.models.user import User
from app.schemas.import_job import ImportPreviewResponse, ImportPublishResponse
from app.schemas.order import (
    OrderAssign,
    OrderCreate,
    OrderPreferredCourierUpdate,
    OrderReschedule,
    OrderResponse,
    OrderReturnCreate,
    OrderStatusHistoryResponse,
    OrderUpdate,
)
from app.schemas.forensics import ForensicsResponse
from app.schemas.order_leg import OrderLegResponse
from app.schemas.order_rating import OrderRatingCreate, OrderRatingResponse
from app.services import forensics_service, import_service, order_service
from app.utils.access import AccessContext, require_dispatcher_or_api_scope
from app.utils.auth import require_role

router = APIRouter()

DispatcherDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))]


@router.post("", response_model=OrderResponse, status_code=201)
async def create_order(
    data: OrderCreate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    access: Annotated[AccessContext, Depends(require_dispatcher_or_api_scope("orders:create"))],
):
    """Create a new order."""
    actor_user_id = access.user.id if access.user else None
    principal = access.user or access.api_client
    return await order_service.create_order(
        db,
        data,
        tenant_id=principal,
        actor_user_id=actor_user_id,
    )


@router.get("", response_model=list[OrderResponse])
async def list_orders(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    response: Response,
    current_user: DispatcherDep,
    status: OrderStatus | None = None,
    type: OrderType | None = None,
    priority: OrderPriority | None = None,
    customer_response_status: FailedOrderCustomerResponseStatus | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    delivery_from: datetime | None = None,
    delivery_to: datetime | None = None,
    service_date: date | None = Query(
        None,
        description=(
            "Operational service date. Assigned orders match scheduled_for_date; "
            "unscheduled orders fall back to delivery-window overlap."
        ),
    ),
    search: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """List orders with optional filters and search."""
    filter_kwargs = {
        "status_filter": status,
        "type_filter": type,
        "priority_filter": priority,
        "customer_response_status_filter": customer_response_status,
        "created_from": created_from,
        "created_to": created_to,
        "delivery_from": delivery_from,
        "delivery_to": delivery_to,
        "service_date": service_date,
        "search": search,
    }
    total = await order_service.count_orders(db, current_user, **filter_kwargs)
    response.headers["X-Total-Count"] = str(total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    return await order_service.get_orders(db, current_user, **filter_kwargs, skip=skip, limit=limit)


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    access: Annotated[AccessContext, Depends(require_dispatcher_or_api_scope("orders:read"))],
):
    """Get order details."""
    return await order_service.get_order(db, order_id, access.user or access.api_client)


@router.get("/{order_id}/history", response_model=list[OrderStatusHistoryResponse])
async def get_order_history(
    order_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Get order status transition history."""
    return await order_service.get_order_status_history(db, order_id, current_user)


@router.get("/{order_id}/forensics", response_model=ForensicsResponse)
async def get_order_forensics(
    order_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
) -> ForensicsResponse:
    """Return the full forensic timeline for one order.

    One round-trip pulls together: status history, route + stops,
    per-stop status history with lat/lon coordinates, dispatch
    decisions (chosen courier + alternatives + score), and ETA
    predictions vs actual arrival. Drives the OrderForensicsDrawer
    that answers "what happened to this order?" without log-diving.
    """
    payload = await forensics_service.get_order_forensics(
        db, order_id=order_id, principal=current_user
    )
    return ForensicsResponse.model_validate(payload)


@router.post(
    "/{order_id}/rating",
    response_model=OrderRatingResponse,
    status_code=201,
)
async def rate_order(
    order_id: uuid.UUID,
    payload: OrderRatingCreate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    access: Annotated[
        AccessContext,
        Depends(require_dispatcher_or_api_scope("orders:read")),
    ],
) -> OrderRatingResponse:
    """Record a customer satisfaction rating for a delivered order.

    Idempotent — re-posting refreshes the existing rating instead of
    creating duplicates. The SMS / app flow that prompts customers is
    deferred; for the pilot, ratings can come from any source (CSAT
    survey, manual operator entry, future SMS link)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.order import Order
    from app.models.order_rating import OrderRating
    from app.utils.tenant_context import tenant_filter

    # Confirm the order belongs to the caller's tenant before writing
    # a rating. tenant_filter raises if the row's tenant doesn't match.
    order = (
        await db.execute(
            select(Order).where(
                Order.id == order_id,
                tenant_filter(Order, access.user or access.api_client),
            )
        )
    ).scalar_one_or_none()
    if order is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Order not found")

    values = {
        "tenant_id": order.tenant_id,
        "order_id": order_id,
        "rating": payload.rating,
        "feedback_text": payload.feedback_text,
    }
    stmt = (
        pg_insert(OrderRating)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["order_id"],
            set_={
                "rating": values["rating"],
                "feedback_text": values["feedback_text"],
            },
        )
        .returning(OrderRating)
    )
    row = (await db.execute(stmt)).scalar_one()
    await db.commit()
    return OrderRatingResponse.model_validate(row)


@router.post("/import/preview", response_model=ImportPreviewResponse)
@router.post("/import", response_model=ImportPreviewResponse)
async def preview_order_import(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
    file: UploadFile = File(...),
):
    """Parse and validate CSV import; persist preview payload."""
    raw = await file.read()
    max_import_bytes = 10 * 1024 * 1024  # 10 MB
    if len(raw) > max_import_bytes:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=413,
            detail="File too large. Maximum allowed size is 10 MB.",
        )
    content = raw.decode("utf-8")
    job = await import_service.preview_csv_import(
        db,
        actor_user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        csv_content=content,
    )
    return ImportPreviewResponse(
        import_id=job.id,
        total_rows=job.total_rows,
        valid_rows=job.valid_rows,
        invalid_rows=job.invalid_rows,
        valid_preview=job.payload[:50],
        errors=job.invalid_details,
    )


@router.post("/import/{import_id}/publish", response_model=ImportPublishResponse)
async def publish_order_import(
    import_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Publish a previewed import job and create orders."""
    job, created_ids = await import_service.publish_import_job(
        db,
        import_id=import_id,
        actor_user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )
    return ImportPublishResponse(
        import_id=job.id,
        created_count=len(created_ids),
        created_order_ids=created_ids,
        published_at=job.published_at,
    )


@router.get("/import/{import_id}/errors.csv")
async def download_order_import_errors(
    import_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Download invalid CSV rows with row-level validation errors."""
    job = await import_service.get_import_job_for_user(
        db,
        import_id=import_id,
        actor_user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )
    csv_content = import_service.render_invalid_rows_csv(job)
    buffer = io.BytesIO(csv_content.encode("utf-8"))
    filename = f"import_{import_id}_errors.csv"
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/{order_id}", response_model=OrderResponse)
async def update_order(
    order_id: uuid.UUID,
    data: OrderUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Update an order (pre-assignment only)."""
    return await order_service.update_order(db, order_id, data, current_user)


@router.post("/{order_id}/assign", response_model=OrderResponse)
async def assign_order(
    order_id: uuid.UUID,
    data: OrderAssign,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Manually assign a courier to an order."""
    return await order_service.assign_order(
        db,
        order_id,
        data.courier_id,
        principal=current_user,
        actor_user_id=current_user.id,
    )


@router.put("/{order_id}/preferred-courier", response_model=OrderResponse)
async def set_preferred_courier(
    order_id: uuid.UUID,
    data: OrderPreferredCourierUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Pin or clear a preferred courier for later optimization."""
    return await order_service.set_preferred_courier(
        db,
        order_id,
        data.courier_id,
        principal=current_user,
    )


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(
    order_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Cancel an order."""
    return await order_service.cancel_order(
        db,
        order_id,
        principal=current_user,
        actor_user_id=current_user.id,
    )


@router.post("/{order_id}/reschedule", response_model=OrderResponse)
async def reschedule_order(
    order_id: uuid.UUID,
    data: OrderReschedule,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Reschedule a failed order."""
    return await order_service.reschedule_order(
        db,
        order_id,
        data.earliest_delivery,
        data.latest_delivery,
        principal=current_user,
        actor_user_id=current_user.id,
    )


@router.post("/{order_id}/returns", response_model=OrderResponse, status_code=201)
async def create_return_order(
    order_id: uuid.UUID,
    data: OrderReturnCreate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Create a reverse-logistics order linked to a delivered order."""
    return await order_service.create_return_order(
        db,
        order_id,
        principal=current_user,
        earliest_pickup=data.earliest_pickup,
        latest_pickup=data.latest_pickup,
        reason_code=data.reason_code,
        actor_user_id=current_user.id,
    )


@router.get("/{order_id}/legs", response_model=list[OrderLegResponse])
async def list_order_legs(
    order_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Return the multi-depot legs for an order, ordered by sequence number."""
    order = await order_service.get_order(db, order_id, principal=current_user)
    result = await db.execute(
        select(OrderLeg)
        .where(OrderLeg.order_id == order.id)
        .order_by(OrderLeg.sequence_number)
    )
    return result.scalars().all()
