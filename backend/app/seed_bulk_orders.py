"""Bulk order seed — deterministic unassigned PENDING orders for the next 14 days.

Usage (inside the api container):
    docker exec -it backend-api-1 python -m app.seed_bulk_orders [--force]

Or from the repo root with the venv activated:
    cd backend && python -m app.seed_bulk_orders [--force]

Each order has an external_ref like "seed_2026-05-05_625".
Re-running tops up missing seed orders for each day without deleting data.
Pass --force to wipe ALL existing order data before re-seeding.
"""
from __future__ import annotations

import asyncio
import random
import secrets
import sys
import uuid
from datetime import date, datetime, timedelta
from datetime import time as dt_time

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.models.vehicle_tag  # noqa: F401  # register association tables for ORM relationships
from app.database import async_session
from app.models.depot import Depot
from app.models.enums import OrderPriority, OrderStatus, OrderType
from app.models.optimization_run import OptimizationRun
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.models.sla_alert import SLAAlert
from app.seed_helpers import ensure_default_tenant

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Stress-test volume for each seeded day in the forward planning window.
ORDERS_PER_DAY = 625
DAYS_AHEAD = 14  # two weeks of demo data

RNG_SEED = 42  # keep results reproducible
rng = random.Random(RNG_SEED)

# ---------------------------------------------------------------------------
# Warehouse definitions — upserted on every run
# These are the 4 new depots added alongside the existing 'Main Warehouse'.
# ---------------------------------------------------------------------------
NEW_WAREHOUSES = [
    {
        "name": "Avrupa Yakası — Bayrampaşa Hub",
        "address": "Terazidere Caddesi No:50, Bayrampaşa, İstanbul",
        "lat": 41.0431,
        "lon": 28.9026,
    },
    {
        "name": "Anadolu Yakası — Ataşehir Hub",
        "address": "Barbaros Mahallesi Halk Sokak No:22, Ataşehir, İstanbul",
        "lat": 40.9952,
        "lon": 29.1213,
    },
    {
        "name": "Kuzey — Maslak Hub",
        "address": "Büyükdere Caddesi No:185, Maslak, Sarıyer, İstanbul",
        "lat": 41.1096,
        "lon": 29.0257,
    },
    {
        "name": "Güney — Maltepe Hub",
        "address": "Bağdat Caddesi No:340, Maltepe, İstanbul",
        "lat": 40.9350,
        "lon": 29.1327,
    },
]

# ---------------------------------------------------------------------------
# Istanbul neighbourhood pool
# Each entry: (display_name, lat, lon)
# ---------------------------------------------------------------------------
LOCATIONS: list[tuple[str, float, float]] = [
    # European side
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
    ("Tuzla, İstanbul", 40.8164, 29.2983),
    ("Bağcılar, İstanbul", 41.0397, 28.8560),
    ("Esenler, İstanbul", 41.0437, 28.8762),
    ("Gaziosmanpaşa, İstanbul", 41.0696, 28.9130),
    ("Eyüpsultan, İstanbul", 41.0640, 28.9335),
    ("Kağıthane, İstanbul", 41.0787, 28.9718),
    ("Zeytinburnu, İstanbul", 41.0026, 28.8964),
    ("Güngören, İstanbul", 41.0231, 28.8711),
    ("Bahçelievler, İstanbul", 41.0024, 28.8622),
    ("Avcılar, İstanbul", 40.9794, 28.7217),
    ("Esenyurt, İstanbul", 41.0340, 28.6836),
    ("Küçükçekmece, İstanbul", 41.0015, 28.7778),
    ("Başakşehir, İstanbul", 41.0930, 28.8019),
    ("Sultanbeyli, İstanbul", 40.9614, 29.2588),
    ("Sancaktepe, İstanbul", 41.0050, 29.2319),
    ("Çekmeköy, İstanbul", 41.0353, 29.1825),
    ("Beykoz, İstanbul", 41.1343, 29.1150),
    ("Silivri, İstanbul", 41.0742, 28.2470),
]

