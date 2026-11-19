import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models.enums import OrderStatus, OrderType, StopStatus, StopType
from app.services import geofencing_service
from app.utils.compat import UTC


def test_arrival_radius_is_configurable_by_order_type():
    assert geofencing_service.arrival_radius_for_order_type(OrderType.ON_DEMAND) == settings.ARRIVAL_RADIUS_ON_DEMAND_METERS
    assert geofencing_service.arrival_radius_for_order_type(OrderType.SCHEDULED) == settings.ARRIVAL_RADIUS_SCHEDULED_METERS
    assert geofencing_service.arrival_radius_for_order_type(OrderType.BULK) == settings.ARRIVAL_RADIUS_BULK_METERS
    assert geofencing_service.arrival_radius_for_order_type(OrderType.REVERSE) == settings.ARRIVAL_RADIUS_REVERSE_METERS


def test_should_trigger_dwell_alert_when_timeout_elapsed():
    now = datetime.now(UTC).replace(tzinfo=None)
    first_exit_at = now - timedelta(seconds=settings.GEOFENCE_DWELL_TIMEOUT_SECONDS + 1)
    assert geofencing_service.should_trigger_dwell_alert(
        first_exit_at=first_exit_at,
        now=now,
        dwell_timeout_seconds=settings.GEOFENCE_DWELL_TIMEOUT_SECONDS,
        already_alerted=False,
    )


def test_should_not_trigger_dwell_alert_if_already_alerted():
    now = datetime.now(UTC).replace(tzinfo=None)
    first_exit_at = now - timedelta(seconds=settings.GEOFENCE_DWELL_TIMEOUT_SECONDS + 100)
    assert not geofencing_service.should_trigger_dwell_alert(
        first_exit_at=first_exit_at,
        now=now,
        dwell_timeout_seconds=settings.GEOFENCE_DWELL_TIMEOUT_SECONDS,
        already_alerted=True,
    )


def test_should_not_trigger_dwell_alert_without_first_exit():
    assert not geofencing_service.should_trigger_dwell_alert(
        first_exit_at=None,
        now=datetime.now(UTC).replace(tzinfo=None),
        dwell_timeout_seconds=settings.GEOFENCE_DWELL_TIMEOUT_SECONDS,
        already_alerted=False,
    )


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, execute_values):
        self._execute_values = list(execute_values)
        self.flush_calls = 0
        # Track every ORM object the service stages so layer-0 history
        # writes (`apply_stop_status_transition` adds a StopStatusHistory
        # row alongside the column mutation) don't blow up the stub.
        self.added: list = []

    async def execute(self, _query):
        if self._execute_values:
            return _ScalarResult(self._execute_values.pop(0))
        return _ScalarResult(None)

    async def flush(self):
        self.flush_calls += 1

    def add(self, obj) -> None:
        self.added.append(obj)

    def add_all(self, objs) -> None:
        self.added.extend(objs)


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        return 1


def _active_route(route_id: uuid.UUID, courier_id: uuid.UUID, tenant_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=route_id,
        courier_id=courier_id,
        tenant_id=tenant_id,
    )


