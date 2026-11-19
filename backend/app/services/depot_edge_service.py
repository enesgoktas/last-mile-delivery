import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.depot_edge import DepotEdge
from app.schemas.depot_edge import DepotEdgeCreate, DepotEdgeUpdate
from app.utils.compat import utcnow_naive
from app.utils.tenant_context import get_current_tenant_id, tenant_filter


async def create_depot_edge(db: AsyncSession, data: DepotEdgeCreate, principal) -> DepotEdge:
    edge = DepotEdge(tenant_id=get_current_tenant_id(principal), **data.model_dump())
    db.add(edge)
    await db.flush()
    await db.refresh(edge)
    return edge


async def list_depot_edges(db: AsyncSession, principal) -> list[DepotEdge]:
    result = await db.execute(
        select(DepotEdge)
        .where(tenant_filter(DepotEdge, get_current_tenant_id(principal)))
        .order_by(DepotEdge.from_depot_id, DepotEdge.to_depot_id)
    )
    return list(result.scalars().all())


async def get_depot_edge(db: AsyncSession, edge_id: uuid.UUID, principal) -> DepotEdge:
    result = await db.execute(
        select(DepotEdge).where(
            DepotEdge.id == edge_id,
            tenant_filter(DepotEdge, get_current_tenant_id(principal)),
        )
    )
    edge = result.scalar_one_or_none()
    if edge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Depot edge not found")
    return edge


async def update_depot_edge(
    db: AsyncSession,
    edge_id: uuid.UUID,
    data: DepotEdgeUpdate,
    principal,
) -> DepotEdge:
    edge = await get_depot_edge(db, edge_id, principal)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(edge, key, value)
    edge.updated_at = utcnow_naive()
    await db.flush()
    await db.refresh(edge)
    return edge


async def delete_depot_edge(db: AsyncSession, edge_id: uuid.UUID, principal) -> None:
    edge = await get_depot_edge(db, edge_id, principal)
    await db.delete(edge)
    await db.flush()