STREET_TEMPLATES = [
    "{n}. Sokak No:{h}",
    "Çiçek Sokak No:{h}",
    "Atatürk Caddesi No:{h}",
    "Cumhuriyet Caddesi No:{h}",
    "İstiklal Caddesi No:{h}",
    "Bağdat Caddesi No:{h}",
    "Nişantaşı Sokak No:{h}",
    "Millet Caddesi No:{h}",
    "Halaskargazi Caddesi No:{h}",
    "Barbaros Bulvarı No:{h}",
]

FIRST_NAMES = [
    "Ali", "Ayşe", "Mehmet", "Fatma", "Ahmet", "Zeynep",
    "Mustafa", "Elif", "Hüseyin", "Hatice", "İbrahim", "Merve",
    "Yusuf", "Büşra", "Ömer", "Şeyma", "Hasan", "Esra",
    "Can", "Selin", "Emre", "Deniz", "Ozan", "Gizem",
]
LAST_NAMES = [
    "Yılmaz", "Kaya", "Demir", "Çelik", "Şahin", "Yıldız",
    "Yıldırım", "Öztürk", "Aydın", "Özdemir", "Arslan", "Doğan",
    "Kılıç", "Aslan", "Çetin", "Koç", "Kurt", "Polat",
]

ORDER_TYPES = [
    OrderType.ON_DEMAND,
    OrderType.SCHEDULED,
    OrderType.BULK,
    OrderType.REVERSE,
]
ORDER_TYPE_WEIGHTS = [0.45, 0.35, 0.12, 0.08]

PRIORITIES = [
    OrderPriority.LOW,
    OrderPriority.NORMAL,
    OrderPriority.HIGH,
    OrderPriority.URGENT,
]
PRIORITY_WEIGHTS = [0.10, 0.55, 0.25, 0.10]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _jitter(lat: float, lon: float, radius_km: float = 2.5) -> tuple[float, float]:
    """Randomly offset a coordinate by up to radius_km."""
    deg = radius_km / 111.0
    return (
        lat + rng.uniform(-deg, deg),
        lon + rng.uniform(-deg, deg),
    )


def _street(neighbourhood: str) -> str:
    tmpl = rng.choice(STREET_TEMPLATES)
    n = rng.randint(1, 30)
    h = rng.randint(1, 120)
    return f"{tmpl.format(n=n, h=h)}, {neighbourhood}"


def _customer_email(first: str, last: str) -> str:
    domains = ["gmail.com", "hotmail.com", "yahoo.com", "outlook.com", "icloud.com"]
    suffix = rng.randint(1, 999)
    tr = str.maketrans("şçğıöüŞÇĞİÖÜ", "scgiouSCGIOU")
    f = first.lower().translate(tr)
    la = last.lower().translate(tr)
    return f"{f}.{la}{suffix}@{rng.choice(domains)}"


def _customer_phone() -> str:
    return f"+905{rng.randint(0,4)}{rng.randint(0,9)}{rng.randint(1000000,9999999)}"


def _seed_external_ref(target_date: date, idx: int) -> str:
    return f"seed_{target_date.isoformat()}_{idx:03d}"


def _parse_seed_order_index(external_ref: str | None, target_date: date) -> int | None:
    prefix = f"seed_{target_date.isoformat()}_"
    if not external_ref or not external_ref.startswith(prefix):
        return None

    suffix = external_ref.removeprefix(prefix)
    if not suffix.isdigit():
        return None
    return int(suffix)