@pytest.mark.asyncio
async def test_process_location_update_auto_arrives_delivery_and_transitions_in_transit(monkeypatch):
    route_id = uuid.uuid4()
    stop_id = uuid.uuid4()
    order_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    stop = SimpleNamespace(
        id=stop_id,
        order_id=order_id,
        sequence_number=1,
        status=StopStatus.PENDING,
        stop_type=StopType.DELIVERY,
        lat=41.0,
        lon=29.0,
        actual_arrival=None,
    )
    route = _active_route(route_id, courier_id, tenant_id)
    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        type=OrderType.ON_DEMAND,
        status=OrderStatus.PICKED_UP,
        external_ref="ext-1",
        customer_email="customer@example.com",
        customer_phone="+15550001111",
        assigned_courier_id=courier_id,
    )

    db = _FakeDB([route, stop, order])
    fake_redis = _FakeRedis()
    transitions: list[OrderStatus] = []

    async def _mock_get_redis():
        return fake_redis

    async def _mock_transition(_db, _order, status, **_kwargs):
        transitions.append(status)

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(geofencing_service, "get_redis", _mock_get_redis)
    monkeypatch.setattr(geofencing_service, "apply_order_status_transition", _mock_transition)
    monkeypatch.setattr(geofencing_service, "_emit_geofence_webhook", _noop)
    monkeypatch.setattr(geofencing_service, "_emit_notification_event", _noop)
    monkeypatch.setattr(geofencing_service, "log_audit_event", _noop)

    result = await geofencing_service.process_location_update(
        db,
        courier_id=courier_id,
        lat=41.0,
        lon=29.0,
    )

    assert result["auto_arrived"] is True
    assert stop.status == StopStatus.ARRIVED
    assert stop.actual_arrival is not None
    assert transitions == [OrderStatus.IN_TRANSIT]
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_process_location_update_emits_dwell_alert_once(monkeypatch):
    route_id = uuid.uuid4()
    stop_id = uuid.uuid4()
    order_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    stop = SimpleNamespace(
        id=stop_id,
        order_id=order_id,
        sequence_number=1,
        status=StopStatus.ARRIVED,
        stop_type=StopType.DELIVERY,
        lat=41.0,
        lon=29.0,
        actual_arrival=datetime.now(UTC).replace(tzinfo=None),
    )
    route = _active_route(route_id, courier_id, tenant_id)
    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        type=OrderType.ON_DEMAND,
        status=OrderStatus.IN_TRANSIT,
        external_ref="ext-1",
        customer_email="customer@example.com",
        customer_phone="+15550001111",
        assigned_courier_id=courier_id,
    )

    db = _FakeDB([route, stop, order])
    fake_redis = _FakeRedis()
    first_exit_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=settings.GEOFENCE_DWELL_TIMEOUT_SECONDS + 10)
    fake_redis.store[f"geofence:first_exit:{stop_id}"] = first_exit_time.isoformat()
    webhook_events: list[str] = []

    async def _mock_get_redis():
        return fake_redis

    async def _mock_webhook(event_type, _payload):
        webhook_events.append(event_type)

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(geofencing_service, "get_redis", _mock_get_redis)
    monkeypatch.setattr(geofencing_service, "_emit_geofence_webhook", _mock_webhook)
    monkeypatch.setattr(geofencing_service, "_emit_notification_event", _noop)
    monkeypatch.setattr(geofencing_service, "log_audit_event", _noop)

    result = await geofencing_service.process_location_update(
        db,
        courier_id=courier_id,
        lat=41.02,
        lon=29.02,
    )

    assert result["auto_arrived"] is False
    assert result["dwell_alerted"] is True
    assert webhook_events == ["courier.dwell_alert"]


@pytest.mark.asyncio
async def test_process_location_update_handles_redis_outage_with_noop_state(monkeypatch):
    route_id = uuid.uuid4()
    stop_id = uuid.uuid4()
    order_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    stop = SimpleNamespace(
        id=stop_id,
        order_id=order_id,
        sequence_number=1,
        status=StopStatus.PENDING,
        stop_type=StopType.DELIVERY,
        lat=41.0,
        lon=29.0,
        actual_arrival=None,
    )
    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        type=OrderType.ON_DEMAND,
        status=OrderStatus.PICKED_UP,
        external_ref="ext-1",
        customer_email="customer@example.com",
        customer_phone="+15550001111",
        assigned_courier_id=courier_id,
    )
    db = _FakeDB([_active_route(route_id, courier_id, tenant_id), stop, order])

    async def _broken_get_redis():
        raise RuntimeError("redis unavailable")

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(geofencing_service, "get_redis", _broken_get_redis)
    monkeypatch.setattr(geofencing_service, "_emit_geofence_webhook", _noop)
    monkeypatch.setattr(geofencing_service, "_emit_notification_event", _noop)
    monkeypatch.setattr(geofencing_service, "log_audit_event", _noop)
    monkeypatch.setattr(
        geofencing_service,
        "apply_order_status_transition",
        _noop,
    )
    geofencing_service._redis_unavailable_logged = False

    result = await geofencing_service.process_location_update(
        db,
        courier_id=courier_id,
        lat=41.0,
        lon=29.0,
    )

    assert result["auto_arrived"] is True
    assert stop.status == StopStatus.ARRIVED
