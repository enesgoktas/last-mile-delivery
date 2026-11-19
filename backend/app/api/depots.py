import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_write_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.depot import DepotCreate, DepotResponse, DepotUpdate
from app.services import depot_service
from app.utils.auth import require_role

router = APIRouter()

AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN))]
DispatcherDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))]


@router.post("", response_model=DepotResponse, status_code=201)
async def create_depot(
    data: DepotCreate,
    current_user: AdminDep,
    db: AsyncSession = Depends(get_write_db),
):
    return await depot_service.create_depot(db, data, current_user)


@router.get("", response_model=list[DepotResponse])
async def list_depots(
    current_user: DispatcherDep,
    response: Response,
    db: AsyncSession = Depends(get_write_db),
    active_only: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    total = await depot_service.count_depots(db, current_user, active_only=active_only)
    response.headers["X-Total-Count"] = str(total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    return await depot_service.list_depots(db, current_user, active_only=active_only, skip=skip, limit=limit)


@router.get("/{depot_id}", response_model=DepotResponse)
async def get_depot(
    depot_id: uuid.UUID,
    current_user: DispatcherDep,
    db: AsyncSession = Depends(get_write_db),
):
    return await depot_service.get_depot(db, depot_id, current_user)


@router.put("/{depot_id}", response_model=DepotResponse)
async def update_depot(
    depot_id: uuid.UUID,
    data: DepotUpdate,
    current_user: AdminDep,
    db: AsyncSession = Depends(get_write_db),
):
    return await depot_service.update_depot(db, depot_id, data, current_user)


@router.delete("/{depot_id}", status_code=204)
async def delete_depot(
    depot_id: uuid.UUID,
    current_user: AdminDep,
    db: AsyncSession = Depends(get_write_db),
):
    await depot_service.delete_depot(db, depot_id, current_user)
