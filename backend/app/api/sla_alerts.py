"""SLA Alerts API — monitor and manage time-window violation alerts."""
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_write_db
from app.models.enums import SLAAlertSeverity, UserRole
from app.models.user import User
from app.services import sla_monitor_service
from app.utils.auth import require_role

router = APIRouter(prefix="/sla-alerts", tags=["SLA Alerts"])


class SLAAlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    route_id: str
    stop_id: str | None
    order_id: str | None
    courier_id: str
    severity: str
    status: str
    alert_type: str
    message: str
    estimated_delay_seconds: int
    created_at: str
    updated_at: str
    acknowledged_at: str | None

class ScanResponse(BaseModel):
    routes_scanned: int
    alerts_created: int
    alerts_resolved: int
    reopt_triggered: int


def _alert_to_response(a) -> SLAAlertResponse:
    return SLAAlertResponse(
        id=str(a.id),
        route_id=str(a.route_id),
        stop_id=str(a.stop_id) if a.stop_id else None,
        order_id=str(a.order_id) if a.order_id else None,
        courier_id=str(a.courier_id),
        severity=a.severity.value if hasattr(a.severity, "value") else a.severity,
        status=a.status.value if hasattr(a.status, "value") else a.status,
        alert_type=a.alert_type,
        message=a.message,
        estimated_delay_seconds=a.estimated_delay_seconds,
        created_at=a.created_at.isoformat(),
        updated_at=a.updated_at.isoformat(),
        acknowledged_at=a.acknowledged_at.isoformat() if a.acknowledged_at else None,
    )


@router.get("", response_model=list[SLAAlertResponse])
async def list_alerts(
    current_user: Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))],
    db: AsyncSession = Depends(get_db),
    severity: SLAAlertSeverity | None = Query(None),
    limit: int = Query(100, le=500),
    cursor: datetime | None = Query(None, description="created_at of last item for pagination"),
):
    alerts = await sla_monitor_service.list_active_alerts(
        db, current_user, severity=severity, limit=limit, cursor=cursor,
    )
    return [_alert_to_response(a) for a in alerts]


@router.post("/{alert_id}/acknowledge", response_model=SLAAlertResponse)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))],
    db: AsyncSession = Depends(get_write_db),
):
    a = await sla_monitor_service.acknowledge_alert(db, alert_id, current_user)
    return _alert_to_response(a)


@router.post("/scan", response_model=ScanResponse)
async def trigger_scan(
    current_user: Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))],
    db: AsyncSession = Depends(get_write_db),
):
    """Manually trigger an SLA violation scan."""
    result = await sla_monitor_service.scan_active_routes(db, current_user)
    return ScanResponse(**result)