def _make_order(
    tenant_id: uuid.UUID,
    target_date: date,
    idx: int,
    depots: list[Depot],
) -> Order:
    """Build one seeded Order record linked to a warehouse depot."""
    depot = rng.choice(depots)
    order_type = rng.choices(ORDER_TYPES, ORDER_TYPE_WEIGHTS)[0]
    priority = rng.choices(PRIORITIES, PRIORITY_WEIGHTS)[0]

    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)

    # Customer location — exclude Silivri for reachability, jitter kept small
    # (0.8 km) to avoid placing coordinates in the sea for coastal neighbourhoods
    reachable = [loc for loc in LOCATIONS if loc[0] != "Silivri, İstanbul"]
    customer_loc = rng.choice(reachable)
    customer_lat, customer_lon = _jitter(customer_loc[1], customer_loc[2], radius_km=0.8)
    customer_address = _street(customer_loc[0])

    # Depot location — small jitter to reflect loading bays
    depot_lat, depot_lon = _jitter(depot.lat, depot.lon, radius_km=0.2)

    # Time windows
    base = datetime(target_date.year, target_date.month, target_date.day)
    earliest_pickup = base + timedelta(hours=rng.randint(6, 9), minutes=rng.choice([0, 15, 30, 45]))
    latest_pickup = earliest_pickup + timedelta(hours=rng.randint(2, 4))
    earliest_delivery = base + timedelta(hours=rng.randint(10, 14), minutes=rng.choice([0, 15, 30, 45]))
    latest_delivery = earliest_delivery + timedelta(hours=rng.randint(3, 6))

    if order_type == OrderType.REVERSE:
        # Customer → warehouse: courier picks up from customer, delivers to depot
        pickup_address = customer_address
        pickup_lat = round(customer_lat, 6)
        pickup_lon = round(customer_lon, 6)
        delivery_address = depot.address
        delivery_lat = round(depot_lat, 6)
        delivery_lon = round(depot_lon, 6)
    else:
        # Warehouse → customer: courier picks up from depot, delivers to customer
        pickup_address = depot.address
        pickup_lat = round(depot_lat, 6)
        pickup_lon = round(depot_lon, 6)
        delivery_address = customer_address
        delivery_lat = round(customer_lat, 6)
        delivery_lon = round(customer_lon, 6)

    return Order(
        tenant_id=tenant_id,
        type=order_type,
        status=OrderStatus.PENDING,
        priority=priority,
        depot_id=depot.id,
        pickup_address=pickup_address,
        pickup_lat=pickup_lat,
        pickup_lon=pickup_lon,
        delivery_address=delivery_address,
        delivery_lat=delivery_lat,
        delivery_lon=delivery_lon,
        weight_kg=round(rng.uniform(0.3, 25.0), 2),
        is_fragile=rng.random() < 0.15,
        is_temp_sensitive=rng.random() < 0.08,
        earliest_pickup=earliest_pickup,
        latest_pickup=latest_pickup,
        earliest_delivery=earliest_delivery,
        latest_delivery=latest_delivery,
        customer_email=_customer_email(first, last),
        customer_phone=_customer_phone() if rng.random() < 0.85 else None,
        tracking_token=secrets.token_urlsafe(24),
        external_ref=_seed_external_ref(target_date, idx),
        assigned_courier_id=None,
        route_id=None,
        notes=rng.choice([None, None, None, "Kapı kodu: 1234", "Komşuya bırakabilirsiniz", "Fragile — handle with care", "Lütfen zili çalın"]),
    )


