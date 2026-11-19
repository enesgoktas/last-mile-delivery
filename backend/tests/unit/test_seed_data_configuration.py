from __future__ import annotations

import uuid
from datetime import date, timedelta
from types import SimpleNamespace

from app import seed_bulk_orders
from app.models.enums import AvailabilityStatus, VehicleType
from app.seed_demo import DEMO_COURIER_DEFINITIONS, DEMO_COURIER_EMAILS, DEMO_FLEET_SIZE


def test_demo_seed_defines_25_unique_courier_vehicles():
    assert DEMO_FLEET_SIZE == 25
    assert len(DEMO_COURIER_DEFINITIONS) == DEMO_FLEET_SIZE

    emails = {definition["email"] for definition in DEMO_COURIER_DEFINITIONS}
    slots = {definition["slot"] for definition in DEMO_COURIER_DEFINITIONS}
    assert len(emails) == DEMO_FLEET_SIZE
    assert emails == DEMO_COURIER_EMAILS
    assert len(slots) == DEMO_FLEET_SIZE
    assert {"moto", "van", "truck"}.issubset(slots)
    assert "courier.moto2@delivery.com" not in DEMO_COURIER_EMAILS
    assert "courier.van@delivery.local" not in DEMO_COURIER_EMAILS

    active_statuses = {AvailabilityStatus.ONLINE, AvailabilityStatus.ON_BREAK}
    assert all(
        definition["availability_status"] in active_statuses
        for definition in DEMO_COURIER_DEFINITIONS
    )
    assert {definition["vehicle_type"] for definition in DEMO_COURIER_DEFINITIONS} == {
        VehicleType.MOTORCYCLE,
        VehicleType.CAR_VAN,
        VehicleType.TRUCK,
    }
    assert all(definition["vehicle_spec"] for definition in DEMO_COURIER_DEFINITIONS)


def test_bulk_seed_uses_625_orders_for_each_forward_day():
    assert seed_bulk_orders.ORDERS_PER_DAY == 625
    assert seed_bulk_orders.DAYS_AHEAD == 14

    start_date = date(2026, 5, 4)
    seeded_dates = [
        start_date + timedelta(days=day_offset)
        for day_offset in range(seed_bulk_orders.DAYS_AHEAD)
    ]
    order_counts = [seed_bulk_orders.ORDERS_PER_DAY for _target in seeded_dates]

    assert set(order_counts) == {625}
    assert sum(order_counts) == 8_750


def test_seed_external_refs_parse_by_target_date():
    target_date = date(2026, 5, 5)
    external_ref = seed_bulk_orders._seed_external_ref(target_date, 625)

    assert external_ref == "seed_2026-05-05_625"
    assert seed_bulk_orders._parse_seed_order_index(external_ref, target_date) == 625
    assert seed_bulk_orders._parse_seed_order_index(external_ref, date(2026, 5, 6)) is None
    assert seed_bulk_orders._parse_seed_order_index("seed_2026-05-05_extra", target_date) is None


def test_bulk_order_factory_supports_the_625th_order_for_a_day():
    seed_bulk_orders.rng.seed(seed_bulk_orders.RNG_SEED)
    tenant_id = uuid.uuid4()
    target_date = date(2026, 5, 5)
    depot = SimpleNamespace(
        id=uuid.uuid4(),
        address="Levent Hub, Istanbul",
        lat=41.0840,
        lon=29.0094,
    )

    order = seed_bulk_orders._make_order(
        tenant_id,
        target_date,
        seed_bulk_orders.ORDERS_PER_DAY,
        [depot],
    )

    assert order.tenant_id == tenant_id
    assert order.external_ref == "seed_2026-05-05_625"
    assert order.earliest_delivery.date() == target_date
    assert order.latest_delivery.date() == target_date
