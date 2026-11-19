"""Seed promotional data for April 20, 2026 screenshot session.

Creates 70 orders for April 20 with a realistic distribution of statuses
(no FAILED orders). Idempotent — re-running skips orders that already exist.

Distribution:
  - 12 PENDING   (awaiting assignment)
  - 20 ASSIGNED  (courier assigned, planned route)
  - 13 IN_TRANSIT (courier en-route, active route)
  - 25 DELIVERED  (completed earlier today)

Usage (inside the api container):
    docker exec -it backend-api-1 python -m app.seed_promo_april20

Or from the repo root with the venv activated:
    cd backend && python -m app.seed_promo_april20
"""
from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, time as dt_time, timedelta

from sqlalchemy import select

from app.database import async_session
from app.models import Courier, Order, Route, RouteStop, User
from app.models.enums import (
    AvailabilityStatus,
    OrderPriority,
    OrderStatus,
    OrderType,
    RouteStatus,
    StopStatus,
    StopType,
    UserRole,
    VehicleType,
)
from app.schemas.order import OrderCreate
from app.seed_helpers import ensure_default_tenant
from app.services import order_service
from app.utils.auth import hash_password

RNG = random.Random(20042026)

# ---------------------------------------------------------------------------
# Istanbul neighbourhood pool — pickup and delivery points
# ---------------------------------------------------------------------------
LOCATIONS: list[tuple[str, float, float]] = [
    ("Beşiktaş, İstanbul", 41.0426, 29.0064),
    ("Şişli, İstanbul", 41.0607, 28.9877),
    ("Kadıköy, İstanbul", 40.9906, 29.0231),
    ("Üsküdar, İstanbul", 41.0228, 29.0154),
    ("Bakırköy, İstanbul", 40.9816, 28.8731),
    ("Fatih, İstanbul", 41.0186, 28.9394),
    ("Beyoğlu, İstanbul", 41.0369, 28.9844),
    ("Sarıyer, İstanbul", 41.1665, 29.0527),
    ("Maltepe, İstanbul", 40.9350, 29.1327),
    ("Ataşehir, İstanbul", 40.9952, 29.1213),
    ("Pendik, İstanbul", 40.8762, 29.2316),
    ("Kartal, İstanbul", 40.9141, 29.1892),
    ("Bağcılar, İstanbul", 41.0397, 28.8560),
    ("Esenler, İstanbul", 41.0437, 28.8762),
    ("Gaziosmanpaşa, İstanbul", 41.0696, 28.9130),
    ("Kağıthane, İstanbul", 41.0787, 28.9718),
    ("Levent, İstanbul", 41.0840, 29.0094),
    ("Maslak, İstanbul", 41.1096, 29.0257),
    ("Ümraniye, İstanbul", 41.0190, 29.1169),
    ("Bahçelievler, İstanbul", 41.0044, 28.8600),
    ("Küçükçekmece, İstanbul", 41.0014, 28.7764),
    ("Zeytinburnu, İstanbul", 41.0002, 28.9026),
    ("Bostancı, İstanbul", 40.9623, 29.0856),
    ("Taksim, İstanbul", 41.0370, 28.9850),
    ("Sultanahmet, İstanbul", 41.0054, 28.9768),
    ("Mecidiyeköy, İstanbul", 41.0667, 28.9959),
    ("Etiler, İstanbul", 41.0778, 29.0263),
    ("Bebek, İstanbul", 41.0779, 29.0425),
    ("Nişantaşı, İstanbul", 41.0495, 28.9970),
    ("Fenerbahçe, İstanbul", 40.9699, 29.0397),
]

