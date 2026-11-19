import uuid
from types import SimpleNamespace

import pytest

from app.models.enums import FailureReason, OrderStatus, StopStatus, StopType
from app.schemas.operations_policy import default_operations_policy
from app.services import route_execution_service


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, order):
        self._order = order
        self.flush_count = 0
        # Track every ORM object the service stages so layer-0 history
        # writes (`apply_stop_status_transition` adds a StopStatusHistory
        # row alongside the column mutation) don't crash the stub.
        self.added: list = []

    async def execute(self, _query):
        return _ScalarResult(self._order)

    async def flush(self):
        self.flush_count += 1

    async def refresh(self, _obj):
        return None

    def add(self, obj) -> None:
        self.added.append(obj)

    def add_all(self, objs) -> None:
        self.added.extend(objs)


@pytest.mark.asyncio
async def test_fail_stop_auto_cancels_when_attempt_limit_reached(monkeypatch):
    order_id = uuid.uuid4()
    stop_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    stop = SimpleNamespace(
        id=stop_id,
        order_id=order_id,
        tenant_id=tenant_id,
        status=StopStatus.ARRIVED,
        stop_type=StopType.DELIVERY,
        failure_reason=None,
        notes=None,
        lat=41.015,
        lon=28.979,
    )
    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        status=OrderStatus.IN_TRANSIT,
        attempt_count=2,
        max_attempts=3,
    )
    db = _FakeDB(order)
    current_user = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id)
    transitions: list[OrderStatus] = []

    async def _mock_get_stop(_db, _route_id, requested_stop_id, _current_user, **_kwargs):
        assert requested_stop_id == stop_id
        return stop

    async def _mock_transition(_db, target_order, target_status, **_kwargs):
        transitions.append(target_status)
        target_order.status = target_status

    async def _mock_assert_prior_stops_done(*_args, **_kwargs):
        return None

    async def _mock_get_operations_policy(*_args, **_kwargs):
        return default_operations_policy()

    monkeypatch.setattr(route_execution_service, "_get_stop", _mock_get_stop)
    monkeypatch.setattr(route_execution_service, "_assert_prior_stops_done", _mock_assert_prior_stops_done)
    monkeypatch.setattr(route_execution_service, "apply_order_status_transition", _mock_transition)
    monkeypatch.setattr(
        route_execution_service.operations_policy_service,
        "get_operations_policy",
        _mock_get_operations_policy,
    )

    await route_execution_service.fail_stop(
        db,
        route_id=uuid.uuid4(),
        stop_id=stop_id,
        current_user=current_user,
        failure_reason=FailureReason.NOT_HOME,
        notes="recipient unavailable",
    )

    assert stop.status == StopStatus.FAILED
    assert stop.failure_reason == FailureReason.NOT_HOME
    assert order.attempt_count == 3
    assert order.customer_response_status.value == "PENDING"
    assert order.customer_response_due_at is not None
    assert order.escalated_at is None
    assert transitions == [OrderStatus.FAILED, OrderStatus.CANCELLED]


@pytest.mark.asyncio
async def test_fail_stop_keeps_failed_when_attempts_remaining(monkeypatch):
    order_id = uuid.uuid4()
    stop_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    stop = SimpleNamespace(
        id=stop_id,
        order_id=order_id,
        tenant_id=tenant_id,
        status=StopStatus.PENDING,
        stop_type=StopType.DELIVERY,
        failure_reason=None,
        notes=None,
        lat=41.015,
        lon=28.979,
    )
    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        status=OrderStatus.IN_TRANSIT,
        attempt_count=0,
        max_attempts=3,
    )
    db = _FakeDB(order)
    current_user = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id)
    transitions: list[OrderStatus] = []

    async def _mock_get_stop(_db, _route_id, requested_stop_id, _current_user, **_kwargs):
        assert requested_stop_id == stop_id
        return stop

    async def _mock_transition(_db, target_order, target_status, **_kwargs):
        transitions.append(target_status)
        target_order.status = target_status

    async def _mock_assert_prior_stops_done(*_args, **_kwargs):
        return None

    async def _mock_get_operations_policy(*_args, **_kwargs):
        return default_operations_policy()

    monkeypatch.setattr(route_execution_service, "_get_stop", _mock_get_stop)
    monkeypatch.setattr(route_execution_service, "_assert_prior_stops_done", _mock_assert_prior_stops_done)
    monkeypatch.setattr(route_execution_service, "apply_order_status_transition", _mock_transition)
    monkeypatch.setattr(
        route_execution_service.operations_policy_service,
        "get_operations_policy",
        _mock_get_operations_policy,
    )

    await route_execution_service.fail_stop(
        db,
        route_id=uuid.uuid4(),
        stop_id=stop_id,
        current_user=current_user,
        failure_reason=FailureReason.NOT_HOME,
        notes="recipient unavailable",
    )

    assert stop.status == StopStatus.FAILED
    assert order.attempt_count == 1
    assert order.customer_response_status.value == "PENDING"
    assert order.customer_response_due_at is not None
    assert order.escalated_at is None
    assert transitions == [OrderStatus.FAILED]
