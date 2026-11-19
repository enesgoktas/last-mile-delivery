from __future__ import annotations

import heapq
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.depot import Depot
from app.models.depot_edge import DepotEdge
from app.models.enums import OrderLegKind, OrderLegStatus
from app.models.order import Order
from app.models.order_leg import OrderLeg
from app.utils.geo import haversine_meters
from app.utils.tenant_context import tenant_filter


@dataclass(frozen=True, slots=True)
class NetworkPlan:
    origin_depot_id: str
    destination_depot_id: str
    edge_ids: list[str]
    earliest_deliverable: datetime


async def _load_active_depots(db: AsyncSession, tenant_id) -> list[Depot]:
    result = await db.execute(
        select(Depot).where(tenant_filter(Depot, tenant_id), Depot.is_active.is_(True))
    )
    return list(result.scalars().all())


def _nearest_depot(depots: list[Depot], lat: float, lon: float) -> Depot | None:
    if not depots:
        return None
    return min(depots, key=lambda depot: haversine_meters(lat, lon, depot.lat, depot.lon))


async def plan_origin_destination(db: AsyncSession, order: Order) -> NetworkPlan | None:
    depots = await _load_active_depots(db, order.tenant_id)
    origin = _nearest_depot(depots, order.pickup_lat, order.pickup_lon)
    destination = _nearest_depot(depots, order.delivery_lat, order.delivery_lon)
    if origin is None or destination is None:
        return None
    if origin.id == destination.id:
        return NetworkPlan(str(origin.id), str(destination.id), [], order.earliest_delivery)

    edge_result = await db.execute(
        select(DepotEdge).where(tenant_filter(DepotEdge, order.tenant_id))
    )
    edges = list(edge_result.scalars().all())
    graph: dict[str, list[tuple[int, DepotEdge]]] = {}
    for edge in edges:
        graph.setdefault(str(edge.from_depot_id), []).append((edge.transit_minutes, edge))

    start = str(origin.id)
    target = str(destination.id)
    counter = 0
    queue: list[tuple[int, int, str, list[DepotEdge]]] = [(0, counter, start, [])]
    seen: set[str] = set()
    while queue:
        cost, _, node, path = heapq.heappop(queue)
        if node == target:
            return NetworkPlan(
                str(origin.id),
                str(destination.id),
                [str(edge.id) for edge in path],
                order.earliest_delivery + timedelta(minutes=cost),
            )
        if node in seen or len(path) >= 2:
            continue
        seen.add(node)
        for transit, edge in graph.get(node, []):
            counter += 1
            heapq.heappush(queue, (cost + transit, counter, str(edge.to_depot_id), [*path, edge]))
    return None


async def create_order_legs_for_plan(db: AsyncSession, order: Order, plan: NetworkPlan) -> list[OrderLeg]:
    if not plan.edge_ids:
        return []
    legs = [
        OrderLeg(
            tenant_id=order.tenant_id,
            order_id=order.id,
            leg_kind=OrderLegKind.FIRST_MILE,
            sequence_number=1,
            to_depot_id=uuid.UUID(plan.origin_depot_id),
            status=OrderLegStatus.PENDING,
            planned_pickup_at=order.earliest_pickup,
            planned_delivery_at=order.earliest_delivery,
        ),
        OrderLeg(
            tenant_id=order.tenant_id,
            order_id=order.id,
            leg_kind=OrderLegKind.LINEHAUL,
            sequence_number=2,
            from_depot_id=uuid.UUID(plan.origin_depot_id),
            to_depot_id=uuid.UUID(plan.destination_depot_id),
            status=OrderLegStatus.PLANNED,
            planned_pickup_at=order.earliest_delivery,
            planned_delivery_at=plan.earliest_deliverable,
        ),
        OrderLeg(
            tenant_id=order.tenant_id,
            order_id=order.id,
            leg_kind=OrderLegKind.LAST_MILE,
            sequence_number=3,
            from_depot_id=uuid.UUID(plan.destination_depot_id),
            status=OrderLegStatus.PENDING,
            planned_pickup_at=plan.earliest_deliverable,
            planned_delivery_at=order.latest_delivery,
        ),
    ]
    db.add_all(legs)
    await db.flush()
    return legs
