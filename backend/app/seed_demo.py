"""Seed deterministic demo data for local end-to-end UI testing.

Usage:
    python -m app.seed_demo
"""

import asyncio
from datetime import datetime, timedelta
from datetime import time as dt_time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import (
    Courier,
    InventoryEvent,
    LocationSnapshot,
    OptimizationRun,
    Order,
    Package,
    Route,
    RouteStop,
    User,
)
from app.models.enums import (
    AvailabilityStatus,
    FailedOrderCustomerResponseStatus,
    FailureReason,
    FuelType,
    InventoryEventType,
    OptimizationRunStatus,
    OptimizationRunType,
    OrderPriority,
    OrderStatus,
    OrderType,
    PackageStatus,
    RouteStatus,
    StopStatus,
    StopType,
    UserRole,
    VehicleType,
)
from app.models.sla_alert import SLAAlert
from app.models.vehicle_spec import VehicleSpec
from app.models.vehicle_tag import courier_vehicle_tags
from app.schemas.order import OrderCreate
from app.seed_helpers import ensure_default_tenant
from app.services import order_service
from app.utils.auth import hash_password
from app.utils.compat import UTC

DEMO_COURIER_PASSWORD = "courier123"
DEMO_FLEET_SIZE = 25


def run_migrations() -> None:
    """Apply migrations before creating seed records."""
    root = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(alembic_cfg, "head")


