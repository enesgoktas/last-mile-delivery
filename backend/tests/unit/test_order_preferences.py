from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.enums import OrderStatus
from app.services import order_service


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        if not self._results:
            raise AssertionError("Unexpected execute call")
        return self._results.pop(0)

    async def flush(self):
        return None

    async def refresh(self, _obj):
        return None


@pytest.mark.asyncio
async def test_set_preferred_courier_persists_pin_to_custom_fields(monkeypatch):
    tenant_id = uuid.uuid4()
    order = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        status=OrderStatus.PENDING,
        assigned_courier_id=None,
        custom_fields={"fragile_lane": True},
    )
    courier_id = uuid.uuid4()
    courier = SimpleNamespace(id=courier_id, tenant_id=tenant_id)
    db = _FakeDB([_ScalarResult(order), _ScalarResult(courier)])

    async def _allow_permission(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        order_service.operations_policy_service,
        "ensure_permission",
        _allow_permission,
    )

    updated = await order_service.set_preferred_courier(
        db,
        order.id,
        courier_id,
        principal=SimpleNamespace(tenant_id=tenant_id),
    )

    assert updated is order
    assert order.custom_fields == {
        "fragile_lane": True,
        "preferred_courier_id": str(courier_id),
    }


@pytest.mark.asyncio
async def test_set_preferred_courier_allows_clearing_existing_pin(monkeypatch):
    tenant_id = uuid.uuid4()
    order = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        status=OrderStatus.PENDING,
        assigned_courier_id=None,
        custom_fields={"preferred_courier_id": str(uuid.uuid4()), "fragile_lane": True},
    )
    db = _FakeDB([_ScalarResult(order)])

    async def _allow_permission(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        order_service.operations_policy_service,
        "ensure_permission",
        _allow_permission,
    )

    updated = await order_service.set_preferred_courier(
        db,
        order.id,
        None,
        principal=SimpleNamespace(tenant_id=tenant_id),
    )

    assert updated is order
    assert order.custom_fields == {"fragile_lane": True}


@pytest.mark.asyncio
async def test_set_preferred_courier_rejects_non_pending_orders(monkeypatch):
    tenant_id = uuid.uuid4()
    order = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        status=OrderStatus.ASSIGNED,
        assigned_courier_id=None,
        custom_fields=None,
    )
    db = _FakeDB([_ScalarResult(order)])

    async def _allow_permission(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        order_service.operations_policy_service,
        "ensure_permission",
        _allow_permission,
    )

    with pytest.raises(HTTPException) as excinfo:
        await order_service.set_preferred_courier(
            db,
            order.id,
            uuid.uuid4(),
            principal=SimpleNamespace(tenant_id=tenant_id),
        )

    assert excinfo.value.status_code == 400
    assert "pending" in excinfo.value.detail.lower()