CUSTOMER_NAMES = [
    "Ahmet Yılmaz", "Fatma Kaya", "Mehmet Demir", "Ayşe Çelik", "Mustafa Şahin",
    "Zeynep Yıldız", "Hüseyin Arslan", "Elif Doğan", "Ali Aydın", "Hülya Özkan",
    "İbrahim Çetin", "Merve Koç", "Osman Kurt", "Selin Avcı", "Yusuf Erdoğan",
    "Büşra Güler", "Emre Polat", "Aslı Kılıç", "Serkan Özdemir", "Gizem Yıldırım",
    "Kemal Aslan", "Neslihan Güneş", "Cem Aktaş", "Derya Şimşek", "Tolga Çakır",
    "Burcu Kara", "Murat Öztürk", "Pınar Duman", "Berk Güven", "Yasemin Bulut",
    "Onur Tekin", "Cansu Aksoy", "Deniz Kaplan", "Ece Kocaman", "Hakan Albayrak",
    "Seda Özer", "Barış Uysal", "Tuğba Işık", "Volkan Önal", "Ceren Karadağ",
    "Tayfun Keskin", "Dilek Akar", "Uğur Topal", "Reyhan Bozkurt", "Serhat Yalçın",
    "Nuray Erdem", "Alper Başaran", "Sevgi Tuna", "Furkan Kılınç", "Özge Saraç",
    "Kadir Yaman", "Dilara Çavuş", "Süleyman Doğru", "Sibel Altın", "Gökhan Ateş",
    "Hatice Özen", "Orhan Güngör", "Ebru Coşkun", "Arif Bolat", "Leyla Demirkol",
    "Tuncay Aksu", "Emine Soylu", "Selim Ercan", "Funda Karacan", "Ömer Kayabaşı",
    "Şebnem Yıldız", "Erdal Bektaş", "Nurgül Çiçek", "İsmail Arıkan", "Pervin Tok",
]

PRIORITIES = [
    OrderPriority.LOW,
    OrderPriority.NORMAL,
    OrderPriority.NORMAL,
    OrderPriority.HIGH,
    OrderPriority.URGENT,
]

ORDER_TYPES = [
    OrderType.SCHEDULED,
    OrderType.SCHEDULED,
    OrderType.ON_DEMAND,
    OrderType.BULK,
]

# ---------------------------------------------------------------------------
# Courier definitions — 5 couriers to handle 70 orders
# ---------------------------------------------------------------------------
COURIER_DEFINITIONS = [
    {
        "email": "courier.moto@delivery.com",
        "name": "courier_moto",
        "vehicle_type": VehicleType.MOTORCYCLE,
        "max_weight_kg": 8.0,
        "status": AvailabilityStatus.ONLINE,
        "depot_lat": 41.0082,
        "depot_lon": 28.9784,
    },
    {
        "email": "courier.van@delivery.com",
        "name": "courier_van",
        "vehicle_type": VehicleType.CAR_VAN,
        "max_weight_kg": 30.0,
        "status": AvailabilityStatus.ONLINE,
        "depot_lat": 41.0369,
        "depot_lon": 28.9850,
    },
    {
        "email": "courier.truck@delivery.com",
        "name": "courier_truck",
        "vehicle_type": VehicleType.TRUCK,
        "max_weight_kg": 200.0,
        "status": AvailabilityStatus.ONLINE,
        "depot_lat": 41.0600,
        "depot_lon": 28.9850,
    },
    {
        "email": "courier.moto2@delivery.com",
        "name": "courier_moto2",
        "vehicle_type": VehicleType.MOTORCYCLE,
        "max_weight_kg": 8.0,
        "status": AvailabilityStatus.ONLINE,
        "depot_lat": 41.0510,
        "depot_lon": 29.0200,
    },
    {
        "email": "courier.van2@delivery.com",
        "name": "courier_van2",
        "vehicle_type": VehicleType.CAR_VAN,
        "max_weight_kg": 25.0,
        "status": AvailabilityStatus.ON_BREAK,
        "depot_lat": 40.9950,
        "depot_lon": 29.1100,
    },
]