async def _ensure_user(
    db: AsyncSession,
    *,
    tenant_id,
    email: str,
    password: str,
    role: UserRole,
) -> User:
    result = await db.execute(
        select(User).where(
            User.email == email,
            User.tenant_id == tenant_id,
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            tenant_id=tenant_id,
            email=email,
            hashed_password=hash_password(password),
            role=role,
        )
        db.add(user)
        await db.flush()
    else:
        user.role = role
    return user


_DEMO_VEHICLE_SPECS: list[dict] = [
    # Motorcycles
    {
        "make": "Honda", "model": "PCX 150", "year": 2022,
        "vehicle_type": VehicleType.MOTORCYCLE, "fuel_type": FuelType.GASOLINE,
        "fuel_efficiency_km_per_liter": 40.0, "co2_emissions_g_per_km": 56.0,
    },
    {
        "make": "Yamaha", "model": "NMAX 155", "year": 2023,
        "vehicle_type": VehicleType.MOTORCYCLE, "fuel_type": FuelType.GASOLINE,
        "fuel_efficiency_km_per_liter": 38.5, "co2_emissions_g_per_km": 59.0,
    },
    {
        "make": "Honda", "model": "CB125F", "year": 2023,
        "vehicle_type": VehicleType.MOTORCYCLE, "fuel_type": FuelType.GASOLINE,
        "fuel_efficiency_km_per_liter": 42.0, "co2_emissions_g_per_km": 54.0,
    },
    # Cars / Vans
    {
        "make": "Toyota", "model": "Corolla", "year": 2023,
        "vehicle_type": VehicleType.CAR_VAN, "fuel_type": FuelType.HYBRID,
        "fuel_efficiency_km_per_liter": 22.0, "co2_emissions_g_per_km": 96.0,
    },
    {
        "make": "Ford", "model": "Transit Custom", "year": 2023,
        "vehicle_type": VehicleType.CAR_VAN, "fuel_type": FuelType.DIESEL,
        "fuel_efficiency_km_per_liter": 13.5, "co2_emissions_g_per_km": 162.0,
    },
    {
        "make": "Mercedes-Benz", "model": "Sprinter 2500", "year": 2022,
        "vehicle_type": VehicleType.CAR_VAN, "fuel_type": FuelType.DIESEL,
        "fuel_efficiency_km_per_liter": 11.5, "co2_emissions_g_per_km": 195.0,
    },
    {
        "make": "Volkswagen", "model": "Caddy Cargo", "year": 2023,
        "vehicle_type": VehicleType.CAR_VAN, "fuel_type": FuelType.DIESEL,
        "fuel_efficiency_km_per_liter": 15.0, "co2_emissions_g_per_km": 148.0,
    },
    {
        "make": "Renault", "model": "Kangoo ZE", "year": 2023,
        "vehicle_type": VehicleType.CAR_VAN, "fuel_type": FuelType.ELECTRIC,
        "fuel_efficiency_km_per_liter": 6.5, "co2_emissions_g_per_km": 0.0,
    },
    # Trucks
    {
        "make": "Isuzu", "model": "NPR 75L", "year": 2022,
        "vehicle_type": VehicleType.TRUCK, "fuel_type": FuelType.DIESEL,
        "fuel_efficiency_km_per_liter": 7.0, "co2_emissions_g_per_km": 320.0,
    },
    {
        "make": "Mercedes-Benz", "model": "Atego 1218", "year": 2022,
        "vehicle_type": VehicleType.TRUCK, "fuel_type": FuelType.DIESEL,
        "fuel_efficiency_km_per_liter": 6.0, "co2_emissions_g_per_km": 370.0,
    },
    {
        "make": "Ford", "model": "F-650", "year": 2022,
        "vehicle_type": VehicleType.TRUCK, "fuel_type": FuelType.DIESEL,
        "fuel_efficiency_km_per_liter": 5.5, "co2_emissions_g_per_km": 405.0,
    },
]


_BASE_DEMO_COURIER_DEFINITIONS: list[dict] = [
    {
        "slot": "moto",
        "email": "courier.moto@delivery.com",
        "vehicle_type": VehicleType.MOTORCYCLE,
        "max_weight_kg": 8.0,
        "availability_status": AvailabilityStatus.ONLINE,
        "depot_lat": 41.0082,
        "depot_lon": 28.9784,
        "vehicle_spec": ("Honda", "PCX 150", 2022),
    },
    {
        "slot": "van",
        "email": "courier.van@delivery.com",
        "vehicle_type": VehicleType.CAR_VAN,
        "max_weight_kg": 30.0,
        "availability_status": AvailabilityStatus.ONLINE,
        "depot_lat": 41.0369,
        "depot_lon": 28.9850,
        "vehicle_spec": ("Ford", "Transit Custom", 2023),
    },
    {
        "slot": "truck",
        "email": "courier.truck@delivery.com",
        "vehicle_type": VehicleType.TRUCK,
        "max_weight_kg": 200.0,
        "availability_status": AvailabilityStatus.ON_BREAK,
        "depot_lat": 41.0600,
        "depot_lon": 28.9850,
        "vehicle_spec": ("Isuzu", "NPR 75L", 2022),
    },
]


_SUPPLEMENTAL_COURIER_PROFILES: list[dict] = [
    {
        "vehicle_type": VehicleType.MOTORCYCLE,
        "max_weight_kg": 8.0,
        "vehicle_spec": ("Yamaha", "NMAX 155", 2023),
    },
    {
        "vehicle_type": VehicleType.MOTORCYCLE,
        "max_weight_kg": 7.0,
        "vehicle_spec": ("Honda", "CB125F", 2023),
    },
    {
        "vehicle_type": VehicleType.CAR_VAN,
        "max_weight_kg": 35.0,
        "vehicle_spec": ("Toyota", "Corolla", 2023),
    },
    {
        "vehicle_type": VehicleType.CAR_VAN,
        "max_weight_kg": 55.0,
        "vehicle_spec": ("Volkswagen", "Caddy Cargo", 2023),
    },
    {
        "vehicle_type": VehicleType.CAR_VAN,
        "max_weight_kg": 65.0,
        "vehicle_spec": ("Renault", "Kangoo ZE", 2023),
    },
    {
        "vehicle_type": VehicleType.CAR_VAN,
        "max_weight_kg": 90.0,
        "vehicle_spec": ("Mercedes-Benz", "Sprinter 2500", 2022),
    },
    {
        "vehicle_type": VehicleType.TRUCK,
        "max_weight_kg": 220.0,
        "vehicle_spec": ("Mercedes-Benz", "Atego 1218", 2022),
    },
    {
        "vehicle_type": VehicleType.TRUCK,
        "max_weight_kg": 260.0,
        "vehicle_spec": ("Ford", "F-650", 2022),
    },
]


_FLEET_DEPOT_COORDINATES: list[tuple[float, float]] = [
    (41.0431, 28.9026),
    (40.9952, 29.1213),
    (41.1096, 29.0257),
    (40.9350, 29.1327),
    (41.0082, 28.9784),
]


def _build_demo_courier_definitions() -> list[dict]:
    definitions = [dict(item) for item in _BASE_DEMO_COURIER_DEFINITIONS]

    for courier_number in range(len(definitions) + 1, DEMO_FLEET_SIZE + 1):
        profile = _SUPPLEMENTAL_COURIER_PROFILES[
            (courier_number - len(_BASE_DEMO_COURIER_DEFINITIONS) - 1)
            % len(_SUPPLEMENTAL_COURIER_PROFILES)
        ]
        depot_lat, depot_lon = _FLEET_DEPOT_COORDINATES[
            (courier_number - len(_BASE_DEMO_COURIER_DEFINITIONS) - 1)
            % len(_FLEET_DEPOT_COORDINATES)
        ]
        coordinate_offset = ((courier_number % 5) - 2) * 0.003
        status = (
            AvailabilityStatus.ON_BREAK
            if courier_number in {10, 18}
            else AvailabilityStatus.ONLINE
        )
        definitions.append(
            {
                **profile,
                "slot": f"fleet_{courier_number:02d}",
                "email": f"courier.fleet{courier_number:02d}@delivery.com",
                "availability_status": status,
                "depot_lat": round(depot_lat + coordinate_offset, 6),
                "depot_lon": round(depot_lon - coordinate_offset, 6),
            }
        )

    return definitions


DEMO_COURIER_DEFINITIONS = _build_demo_courier_definitions()
DEMO_COURIER_EMAILS = frozenset(
    definition["email"] for definition in DEMO_COURIER_DEFINITIONS
)


async def _ensure_vehicle_specs(db: AsyncSession, *, tenant_id=None) -> dict[tuple, VehicleSpec]:
    """Seed demo vehicle specs as global entries (tenant_id=None)."""
    spec_map: dict[tuple, VehicleSpec] = {}
    for spec_data in _DEMO_VEHICLE_SPECS:
        key = (spec_data["make"], spec_data["model"], spec_data["year"])
        result = await db.execute(
            select(VehicleSpec).where(
                VehicleSpec.make == spec_data["make"],
                VehicleSpec.model == spec_data["model"],
                VehicleSpec.year == spec_data["year"],
                VehicleSpec.tenant_id.is_(None),
            )
        )
        spec = result.scalar_one_or_none()
        if spec is None:
            spec = VehicleSpec(
                tenant_id=None,
                make=spec_data["make"],
                model=spec_data["model"],
                year=spec_data["year"],
                vehicle_type=spec_data["vehicle_type"],
                fuel_type=spec_data["fuel_type"],
                fuel_efficiency_km_per_liter=spec_data["fuel_efficiency_km_per_liter"],
                co2_emissions_g_per_km=spec_data.get("co2_emissions_g_per_km"),
                source="MANUAL",
            )
            db.add(spec)
            await db.flush()
        spec_map[key] = spec
    return spec_map


async def _ensure_courier(
    db: AsyncSession,
    *,
    tenant_id,
    user_id,
    vehicle_type: VehicleType,
    max_weight_kg: float,
    availability_status: AvailabilityStatus,
    depot_lat: float,
    depot_lon: float,
    vehicle_spec_id=None,
) -> Courier:
    result = await db.execute(
        select(Courier).where(
            Courier.user_id == user_id,
            Courier.tenant_id == tenant_id,
        )
    )
    courier = result.scalar_one_or_none()
    if courier is None:
        courier = Courier(
            tenant_id=tenant_id,
            user_id=user_id,
            vehicle_type=vehicle_type,
            max_weight_kg=max_weight_kg,
            availability_status=availability_status,
            depot_lat=depot_lat,
            depot_lon=depot_lon,
            working_hours_start=dt_time(hour=8, minute=0),
            working_hours_end=dt_time(hour=20, minute=0),
            zone_polygon=None,
            vehicle_spec_id=vehicle_spec_id,
        )
        db.add(courier)
        await db.flush()
    else:
        courier.vehicle_type = vehicle_type
        courier.max_weight_kg = max_weight_kg
        courier.availability_status = availability_status
        courier.depot_lat = depot_lat
        courier.depot_lon = depot_lon
        if vehicle_spec_id is not None:
            courier.vehicle_spec_id = vehicle_spec_id
    return courier


def _demo_order_payloads(now: datetime) -> list[dict]:
    return [
        {
            "external_ref": "DEMO-PENDING-001",
            "type": OrderType.SCHEDULED,
            "priority": OrderPriority.NORMAL,
            "pickup_address": "Levent Hub, Istanbul",
            "pickup_lat": 41.0840,
            "pickup_lon": 29.0094,
            "delivery_address": "Kadikoy Center, Istanbul",
            "delivery_lat": 40.9918,
            "delivery_lon": 29.0274,
            "weight_kg": 4.0,
            "customer_email": "pending.customer@demo.local",
            "customer_phone": "+905300000101",
            "earliest_delivery": now + timedelta(hours=2),
            "latest_delivery": now + timedelta(hours=5),
            "path": [OrderStatus.PENDING],
            "courier_slot": None,
        },
        {
            "external_ref": "DEMO-ASSIGNED-001",
            "type": OrderType.ON_DEMAND,
            "priority": OrderPriority.HIGH,
            "pickup_address": "Maslak Tech Park, Istanbul",
            "pickup_lat": 41.1096,
            "pickup_lon": 29.0257,
            "delivery_address": "Sisli Office Tower, Istanbul",
            "delivery_lat": 41.0611,
            "delivery_lon": 28.9884,
            "weight_kg": 2.5,
            "customer_email": "assigned.customer@demo.local",
            "customer_phone": "+905300000102",
            "earliest_delivery": now + timedelta(hours=1),
            "latest_delivery": now + timedelta(hours=3),
            "path": [OrderStatus.PENDING, OrderStatus.ASSIGNED],
            "courier_slot": "moto",
        },
        {
            "external_ref": "DEMO-ASSIGNED-002",
            "type": OrderType.ON_DEMAND,
            "priority": OrderPriority.NORMAL,
            "pickup_address": "Maslak Tech Park, Istanbul",
            "pickup_lat": 41.1096,
            "pickup_lon": 29.0257,
            "delivery_address": "Besiktas Barbaros Blvd Drop Point, Istanbul",
            "delivery_lat": 41.0448,
            "delivery_lon": 29.0026,
            "weight_kg": 1.8,
            "customer_email": "assigned2.customer@demo.local",
            "customer_phone": "+905300000106",
            "earliest_delivery": now + timedelta(hours=1),
            "latest_delivery": now + timedelta(hours=3),
            "path": [OrderStatus.PENDING, OrderStatus.ASSIGNED],
            "courier_slot": "moto",
        },
        {
            "external_ref": "DEMO-ASSIGNED-003",
            "type": OrderType.ON_DEMAND,
            "priority": OrderPriority.NORMAL,
            "pickup_address": "Maslak Tech Park, Istanbul",
            "pickup_lat": 41.1096,
            "pickup_lon": 29.0257,
            "delivery_address": "Kadikoy Rıhtım Delivery Point, Istanbul",
            "delivery_lat": 40.9903,
            "delivery_lon": 29.0564,
            "weight_kg": 2.1,
            "customer_email": "assigned3.customer@demo.local",
            "customer_phone": "+905300000107",
            "earliest_delivery": now + timedelta(hours=1),
            "latest_delivery": now + timedelta(hours=4),
            "path": [OrderStatus.PENDING, OrderStatus.ASSIGNED],
            "courier_slot": "moto",
        },
        {
            "external_ref": "DEMO-INTRANSIT-001",
            "type": OrderType.BULK,
            "priority": OrderPriority.URGENT,
            "pickup_address": "Bayrampasa Warehouse, Istanbul",
            "pickup_lat": 41.0431,
            "pickup_lon": 28.9026,
            "delivery_address": "Uskudar Medical Center, Istanbul",
            "delivery_lat": 41.0257,
            "delivery_lon": 29.0154,
            "weight_kg": 15.0,
            "customer_email": "intransit.customer@demo.local",
            "customer_phone": "+905300000103",
            "earliest_delivery": now + timedelta(minutes=45),
            "latest_delivery": now + timedelta(hours=4),
            "path": [
                OrderStatus.PENDING,
                OrderStatus.ASSIGNED,
                OrderStatus.PICKED_UP,
                OrderStatus.IN_TRANSIT,
            ],
            "courier_slot": "van",
        },
        {
            "external_ref": "DEMO-FAILED-001",
            "type": OrderType.REVERSE,
            "priority": OrderPriority.HIGH,
            "pickup_address": "Besiktas Customer Site, Istanbul",
            "pickup_lat": 41.0435,
            "pickup_lon": 29.0062,
            "delivery_address": "Kartal Service Center, Istanbul",
            "delivery_lat": 40.8892,
            "delivery_lon": 29.1852,
            "weight_kg": 3.0,
            "customer_email": "failed.customer@demo.local",
            "customer_phone": "+905300000104",
            "earliest_delivery": now - timedelta(hours=2),
            "latest_delivery": now + timedelta(hours=1),
            "path": [
                OrderStatus.PENDING,
                OrderStatus.ASSIGNED,
                OrderStatus.PICKED_UP,
                OrderStatus.IN_TRANSIT,
                OrderStatus.FAILED,
            ],
            "courier_slot": "van",
        },
        {
            "external_ref": "DEMO-DELIVERED-001",
            "type": OrderType.SCHEDULED,
            "priority": OrderPriority.LOW,
            "pickup_address": "Atasehir Depot, Istanbul",
            "pickup_lat": 40.9985,
            "pickup_lon": 29.1231,
            "delivery_address": "Maltepe Residence, Istanbul",
            "delivery_lat": 40.9345,
            "delivery_lon": 29.1483,
            "weight_kg": 6.0,
            "customer_email": "delivered.customer@demo.local",
            "customer_phone": "+905300000105",
            "earliest_delivery": now - timedelta(hours=5),
            "latest_delivery": now - timedelta(hours=1),
            "path": [
                OrderStatus.PENDING,
                OrderStatus.ASSIGNED,
                OrderStatus.PICKED_UP,
                OrderStatus.IN_TRANSIT,
                OrderStatus.DELIVERED,
            ],
            "courier_slot": "truck",
        },
    ]


async def _ensure_order(
    db: AsyncSession,
    *,
    actor_user_id,
    tenant_id,
    payload: dict,
) -> tuple[Order, bool]:
    result = await db.execute(
        select(Order).where(
            Order.external_ref == payload["external_ref"],
            Order.tenant_id == tenant_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        # Re-seeding should restore demo scenarios deterministically even if a
        # previous local run changed the order state (for example FAILED after a
        # stop action). Reset the order back to a clean seed baseline, then let
        # the configured path below replay the intended scenario.
        existing.type = payload["type"]
        existing.priority = payload["priority"]
        existing.pickup_address = payload["pickup_address"]
        existing.pickup_lat = payload["pickup_lat"]
        existing.pickup_lon = payload["pickup_lon"]
        existing.delivery_address = payload["delivery_address"]
        existing.delivery_lat = payload["delivery_lat"]
        existing.delivery_lon = payload["delivery_lon"]
        existing.weight_kg = payload["weight_kg"]
        existing.earliest_delivery = payload["earliest_delivery"]
        existing.latest_delivery = payload["latest_delivery"]
        existing.customer_email = payload["customer_email"]
        existing.customer_phone = payload["customer_phone"]
        existing.notes = "seed_demo fixture"
        existing.status = OrderStatus.PENDING
        existing.assigned_courier_id = None
        existing.route_id = None
        existing.scheduled_for_date = None
        existing.attempt_count = 0
        existing.failed_at = None
        existing.customer_response_status = None
        existing.customer_response_due_at = None
        existing.escalated_at = None
        await db.flush()
        return existing, False

    order = await order_service.create_order(
        db,
        OrderCreate(
            type=payload["type"],
            priority=payload["priority"],
            pickup_address=payload["pickup_address"],
            pickup_lat=payload["pickup_lat"],
            pickup_lon=payload["pickup_lon"],
            delivery_address=payload["delivery_address"],
            delivery_lat=payload["delivery_lat"],
            delivery_lon=payload["delivery_lon"],
            weight_kg=payload["weight_kg"],
            earliest_delivery=payload["earliest_delivery"],
            latest_delivery=payload["latest_delivery"],
            customer_email=payload["customer_email"],
            customer_phone=payload["customer_phone"],
            external_ref=payload["external_ref"],
            notes="seed_demo fixture",
        ),
        tenant_id=tenant_id,
        initial_status=OrderStatus.PENDING,
        creation_reason="Seeded demo order",
        actor_user_id=actor_user_id,
    )
    return order, True


async def _advance_order_along_path(
    db: AsyncSession,
    *,
    order: Order,
    path: list[OrderStatus],
    actor_user_id,
    reason_prefix: str,
) -> None:
    if order.status not in path:
        return
    start_idx = path.index(order.status)
    for target in path[start_idx + 1 :]:
        await order_service.apply_order_status_transition(
            db,
            order,
            target,
            actor_user_id=actor_user_id,
            reason=f"{reason_prefix}: {target.value}",
        )


async def _ensure_order_route(
    db: AsyncSession,
    *,
    order: Order,
    courier: Courier,
    route_status: RouteStatus,
    pickup_status: StopStatus,
    delivery_status: StopStatus,
    delivery_failure_reason: FailureReason | None = None,
    delivery_notes: str | None = None,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    route = await db.get(Route, order.route_id) if order.route_id else None
    if route is None:
        route = Route(
            tenant_id=order.tenant_id,
            courier_id=courier.id,
            status=route_status,
            planned_distance_km=12.0,
        )
        db.add(route)
        await db.flush()
        order.route_id = route.id
    else:
        route.courier_id = courier.id
        route.status = route_status

    if route_status == RouteStatus.PLANNED:
        route.started_at = None
        route.completed_at = None
    elif route_status == RouteStatus.ACTIVE:
        route.started_at = now - timedelta(minutes=40)
        route.completed_at = None
    else:
        route.started_at = now - timedelta(hours=3)
        route.completed_at = now - timedelta(hours=1)

    async def _ensure_stop(
        *,
        stop_type: StopType,
        sequence_number: int,
        status_value: StopStatus,
        planned_arrival: datetime,
        actual_arrival: datetime | None,
        failure_reason: FailureReason | None = None,
        notes: str | None = None,
    ) -> None:
        result = await db.execute(
            select(RouteStop).where(
                RouteStop.route_id == route.id,
                RouteStop.order_id == order.id,
                RouteStop.stop_type == stop_type,
            )
        )
        stop = result.scalar_one_or_none()
        lat = order.pickup_lat if stop_type == StopType.PICKUP else order.delivery_lat
        lon = order.pickup_lon if stop_type == StopType.PICKUP else order.delivery_lon
        address = order.pickup_address if stop_type == StopType.PICKUP else order.delivery_address
        if stop is None:
            stop = RouteStop(
                tenant_id=order.tenant_id,
                route_id=route.id,
                order_id=order.id,
                sequence_number=sequence_number,
                stop_type=stop_type,
                lat=lat,
                lon=lon,
                address=address,
                planned_arrival=planned_arrival,
                actual_arrival=actual_arrival,
                status=status_value,
                failure_reason=failure_reason,
                notes=notes,
            )
            db.add(stop)
        else:
            stop.sequence_number = sequence_number
            stop.lat = lat
            stop.lon = lon
            stop.address = address
            stop.planned_arrival = planned_arrival
            stop.actual_arrival = actual_arrival
            stop.status = status_value
            stop.failure_reason = failure_reason
            stop.notes = notes
        await db.flush()

    pickup_actual = None
    if pickup_status in (StopStatus.ARRIVED, StopStatus.COMPLETED, StopStatus.FAILED):
        pickup_actual = now - timedelta(minutes=30)
    delivery_actual = None
    if delivery_status in (StopStatus.ARRIVED, StopStatus.COMPLETED, StopStatus.FAILED):
        delivery_actual = now - timedelta(minutes=10)

    pickup_planned = now - timedelta(minutes=35)
    delivery_planned = now + timedelta(minutes=20)
    if route_status in (RouteStatus.COMPLETED, RouteStatus.ABORTED):
        pickup_planned = now - timedelta(hours=3)
        delivery_planned = now - timedelta(hours=2)

    await _ensure_stop(
        stop_type=StopType.PICKUP,
        sequence_number=1,
        status_value=pickup_status,
        planned_arrival=pickup_planned,
        actual_arrival=pickup_actual,
    )
    await _ensure_stop(
        stop_type=StopType.DELIVERY,
        sequence_number=2,
        status_value=delivery_status,
        planned_arrival=delivery_planned,
        actual_arrival=delivery_actual,
        failure_reason=delivery_failure_reason,
        notes=delivery_notes,
    )


async def _ensure_recent_location(
    db: AsyncSession,
    *,
    courier: Courier,
    lat: float,
    lon: float,
) -> None:
    result = await db.execute(
        select(LocationSnapshot)
        .where(LocationSnapshot.courier_id == courier.id)
        .order_by(LocationSnapshot.recorded_at.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    if latest and latest.recorded_at >= datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10):
        return
    db.add(
        LocationSnapshot(
            tenant_id=courier.tenant_id,
            courier_id=courier.id,
            lat=lat,
            lon=lon,
            heading=180.0,
            speed_kmh=22.0,
            accuracy_m=8.0,
        )
    )
    await db.flush()


async def _ensure_optimization_runs(db: AsyncSession, tenant_id) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    fixtures = [
        {
            "task_id": "seed-demo-batch-success",
            "run_type": OptimizationRunType.BATCH,
            "status": OptimizationRunStatus.SUCCEEDED,
            "routes_created": 4,
            "unassigned_count": 1,
            "solver_status": "OPTIMAL",
            "metrics": {"solver_runtime_seconds": 4.2, "objective": 18234},
            "started_at": now - timedelta(minutes=25),
            "finished_at": now - timedelta(minutes=24),
        },
        {
            "task_id": "seed-demo-reoptimization-noop",
            "run_type": OptimizationRunType.REOPTIMIZATION,
            "status": OptimizationRunStatus.NO_OP,
            "routes_created": 0,
            "unassigned_count": 0,
            "solver_status": "NO_OP",
            "metrics": {"reason": "guardrail_cooldown_active"},
            "started_at": now - timedelta(minutes=8),
            "finished_at": now - timedelta(minutes=8),
        },
    ]

    for fixture in fixtures:
        result = await db.execute(
            select(OptimizationRun).where(
                OptimizationRun.task_id == fixture["task_id"],
                OptimizationRun.tenant_id == tenant_id,
            )
        )
        run = result.scalar_one_or_none()
        if run is None:
            run = OptimizationRun(tenant_id=tenant_id, **fixture)
            db.add(run)
        else:
            run.run_type = fixture["run_type"]
            run.status = fixture["status"]
            run.routes_created = fixture["routes_created"]
            run.unassigned_count = fixture["unassigned_count"]
            run.solver_status = fixture["solver_status"]
            run.metrics = fixture["metrics"]
            run.started_at = fixture["started_at"]
            run.finished_at = fixture["finished_at"]
        await db.flush()


async def _ensure_loaded_packages_for_order(
    db: AsyncSession,
    *,
    order: Order,
    courier: Courier,
    minimum_package_count: int = 1,
    load_on_vehicle: bool = True,
) -> None:
    package_result = await db.execute(
        select(Package)
        .where(Package.order_id == order.id)
        .order_by(Package.created_at)
    )
    packages = list(package_result.scalars().all())

    normalized_ref = (order.external_ref or "ORDER").replace("DEMO-", "").replace(" ", "-").upper()
    existing_labels = {pkg.label for pkg in packages}
    while len(packages) < minimum_package_count:
        next_idx = len(packages) + 1
        label = f"PKG-{normalized_ref}-{next_idx:02d}"
        while label in existing_labels:
            next_idx += 1
            label = f"PKG-{normalized_ref}-{next_idx:02d}"
        existing_labels.add(label)
        pkg = Package(
            tenant_id=order.tenant_id,
            order_id=order.id,
            label=label,
            weight_kg=max(1.0, round((order.weight_kg or 1.0) / minimum_package_count, 2)),
            status=PackageStatus.LOADED if load_on_vehicle else PackageStatus.AT_DEPOT,
        )
        db.add(pkg)
        await db.flush()
        packages.append(pkg)

    kept_packages = packages[:minimum_package_count]
    extra_packages = packages[minimum_package_count:]

    for idx, pkg in enumerate(kept_packages, start=1):
        expected_label = f"PKG-{normalized_ref}-{idx:02d}"
        pkg.label = expected_label
        pkg.status = PackageStatus.LOADED if load_on_vehicle else PackageStatus.AT_DEPOT
        if load_on_vehicle:
            loaded_event_result = await db.execute(
                select(InventoryEvent).where(
                    InventoryEvent.package_id == pkg.id,
                    InventoryEvent.courier_id == courier.id,
                    InventoryEvent.event_type == InventoryEventType.LOADED_ONTO_VEHICLE,
                )
            )
            loaded_event = loaded_event_result.scalar_one_or_none()
            if loaded_event is None:
                db.add(
                    InventoryEvent(
                        tenant_id=order.tenant_id,
                        package_id=pkg.id,
                        event_type=InventoryEventType.LOADED_ONTO_VEHICLE,
                        courier_id=courier.id,
                        notes="seed_demo loaded package for loading checklist",
                    )
                )
                await db.flush()
        else:
            await db.execute(
                delete(InventoryEvent).where(
                    InventoryEvent.package_id == pkg.id,
                    InventoryEvent.event_type == InventoryEventType.LOADED_ONTO_VEHICLE,
                )
            )

    if extra_packages:
        extra_ids = [pkg.id for pkg in extra_packages]
        await db.execute(
            delete(InventoryEvent).where(
                InventoryEvent.package_id.in_(extra_ids),
            )
        )
        await db.execute(
            delete(Package).where(
                Package.id.in_(extra_ids),
            )
        )


async def _prune_extra_demo_couriers(db: AsyncSession, *, tenant_id) -> int:
    result = await db.execute(
        select(Courier.id, Courier.user_id)
        .join(User, Courier.user_id == User.id)
        .where(
            Courier.tenant_id == tenant_id,
            User.email.notin_(DEMO_COURIER_EMAILS),
        )
    )
    stale_rows = result.all()
    if not stale_rows:
        return 0

    courier_ids = [row.id for row in stale_rows]
    user_ids = [row.user_id for row in stale_rows]

    route_result = await db.execute(
        select(Route.id).where(Route.courier_id.in_(courier_ids))
    )
    route_ids = list(route_result.scalars().all())

    await db.execute(
        update(Order)
        .where(Order.assigned_courier_id.in_(courier_ids))
        .values(
            assigned_courier_id=None,
            route_id=None,
            scheduled_for_date=None,
        )
    )
    await db.execute(
        update(InventoryEvent)
        .where(InventoryEvent.courier_id.in_(courier_ids))
        .values(courier_id=None)
    )

    if route_ids:
        await db.execute(
            update(Order)
            .where(Order.route_id.in_(route_ids))
            .values(route_id=None)
        )
        await db.execute(delete(SLAAlert).where(SLAAlert.route_id.in_(route_ids)))
        await db.execute(delete(RouteStop).where(RouteStop.route_id.in_(route_ids)))
        await db.execute(delete(Route).where(Route.id.in_(route_ids)))

    await db.execute(delete(SLAAlert).where(SLAAlert.courier_id.in_(courier_ids)))
    await db.execute(delete(LocationSnapshot).where(LocationSnapshot.courier_id.in_(courier_ids)))
    await db.execute(
        delete(courier_vehicle_tags).where(
            courier_vehicle_tags.c.courier_id.in_(courier_ids)
        )
    )
    await db.execute(delete(Courier).where(Courier.id.in_(courier_ids)))
    await db.execute(
        update(User)
        .where(User.id.in_(user_ids))
        .values(is_active=False)
    )
    await db.flush()
    return len(courier_ids)


async def seed_demo() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)

    async with async_session() as db:
        default_tenant = await ensure_default_tenant(db)
        await _ensure_user(
            db,
            tenant_id=default_tenant.id,
            email="admin@delivery.com",
            password="admin123",
            role=UserRole.ADMIN,
        )
        dispatcher = await _ensure_user(
            db,
            tenant_id=default_tenant.id,
            email="dispatcher@delivery.com",
            password="changeme123",
            role=UserRole.DISPATCHER,
        )
        pruned_courier_count = await _prune_extra_demo_couriers(
            db,
            tenant_id=default_tenant.id,
        )
        vehicle_spec_map = await _ensure_vehicle_specs(db)

        def _spec_id(spec_key: tuple[str, str, int]):
            spec = vehicle_spec_map.get(spec_key)
            return spec.id if spec is not None else None

        courier_lookup: dict[str, Courier] = {}
        for definition in DEMO_COURIER_DEFINITIONS:
            courier_user = await _ensure_user(
                db,
                tenant_id=default_tenant.id,
                email=definition["email"],
                password=DEMO_COURIER_PASSWORD,
                role=UserRole.COURIER,
            )
            courier_lookup[definition["slot"]] = await _ensure_courier(
                db,
                tenant_id=default_tenant.id,
                user_id=courier_user.id,
                vehicle_type=definition["vehicle_type"],
                max_weight_kg=definition["max_weight_kg"],
                availability_status=definition["availability_status"],
                depot_lat=definition["depot_lat"],
                depot_lon=definition["depot_lon"],
                vehicle_spec_id=_spec_id(definition["vehicle_spec"]),
            )

        courier_moto = courier_lookup["moto"]
        courier_van = courier_lookup["van"]
        courier_truck = courier_lookup["truck"]

        orders_by_ref: dict[str, Order] = {}
        for payload in _demo_order_payloads(now):
            order, _created = await _ensure_order(
                db,
                actor_user_id=dispatcher.id,
                tenant_id=default_tenant.id,
                payload=payload,
            )

            courier_slot = payload["courier_slot"]
            if courier_slot:
                order.assigned_courier_id = courier_lookup[courier_slot].id
                await db.flush()

            await _advance_order_along_path(
                db,
                order=order,
                path=payload["path"],
                actor_user_id=dispatcher.id,
                reason_prefix="Seeded demo transition",
            )

            if payload["external_ref"] == "DEMO-FAILED-001" and order.status == OrderStatus.FAILED:
                order.failed_at = order.failed_at or (now - timedelta(minutes=30))
                order.attempt_count = max(order.attempt_count, 1)
                order.customer_response_status = FailedOrderCustomerResponseStatus.PENDING
                order.customer_response_due_at = now + timedelta(hours=48)
                order.escalated_at = None
                await db.flush()

            orders_by_ref[payload["external_ref"]] = order

        await _ensure_order_route(
            db,
            order=orders_by_ref["DEMO-ASSIGNED-001"],
            courier=courier_moto,
            route_status=RouteStatus.PLANNED,
            pickup_status=StopStatus.PENDING,
            delivery_status=StopStatus.PENDING,
        )
        await _ensure_loaded_packages_for_order(
            db,
            order=orders_by_ref["DEMO-ASSIGNED-001"],
            courier=courier_moto,
            minimum_package_count=1,
            load_on_vehicle=False,
        )
        moto_demo_route = await db.get(Route, orders_by_ref["DEMO-ASSIGNED-001"].route_id)
        if moto_demo_route is not None:
            for ref in ("DEMO-ASSIGNED-002", "DEMO-ASSIGNED-003"):
                extra_order = orders_by_ref[ref]
                extra_order.assigned_courier_id = courier_moto.id
                extra_order.route_id = moto_demo_route.id
                if extra_order.status == OrderStatus.PENDING:
                    extra_order.status = OrderStatus.ASSIGNED
                await db.flush()
                await _ensure_loaded_packages_for_order(
                    db,
                    order=extra_order,
                    courier=courier_moto,
                    minimum_package_count=1,
                    load_on_vehicle=True,
                )

            extra_delivery_stops = [
                {
                    "sequence_number": 3,
                    "external_ref": "DEMO-ASSIGNED-002",
                    "lat": 41.0448,
                    "lon": 29.0026,
                    "address": "Besiktas Barbaros Blvd Drop Point, Istanbul",
                },
                {
                    "sequence_number": 4,
                    "external_ref": "DEMO-ASSIGNED-003",
                    "lat": 40.9903,
                    "lon": 29.0564,
                    "address": "Kadikoy Rıhtım Delivery Point, Istanbul",
                },
            ]
            for stop_data in extra_delivery_stops:
                result = await db.execute(
                    select(RouteStop).where(
                        RouteStop.route_id == moto_demo_route.id,
                        RouteStop.sequence_number == stop_data["sequence_number"],
                        RouteStop.stop_type == StopType.DELIVERY,
                    )
                )
                existing_extra_stop = result.scalar_one_or_none()
                stop_order_id = orders_by_ref[stop_data["external_ref"]].id
                if existing_extra_stop is None:
                    db.add(
                        RouteStop(
                            tenant_id=moto_demo_route.tenant_id,
                            route_id=moto_demo_route.id,
                            order_id=stop_order_id,
                            sequence_number=stop_data["sequence_number"],
                            stop_type=StopType.DELIVERY,
                            lat=stop_data["lat"],
                            lon=stop_data["lon"],
                            address=stop_data["address"],
                            planned_arrival=now + timedelta(minutes=25 + 10 * stop_data["sequence_number"]),
                            actual_arrival=None,
                            status=StopStatus.PENDING,
                            failure_reason=None,
                            notes=None,
                            dispatcher_note=None,
                        )
                    )
                else:
                    existing_extra_stop.order_id = stop_order_id
                    existing_extra_stop.lat = stop_data["lat"]
                    existing_extra_stop.lon = stop_data["lon"]
                    existing_extra_stop.address = stop_data["address"]
                    existing_extra_stop.planned_arrival = now + timedelta(
                        minutes=25 + 10 * stop_data["sequence_number"]
                    )
                    existing_extra_stop.actual_arrival = None
                    existing_extra_stop.status = StopStatus.PENDING
                    existing_extra_stop.failure_reason = None
                    existing_extra_stop.notes = None
                    existing_extra_stop.dispatcher_note = None
                await db.flush()
        await _ensure_order_route(
            db,
            order=orders_by_ref["DEMO-INTRANSIT-001"],
            courier=courier_van,
            route_status=RouteStatus.ACTIVE,
            pickup_status=StopStatus.COMPLETED,
            delivery_status=StopStatus.ARRIVED,
        )
        await _ensure_order_route(
            db,
            order=orders_by_ref["DEMO-FAILED-001"],
            courier=courier_van,
            route_status=RouteStatus.ABORTED,
            pickup_status=StopStatus.COMPLETED,
            delivery_status=StopStatus.FAILED,
            delivery_failure_reason=FailureReason.NOT_HOME,
            delivery_notes="No answer at doorbell.",
        )
        await _ensure_order_route(
            db,
            order=orders_by_ref["DEMO-DELIVERED-001"],
            courier=courier_truck,
            route_status=RouteStatus.COMPLETED,
            pickup_status=StopStatus.COMPLETED,
            delivery_status=StopStatus.COMPLETED,
        )

        for idx, courier in enumerate(courier_lookup.values(), start=1):
            if courier.availability_status not in {
                AvailabilityStatus.ONLINE,
                AvailabilityStatus.ON_BREAK,
            }:
                continue
            location_offset = ((idx % 7) - 3) * 0.0025
            await _ensure_recent_location(
                db,
                courier=courier,
                lat=round(courier.depot_lat + location_offset, 6),
                lon=round(courier.depot_lon - location_offset, 6),
            )
        await _ensure_optimization_runs(db, default_tenant.id)

        await db.commit()

        print("Demo seed completed.")
        print("")
        print("Credentials")
        print("  Admin:       admin@delivery.com / admin123")
        print("  Dispatcher:  dispatcher@delivery.com / changeme123")
        print("  Courier:     courier.moto@delivery.com / courier123")
        print(f"  Fleet:       {len(DEMO_COURIER_DEFINITIONS)} couriers / vehicles")
        if pruned_courier_count:
            print(f"  Pruned:      {pruned_courier_count} stale courier profile(s)")
        print("")
        print("Frontend URLs")
        print("  Dashboard:   http://localhost:5173")
        print("  Tracking:    http://localhost:5175")
        print("")
        print("Tracking links")
        print(
            "  Failed order:    "
            f"http://localhost:5175/?token={orders_by_ref['DEMO-FAILED-001'].tracking_token}"
        )
        print(
            "  In-transit order:"
            f" http://localhost:5175/?token={orders_by_ref['DEMO-INTRANSIT-001'].tracking_token}"
        )
        print(
            "  Delivered order: "
            f"http://localhost:5175/?token={orders_by_ref['DEMO-DELIVERED-001'].tracking_token}"
        )


if __name__ == "__main__":
    run_migrations()
    asyncio.run(seed_demo())
