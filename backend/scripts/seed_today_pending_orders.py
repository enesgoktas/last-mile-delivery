"""One-shot: seed PENDING orders for one or more target days in the
default tenant — an unassigned same-day / near-term dispatch backlog.

Orders are created with:
  * status     = PENDING
  * created_at = early morning of the target day
  * pickup / delivery windows = same target day (stays within the
    seconds-from-midnight bounds the VRP solver expects)
  * external_ref = "today_seed_<date>_<NNN>" (idempotent re-run)

Does NOT walk through dispatch — orders stay on the queue for the
admin to see / act on.
"""
from __future__ import annotations

import asyncio
import random
import secrets
import uuid
from datetime import date, datetime, timedelta

import app.models.vehicle_tag  # noqa: F401  — register association table

from sqlalchemy import select

from app.database import async_session
from app.models.depot import Depot
from app.models.enums import OrderPriority, OrderStatus, OrderType
from app.models.order import Order
from app.models.tenant import Tenant
from app.seed_bulk_orders import (
    FIRST_NAMES,
    LAST_NAMES,
    LOCATIONS,
    ORDER_TYPES,
    ORDER_TYPE_WEIGHTS,
    PRIORITIES,
    PRIORITY_WEIGHTS,
    STREET_TEMPLATES,
)


TARGET_DATES = [date(2026, 5, 22), date(2026, 5, 23)]
ORDERS_PER_DAY = 625

RNG_SEED = 20260522
rng = random.Random(RNG_SEED)


def _jitter(lat: float, lon: float, radius_km: float = 2.5) -> tuple[float, float]:
    deg = radius_km / 111.0
    return (lat + rng.uniform(-deg, deg), lon + rng.uniform(-deg, deg))


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


def _today_external_ref(target_date: date, idx: int) -> str:
    return f"today_seed_{target_date.isoformat()}_{idx:03d}"


def _make_today_order(
    tenant_id: uuid.UUID, target_date: date, idx: int, depots: list[Depot]
) -> Order:
    depot = rng.choice(depots)
    order_type = rng.choices(ORDER_TYPES, ORDER_TYPE_WEIGHTS)[0]
    priority = rng.choices(PRIORITIES, PRIORITY_WEIGHTS)[0]

    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)

    reachable = [loc for loc in LOCATIONS if loc[0] != "Silivri, İstanbul"]
    customer_loc = rng.choice(reachable)
    customer_lat, customer_lon = _jitter(customer_loc[1], customer_loc[2], 0.8)
    customer_address = _street(customer_loc[0])

    depot_lat, depot_lon = _jitter(depot.lat, depot.lon, 0.2)

    base = datetime(target_date.year, target_date.month, target_date.day)
    # All four windows MUST stay on the same calendar day — the VRP solver
    # compares them as seconds-from-midnight, so any datetime that rolls
    # past 24:00 looks "earlier than" the same-day earliest and trips the
    # "delivery window is invalid" guard in vrp_solver.py.
    earliest_pickup = base + timedelta(hours=rng.randint(6, 9), minutes=rng.choice([0, 15, 30, 45]))
    latest_pickup = earliest_pickup + timedelta(hours=rng.randint(2, 4))
    earliest_delivery = base + timedelta(hours=rng.randint(10, 14), minutes=rng.choice([0, 15, 30, 45]))
    latest_delivery = earliest_delivery + timedelta(hours=rng.randint(3, 6))
    # Created earlier in the day so it shows up as "today's backlog".
    created_at = base + timedelta(hours=rng.randint(4, 6), minutes=rng.randint(0, 59))

    if order_type == OrderType.REVERSE:
        pickup_address = customer_address
        pickup_lat = round(customer_lat, 6)
        pickup_lon = round(customer_lon, 6)
        delivery_address = depot.address
        delivery_lat = round(depot_lat, 6)
        delivery_lon = round(depot_lon, 6)
    else:
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
        external_ref=_today_external_ref(target_date, idx),
        created_at=created_at,
        updated_at=created_at,
        notes=rng.choice([None, None, None, "Kapı kodu: 1234", "Komşuya bırakabilirsiniz"]),
    )


async def main() -> None:
    async with async_session() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == "default"))
        ).scalar_one()
        depots = list(
            (await db.execute(
                select(Depot).where(Depot.tenant_id == tenant.id, Depot.is_active == True)  # noqa: E712
            )).scalars().all()
        )
        if not depots:
            print("no active depots — run seed_bulk_orders first to upsert depots")
            return

        total_created = 0
        total_skipped = 0
        for target_date in TARGET_DATES:
            existing_refs = set(
                (await db.execute(
                    select(Order.external_ref).where(
                        Order.tenant_id == tenant.id,
                        Order.external_ref.like(
                            f"today_seed_{target_date.isoformat()}_%"
                        ),
                    )
                )).scalars().all()
            )

            day_created = 0
            day_skipped = 0
            for i in range(1, ORDERS_PER_DAY + 1):
                ref = _today_external_ref(target_date, i)
                if ref in existing_refs:
                    day_skipped += 1
                    continue
                db.add(_make_today_order(tenant.id, target_date, i, depots))
                day_created += 1
            await db.commit()
            print(
                f"  {target_date}: seeded {day_created} PENDING orders "
                f"({day_skipped} already existed)"
            )
            total_created += day_created
            total_skipped += day_skipped

        print(
            f"done — {total_created} new orders across "
            f"{len(TARGET_DATES)} day(s); target tenant: {tenant.name}"
        )


if __name__ == "__main__":
    asyncio.run(main())