async def _ensure_user(db, *, tenant_id, email: str, role: UserRole) -> User:
    result = await db.execute(
        select(User).where(User.email == email, User.tenant_id == tenant_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            tenant_id=tenant_id,
            email=email,
            hashed_password=hash_password("courier123"),
            role=role,
        )
        db.add(user)
        await db.flush()
    return user


async def _ensure_courier(db, *, tenant_id, user_id, defn: dict) -> Courier:
    result = await db.execute(
        select(Courier).where(Courier.user_id == user_id, Courier.tenant_id == tenant_id)
    )
    courier = result.scalar_one_or_none()
    if courier is None:
        courier = Courier(
            tenant_id=tenant_id,
            user_id=user_id,
            vehicle_type=defn["vehicle_type"],
            max_weight_kg=defn["max_weight_kg"],
            availability_status=defn["status"],
            depot_lat=defn["depot_lat"],
            depot_lon=defn["depot_lon"],
            working_hours_start=dt_time(hour=7, minute=30),
            working_hours_end=dt_time(hour=21, minute=0),
        )
        db.add(courier)
        await db.flush()
    else:
        courier.availability_status = defn["status"]
    return courier


def _pick_locations() -> tuple[tuple, tuple]:
    pickup = RNG.choice(LOCATIONS)
    delivery = RNG.choice([loc for loc in LOCATIONS if loc != pickup])
    return pickup, delivery


def _weight_for_vehicle(vehicle_type: VehicleType) -> float:
    if vehicle_type == VehicleType.MOTORCYCLE:
        return round(RNG.uniform(0.5, 7.5), 1)
    if vehicle_type == VehicleType.CAR_VAN:
        return round(RNG.uniform(2.0, 28.0), 1)
    return round(RNG.uniform(10.0, 180.0), 1)


async def _create_order_if_missing(
    db,
    *,
    ref: str,
    tenant_id,
    actor_user_id,
    now: datetime,
    vehicle_type: VehicleType,
    window_start: datetime,
    window_end: datetime,
    idx: int,
) -> Order | None:
    result = await db.execute(
        select(Order).where(Order.external_ref == ref, Order.tenant_id == tenant_id)
    )
    if result.scalar_one_or_none() is not None:
        return None

    pickup_loc, delivery_loc = _pick_locations()
    customer_name = CUSTOMER_NAMES[idx % len(CUSTOMER_NAMES)]
    slug = customer_name.lower().replace(" ", ".").replace("ş", "s").replace("ı", "i")
    slug = slug.replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c")

    order = await order_service.create_order(
        db,
        OrderCreate(
            type=RNG.choice(ORDER_TYPES),
            priority=RNG.choice(PRIORITIES),
            pickup_address=pickup_loc[0],
            pickup_lat=pickup_loc[1],
            pickup_lon=pickup_loc[2],
            delivery_address=delivery_loc[0],
            delivery_lat=delivery_loc[1],
            delivery_lon=delivery_loc[2],
            weight_kg=_weight_for_vehicle(vehicle_type),
            earliest_delivery=window_start,
            latest_delivery=window_end,
            customer_email=f"{slug}@customer.local",
            customer_phone=f"+9053000{idx:05d}",
            external_ref=ref,
            notes="promo-apr20 seed",
        ),
        tenant_id=tenant_id,
        initial_status=OrderStatus.PENDING,
        creation_reason="Promo April 20 seed",
        actor_user_id=actor_user_id,
    )
    return order


async def _advance(db, order: Order, path: list[OrderStatus], actor_user_id) -> None:
    if order.status not in path:
        return
    idx = path.index(order.status)
    for target in path[idx + 1:]:
        await order_service.apply_order_status_transition(
            db, order, target,
            actor_user_id=actor_user_id,
            reason=f"promo-apr20 seed: {target.value}",
        )


async def _add_route(
    db,
    *,
    order: Order,
    courier: Courier,
    route_status: RouteStatus,
    pickup_stop_status: StopStatus,
    delivery_stop_status: StopStatus,
    now: datetime,
) -> None:
    if order.route_id:
        return

    if route_status == RouteStatus.PLANNED:
        started_at = None
        completed_at = None
        pickup_planned = now + timedelta(minutes=RNG.randint(15, 90))
        delivery_planned = pickup_planned + timedelta(minutes=RNG.randint(20, 60))
        pickup_actual = None
        delivery_actual = None
    elif route_status == RouteStatus.ACTIVE:
        started_at = now - timedelta(minutes=RNG.randint(30, 90))
        completed_at = None
        pickup_planned = started_at + timedelta(minutes=15)
        delivery_planned = now + timedelta(minutes=RNG.randint(10, 40))
        pickup_actual = pickup_planned + timedelta(minutes=RNG.randint(-5, 10))
        delivery_actual = None
    else:  # COMPLETED
        started_at = now - timedelta(hours=RNG.randint(3, 7))
        completed_at = now - timedelta(minutes=RNG.randint(10, 120))
        pickup_planned = started_at + timedelta(minutes=15)
        delivery_planned = started_at + timedelta(minutes=45)
        pickup_actual = pickup_planned + timedelta(minutes=RNG.randint(-3, 8))
        delivery_actual = delivery_planned + timedelta(minutes=RNG.randint(-5, 15))

    route = Route(
        tenant_id=order.tenant_id,
        courier_id=courier.id,
        status=route_status,
        planned_distance_km=round(RNG.uniform(4.0, 18.0), 1),
        started_at=started_at,
        completed_at=completed_at,
    )
    db.add(route)
    await db.flush()
    order.route_id = route.id
    await db.flush()

    for seq, (stop_type, stype, planned, actual) in enumerate(
        [
            (StopType.PICKUP, pickup_stop_status, pickup_planned, pickup_actual),
            (StopType.DELIVERY, delivery_stop_status, delivery_planned, delivery_actual),
        ],
        start=1,
    ):
        lat = order.pickup_lat if stop_type == StopType.PICKUP else order.delivery_lat
        lon = order.pickup_lon if stop_type == StopType.PICKUP else order.delivery_lon
        address = order.pickup_address if stop_type == StopType.PICKUP else order.delivery_address
        db.add(
            RouteStop(
                tenant_id=order.tenant_id,
                route_id=route.id,
                order_id=order.id,
                sequence_number=seq,
                stop_type=stop_type,
                lat=lat,
                lon=lon,
                address=address,
                planned_arrival=planned,
                actual_arrival=actual,
                status=stype,
            )
        )
    await db.flush()


# ---------------------------------------------------------------------------
# Order groups
# ---------------------------------------------------------------------------
def _build_order_specs(now: datetime, couriers: list[Courier]) -> list[dict]:
    """Return a list of order spec dicts describing each of the 70 orders."""
    specs: list[dict] = []
    courier_cycle = list(couriers)

    def _add(
        n: int,
        prefix: str,
        path: list[OrderStatus],
        route_status: RouteStatus | None,
        pickup_stop: StopStatus | None,
        delivery_stop: StopStatus | None,
        window_offset_hours: tuple[float, float],
        courier: Courier | None = None,
    ) -> None:
        start_idx = len(specs)
        for i in range(n):
            idx = start_idx + i
            ref = f"PROMO-APR20-{prefix}-{i + 1:03d}"
            hours_min, hours_max = window_offset_hours
            window_start = now + timedelta(hours=hours_min + RNG.uniform(-0.5, 0.5))
            window_end = window_start + timedelta(hours=RNG.uniform(1.5, 3.0))
            assigned_courier = courier or courier_cycle[idx % len(courier_cycle)]
            specs.append(
                {
                    "ref": ref,
                    "idx": idx,
                    "path": path,
                    "route_status": route_status,
                    "pickup_stop": pickup_stop,
                    "delivery_stop": delivery_stop,
                    "window_start": window_start,
                    "window_end": window_end,
                    "courier": assigned_courier if path != [OrderStatus.PENDING] else None,
                    "vehicle_type": assigned_courier.vehicle_type,
                }
            )

    # 12 PENDING — no courier, future delivery window
    _add(
        12, "PND",
        path=[OrderStatus.PENDING],
        route_status=None, pickup_stop=None, delivery_stop=None,
        window_offset_hours=(2.0, 7.0),
        courier=couriers[0],
    )

    # 20 ASSIGNED — courier locked in, route planned
    _add(
        20, "ASG",
        path=[OrderStatus.PENDING, OrderStatus.ASSIGNED],
        route_status=RouteStatus.PLANNED,
        pickup_stop=StopStatus.PENDING,
        delivery_stop=StopStatus.PENDING,
        window_offset_hours=(0.5, 4.0),
    )

    # 13 IN_TRANSIT — courier picked up, actively delivering
    _add(
        13, "INT",
        path=[OrderStatus.PENDING, OrderStatus.ASSIGNED, OrderStatus.PICKED_UP, OrderStatus.IN_TRANSIT],
        route_status=RouteStatus.ACTIVE,
        pickup_stop=StopStatus.COMPLETED,
        delivery_stop=StopStatus.ARRIVED,
        window_offset_hours=(-0.5, 1.5),
    )

    # 25 DELIVERED — completed earlier today
    _add(
        25, "DLV",
        path=[
            OrderStatus.PENDING,
            OrderStatus.ASSIGNED,
            OrderStatus.PICKED_UP,
            OrderStatus.IN_TRANSIT,
            OrderStatus.DELIVERED,
        ],
        route_status=RouteStatus.COMPLETED,
        pickup_stop=StopStatus.COMPLETED,
        delivery_stop=StopStatus.COMPLETED,
        window_offset_hours=(-6.0, -1.0),
    )

    return specs


async def seed_promo_april20() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)

    async with async_session() as db:
        tenant = await ensure_default_tenant(db)

        # Ensure dispatcher user exists
        dispatcher_result = await db.execute(
            select(User).where(
                User.email == "dispatcher@delivery.com",
                User.tenant_id == tenant.id,
            )
        )
        dispatcher = dispatcher_result.scalar_one_or_none()
        if dispatcher is None:
            dispatcher = User(
                tenant_id=tenant.id,
                email="dispatcher@delivery.com",
                hashed_password=hash_password("changeme123"),
                role=UserRole.DISPATCHER,
            )
            db.add(dispatcher)
            await db.flush()

        # Ensure all couriers exist
        couriers: list[Courier] = []
        for defn in COURIER_DEFINITIONS:
            user = await _ensure_user(db, tenant_id=tenant.id, email=defn["email"], role=UserRole.COURIER)
            courier = await _ensure_courier(db, tenant_id=tenant.id, user_id=user.id, defn=defn)
            couriers.append(courier)

        await db.flush()

        specs = _build_order_specs(now, couriers)

        created = 0
        skipped = 0
        for spec in specs:
            order = await _create_order_if_missing(
                db,
                ref=spec["ref"],
                tenant_id=tenant.id,
                actor_user_id=dispatcher.id,
                now=now,
                vehicle_type=spec["vehicle_type"],
                window_start=spec["window_start"],
                window_end=spec["window_end"],
                idx=spec["idx"],
            )
            if order is None:
                skipped += 1
                continue

            created += 1

            # Assign courier before status transitions that need it
            if spec["courier"] is not None:
                order.assigned_courier_id = spec["courier"].id
                await db.flush()

            await _advance(db, order, spec["path"], actor_user_id=dispatcher.id)

            if spec["route_status"] is not None and spec["courier"] is not None:
                await _add_route(
                    db,
                    order=order,
                    courier=spec["courier"],
                    route_status=spec["route_status"],
                    pickup_stop_status=spec["pickup_stop"],
                    delivery_stop_status=spec["delivery_stop"],
                    now=now,
                )

        await db.commit()

    print(f"Promo April 20 seed complete — created {created}, skipped {skipped} (already existed).")
    print()
    print("Order breakdown:")
    print("  PENDING    12")
    print("  ASSIGNED   20")
    print("  IN_TRANSIT 13")
    print("  DELIVERED  25")
    print("  TOTAL      70")


if __name__ == "__main__":
    asyncio.run(seed_promo_april20())
