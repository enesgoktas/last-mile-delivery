from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.models.enums import OrderStatus
from app.schemas.order import OrderCreate
from app.services import order_service
from app.utils.compat import UTC


class _FakeDB:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, item: object) -> None:
        self.items.append(item)

    async def execute(self, _query):
        class _ScalarResult:
            @staticmethod
            def scalar_one_or_none():
                return None

        return _ScalarResult()

    async def flush(self) -> None:
        return None

    async def refresh(self, _obj: object) -> None:
        return None

@pytest.mark.asyncio
async def test_create_order_does_not_fail_when_insertion_dispatch_enqueue_fails(monkeypatch):
    monkeypatch.setattr(settings, "TRACKING_JWT_SECRET", "unit-test-tracking-secret-32b!")
    monkeypatch.setattr(settings, "TRACKING_JWT_ACTIVE_KID", "test-tracking-v1")
    monkeypatch.setattr(settings, "TRACKING_JWT_PREVIOUS_KEYS", {})
    now = datetime.now(UTC).replace(tzinfo=None)
    payload = OrderCreate(
        type="ON_DEMAND",
        priority="NORMAL",
        pickup_address="Warehouse A",
        pickup_lat=41.0,
        pickup_lon=29.0,
        delivery_address="Customer B",
        delivery_lat=41.01,
        delivery_lon=29.01,
        weight_kg=2.5,
        is_fragile=False,
        is_temp_sensitive=False,
        earliest_delivery=now + timedelta(hours=1),
        latest_delivery=now + timedelta(hours=2),
        customer_email="customer@example.com",
        customer_phone="+15550001111",
        external_ref=f"ext-{uuid.uuid4()}",
        notes=None,
    )

    async def _noop_record_transition(*_args, **_kwargs):
        return None

    async def _always_in_flight(*_args, **_kwargs):
        return True

    monkeypatch.setattr(order_service, "_record_status_transition", _noop_record_transition)
    monkeypatch.setattr(order_service, "_day_plan_in_flight", _always_in_flight)
    monkeypatch.setattr(
        order_service,
        "enqueue_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )

    created = await order_service.create_order(
        _FakeDB(),
        payload,
        tenant_id=uuid.uuid4(),
    )

    assert created is not None
    assert created.status == OrderStatus.PENDING
    assert created.tracking_token


@pytest.mark.asyncio
async def test_create_order_does_not_enqueue_when_day_plan_not_in_flight(monkeypatch):
    """Day-in-flight gate: before batch has run for the order's day, the
    order accumulates as PENDING and no insertion-dispatch is queued."""
    monkeypatch.setattr(settings, "TRACKING_JWT_SECRET", "unit-test-tracking-secret-32b!")
    monkeypatch.setattr(settings, "TRACKING_JWT_ACTIVE_KID", "test-tracking-v1")
    monkeypatch.setattr(settings, "TRACKING_JWT_PREVIOUS_KEYS", {})
    now = datetime.now(UTC).replace(tzinfo=None)
    payload = OrderCreate(
        type="ON_DEMAND",
        priority="NORMAL",
        pickup_address="Warehouse A",
        pickup_lat=41.0,
        pickup_lon=29.0,
        delivery_address="Customer B",
        delivery_lat=41.01,
        delivery_lon=29.01,
        weight_kg=2.5,
        is_fragile=False,
        is_temp_sensitive=False,
        earliest_delivery=now + timedelta(hours=1),
        latest_delivery=now + timedelta(hours=2),
        customer_email="customer@example.com",
        customer_phone="+15550001111",
        external_ref=f"ext-{uuid.uuid4()}",
        notes=None,
    )

    async def _noop_record_transition(*_args, **_kwargs):
        return None

    enqueue_calls: list = []

    def _track_enqueue(*args, **kwargs):
        enqueue_calls.append((args, kwargs))

    monkeypatch.setattr(order_service, "_record_status_transition", _noop_record_transition)
    monkeypatch.setattr(order_service, "enqueue_task", _track_enqueue)

    created = await order_service.create_order(_FakeDB(), payload, tenant_id=uuid.uuid4())

    assert created is not None
    assert created.status == OrderStatus.PENDING
    # No insertion dispatch should have been enqueued — empty fake DB has no
    # orders scheduled for today, so the day's plan is not in flight.
    assert enqueue_calls == []


@pytest.mark.asyncio
async def test_create_order_enqueues_for_each_split_ride_sibling(monkeypatch):
    """When a large RIDE is split into siblings during creation, every member
    of the group (parent + siblings) must be independently enqueued for the
    insertion heuristic — that's the only way each lands on a different
    vehicle."""
    monkeypatch.setattr(settings, "TRACKING_JWT_SECRET", "unit-test-tracking-secret-32b!")
    monkeypatch.setattr(settings, "TRACKING_JWT_ACTIVE_KID", "test-tracking-v1")
    monkeypatch.setattr(settings, "TRACKING_JWT_PREVIOUS_KEYS", {})
    now = datetime.now(UTC).replace(tzinfo=None)
    payload = OrderCreate(
        type="RIDE",
        priority="NORMAL",
        pickup_address="Airport Terminal 1",
        pickup_lat=41.0, pickup_lon=29.0,
        delivery_address="Hotel Galata",
        delivery_lat=41.025, delivery_lon=28.97,
        weight_kg=0.0,
        is_fragile=False,
        is_temp_sensitive=False,
        earliest_pickup=now + timedelta(hours=1),
        latest_pickup=now + timedelta(hours=1, minutes=30),
        earliest_delivery=now + timedelta(hours=1, minutes=30),
        latest_delivery=now + timedelta(hours=3),
        customer_email="vip@example.com",
        customer_phone="+15551112222",
        external_ref=f"ext-{uuid.uuid4()}",
        notes=None,
        passenger_count=10,
        passenger_name="Big Group",
        allow_split=True,
        passengers_per_vehicle=4,
    )

    async def _noop_record_transition(*_args, **_kwargs):
        return None

    async def _always_in_flight(*_args, **_kwargs):
        return True

    async def _ride_features_enabled_policy(_db, _principal):
        from app.schemas.operations_policy import OperationsPolicy

        policy = OperationsPolicy()
        policy.feature_flags.rides_enabled = True
        policy.feature_flags.deliveries_enabled = True
        return policy

    enqueue_calls: list = []

    def _track_enqueue(*args, **kwargs):
        enqueue_calls.append((args, kwargs))

    monkeypatch.setattr(order_service, "_record_status_transition", _noop_record_transition)
    monkeypatch.setattr(order_service, "_day_plan_in_flight", _always_in_flight)
    monkeypatch.setattr(
        order_service.operations_policy_service,
        "get_operations_policy",
        _ride_features_enabled_policy,
    )
    monkeypatch.setattr(order_service, "enqueue_task", _track_enqueue)

    created = await order_service.create_order(_FakeDB(), payload, tenant_id=uuid.uuid4())

    assert created is not None
    # 10 passengers / 4 per vehicle = 3 vehicles → parent + 2 siblings = 3 enqueues
    assert len(enqueue_calls) == 3
