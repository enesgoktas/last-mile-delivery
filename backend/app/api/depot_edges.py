import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_write_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.depot_edge import DepotEdgeCreate, DepotEdgeResponse, DepotEdgeUpdate
from app.services import depot_edge_service
from app.utils.auth import require_role

router = APIRouter()

DispatcherDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))]
AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN))]


@router.post("", response_model=DepotEdgeResponse, status_code=201)
async def create_depot_edge(
    data: DepotEdgeCreate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    return await depot_edge_service.create_depot_edge(db, data, current_user)


@router.get("", response_model=list[DepotEdgeResponse])
async def list_depot_edges(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
):
    return await depot_edge_service.list_depot_edges(db, current_user)


@router.put("/{edge_id}", response_model=DepotEdgeResponse)
async def update_depot_edge(
    edge_id: uuid.UUID,
    data: DepotEdgeUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    return await depot_edge_service.update_depot_edge(db, edge_id, data, current_user)


@router.delete("/{edge_id}", status_code=204)
async def delete_depot_edge(
    edge_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    await depot_edge_service.delete_depot_edge(db, edge_id, current_user)
