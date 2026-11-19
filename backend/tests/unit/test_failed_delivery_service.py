import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.enums import FailedOrderCustomerResponseStatus, OrderStatus
from app.services import failed_delivery_service
from app.utils.compat import UTC


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarResult(self._items)


class _FakeDB:
    def __init__(self, orders):
        self._orders = orders
        self.flush_calls = 0

    async def execute(self, _query):
        return _ExecuteResult(self._orders)

    async def flush(self):
        self.flush_calls += 1


@pytest.mark.asyncio
async def test_escalate_stale_failed_orders_marks_orders_and_logs_audit(monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    tenant_id = uuid.uuid4()
    stale_order = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        external_ref="ext-1",
        status=OrderStatus.FAILED,
        failed_at=now - timedelta(hours=50),
        customer_response_status=FailedOrderCustomerResponseStatus.PENDING,
        customer_response_due_at=now - timedelta(minutes=5),
        escalated_at=None,
    )
    db = _FakeDB([stale_order])
    audit_calls: list[dict] = []
    webhook_calls: list[tuple[str, dict]] = []

    async def _mock_log_audit_event(_db, **kwargs):
        audit_calls.append(kwargs)
        return None

    monkeypatch.setattr(failed_delivery_service, "log_audit_event", _mock_log_audit_event)
    monkeypatch.setattr(
        failed_delivery_service,
        "enqueue_task",
        lambda _task, *, args=None, **_kwargs: webhook_calls.append(tuple(args or [])),
    )

    result = await failed_delivery_service.escalate_stale_failed_orders(db, now=now)

    assert result["escalated_count"] == 1
    assert result["order_ids"] == [stale_order.id]
    assert stale_order.customer_response_status == FailedOrderCustomerResponseStatus.ESCALATED
    assert stale_order.escalated_at == now
    assert len(audit_calls) == 1
    assert audit_calls[0]["tenant_id"] == tenant_id
    assert webhook_calls[0][0] == "order.failed.escalated"
    assert webhook_calls[0][1]["tenant_id"] == str(tenant_id)
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_escalate_stale_failed_orders_noop_when_no_matching_orders(monkeypatch):
    db = _FakeDB([])
    audit_calls = 0

    async def _mock_log_audit_event(_db, **_kwargs):
        nonlocal audit_calls
        audit_calls += 1
        return None

    monkeypatch.setattr(failed_delivery_service, "log_audit_event", _mock_log_audit_event)

    result = await failed_delivery_service.escalate_stale_failed_orders(db)

    assert result == {"escalated_count": 0, "order_ids": []}
    assert audit_calls == 0
    assert db.flush_calls == 0