# ---------------------------------------------------------------------------
# Depot upsert
# ---------------------------------------------------------------------------
async def _ensure_depots(session: AsyncSession, tenant_id: uuid.UUID) -> list[Depot]:
    """Upsert the 4 new Istanbul warehouse depots (idempotent by name+tenant).
    Returns all active depots for this tenant including any pre-existing ones."""
    for spec in NEW_WAREHOUSES:
        existing = (await session.execute(
            select(Depot).where(Depot.name == spec["name"], Depot.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if existing is None:
            session.add(Depot(
                tenant_id=tenant_id,
                name=spec["name"],
                address=spec["address"],
                lat=spec["lat"],
                lon=spec["lon"],
                operating_hours_start=dt_time(6, 0),
                operating_hours_end=dt_time(22, 0),
                is_active=True,
            ))
    await session.flush()
    depots = (await session.execute(
        select(Depot).where(Depot.tenant_id == tenant_id, Depot.is_active == True)  # noqa: E712
    )).scalars().all()
    return list(depots)


# ---------------------------------------------------------------------------
# Wipe
# ---------------------------------------------------------------------------
async def _wipe_all_data(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Delete all order-related data for this tenant in the correct FK order."""
    # 1. SLA alerts (FK → route_stops)
    stop_ids = (await session.execute(
        select(RouteStop.id).join(Route, RouteStop.route_id == Route.id)
        .where(Route.tenant_id == tenant_id)
    )).scalars().all()
    if stop_ids:
        await session.execute(delete(SLAAlert).where(SLAAlert.stop_id.in_(stop_ids)))

    # 2. Route stops + routes
    route_ids = (await session.execute(
        select(Route.id).where(Route.tenant_id == tenant_id)
    )).scalars().all()
    if route_ids:
        await session.execute(delete(RouteStop).where(RouteStop.route_id.in_(route_ids)))
        await session.execute(delete(Route).where(Route.id.in_(route_ids)))

    # 3. Optimization runs
    await session.execute(delete(OptimizationRun).where(OptimizationRun.tenant_id == tenant_id))

    # 4. Orders (cascades: packages, order_legs, status_history, order_history)
    await session.execute(delete(Order).where(Order.tenant_id == tenant_id))

    await session.flush()
    print("Wiped existing routes, optimization runs, and orders.")


async def _existing_seed_order_indices(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    target_date: date,
) -> set[int]:
    prefix = f"seed_{target_date.isoformat()}_"
    result = await session.execute(
        select(Order.external_ref).where(
            Order.tenant_id == tenant_id,
            Order.external_ref.like(f"{prefix}%"),
        )
    )

    indices: set[int] = set()
    for external_ref in result.scalars().all():
        index = _parse_seed_order_index(external_ref, target_date)
        if index is not None:
            indices.add(index)
    return indices


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def seed_bulk_orders(*, force: bool = False) -> None:
    today = date.today()

    async with async_session() as session:
        tenant = await ensure_default_tenant(session)

        existing_order_count = int((await session.execute(
            select(func.count(Order.id)).where(Order.tenant_id == tenant.id)
        )).scalar_one())
        if existing_order_count and force:
            print(f"--force: wiping {existing_order_count} existing orders.")
            await _wipe_all_data(session, tenant.id)
        elif existing_order_count:
            print(
                f"Found {existing_order_count} existing orders for this tenant; "
                "topping up missing seed orders without deleting data."
            )

        # Ensure warehouses exist and collect them
        depots = await _ensure_depots(session, tenant.id)
        print(f"Using {len(depots)} active warehouse(s): {[d.name for d in depots]}")

        # Build new orders day-by-day
        new_orders: list[Order] = []
        days_seeded = 0
        existing_seed_orders_in_window = 0

        for d in range(DAYS_AHEAD):
            target = today + timedelta(days=d)
            existing_indices = await _existing_seed_order_indices(session, tenant.id, target)
            existing_seed_orders_in_window += len(existing_indices)
            for i in range(1, ORDERS_PER_DAY + 1):
                if i in existing_indices:
                    continue
                new_orders.append(_make_order(tenant.id, target, i, depots))
            days_seeded += 1

        session.add_all(new_orders)
        await session.commit()

        print(
            f"Seeded {len(new_orders)} new orders across {days_seeded} days "
            f"({today} → {today + timedelta(days=DAYS_AHEAD - 1)}) "
            f"linked to {len(depots)} warehouse(s); "
            f"{len(new_orders) + existing_seed_orders_in_window} seed orders now present "
            f"in the window ({ORDERS_PER_DAY}/day target)."
        )


if __name__ == "__main__":
    force = "--force" in sys.argv
    asyncio.run(seed_bulk_orders(force=force))
