from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import HTTPException

from app.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.analytics import (
    AnalyticsSummaryResponse,
    CostSavingsResponse,
    CourierAnalyticsTableResponse,
    ExceptionCenterSummaryResponse,
    OptimizationSavingsResponse,
    RuntimePerformanceSnapshotResponse,
    SolverQualityResponse,
)
from app.schemas.ride_analytics import (
    AirportPunctualityResponse,
    FleetUtilizationResponse,
    RideAnalyticsResponse,
)
from app.services import analytics_service, operations_policy_service
from app.utils.auth import require_role

router = APIRouter(prefix="/analytics", tags=["analytics"])
DispatcherDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))]


@router.get("/summary", response_model=AnalyticsSummaryResponse)
async def get_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    return await analytics_service.get_summary(db, current_user, date_from=date_from, date_to=date_to)


@router.get("/couriers", response_model=CourierAnalyticsTableResponse)
async def get_courier_table(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    rows = await analytics_service.get_courier_table(db, current_user, date_from=date_from, date_to=date_to)
    return CourierAnalyticsTableResponse(rows=rows)


@router.get("/exception-center", response_model=ExceptionCenterSummaryResponse)
async def get_exception_center_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
):
    return await analytics_service.get_exception_center_summary(db, current_user)


@router.get("/optimization-savings", response_model=OptimizationSavingsResponse)
async def get_optimization_savings(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """Aggregate route-optimization savings (baseline vs selected scenario)
    over the given range.  Drives the Insights "Optimization gains" panel.
    """
    return await analytics_service.get_optimization_savings(
        db, current_user, date_from=date_from, date_to=date_to
    )


@router.get("/solver-quality", response_model=SolverQualityResponse)
async def get_solver_quality(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """ETA accuracy + failure-cause breakdown + plan stability in one
    round-trip. Feeds the Layer 2/3 cards in the Optimization-gains
    Insights tab."""
    return await analytics_service.get_solver_quality(
        db, current_user, date_from=date_from, date_to=date_to
    )


@router.get("/cost-savings", response_model=CostSavingsResponse)
async def get_cost_savings(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """Fuel cost (TRY) and CO₂ totals over the date range — the pilot's
    headline KPI surface. Reads from `route_performance_snapshot` so
    response latency stays flat as the route table grows.

    Counterfactual savings (`fuel_cost_saved_try`, `co2_saved_kg`) stay
    NULL until Layer 2's greedy baseline lands alongside every solve.
    """
    return await analytics_service.get_cost_savings(
        db, current_user, date_from=date_from, date_to=date_to
    )


@router.get("/rides", response_model=RideAnalyticsResponse)
async def get_ride_analytics(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """Phase 2 ride analytics — only available when the tenant has rides_enabled."""
    policy = await operations_policy_service.get_operations_policy(db, current_user)
    if not policy.feature_flags.rides_enabled:
        raise HTTPException(
            status_code=403,
            detail="Passenger transport is not enabled for this tenant.",
        )
    return await analytics_service.get_ride_analytics(
        db, current_user, date_from=date_from, date_to=date_to
    )


@router.get("/fleet-utilization", response_model=FleetUtilizationResponse)
async def get_fleet_utilization(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """Phase 2 fleet utilization — per-courier route count, distance, hours,
    order count. Available to every tenant (no flag gate)."""
    return await analytics_service.get_fleet_utilization(
        db, current_user, date_from=date_from, date_to=date_to
    )


@router.get("/airport-punctuality", response_model=AirportPunctualityResponse)
async def get_airport_punctuality(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """Phase 2 airport pickup punctuality by terminal. Requires both
    `rides_enabled` and `ride_airport_features_enabled`."""
    policy = await operations_policy_service.get_operations_policy(db, current_user)
    flags = policy.feature_flags
    if not flags.rides_enabled:
        raise HTTPException(
            status_code=403,
            detail="Passenger transport is not enabled for this tenant.",
        )
    if not flags.ride_airport_features_enabled:
        raise HTTPException(
            status_code=403,
            detail="Airport features are not enabled for this tenant.",
        )
    return await analytics_service.get_airport_punctuality(
        db, current_user, date_from=date_from, date_to=date_to
    )


@router.get("/export")
async def export_csv(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    csv_data = await analytics_service.export_orders_csv(db, current_user, date_from=date_from, date_to=date_to)
    return StreamingResponse(
        iter([csv_data]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders_export.csv"},
    )


@router.get("/runtime-performance", response_model=RuntimePerformanceSnapshotResponse)
async def runtime_performance_snapshot(
    _user: DispatcherDep,
):
    return analytics_service.get_runtime_performance_snapshot()


@router.get(
    "/runtime-performance/prometheus",
    response_class=PlainTextResponse,
)
async def runtime_performance_prometheus(
    _user: DispatcherDep,
):
    return PlainTextResponse(
        analytics_service.get_runtime_performance_metrics_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
