from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import order_service


class _ScalarValues:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class _QueryResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return _ScalarValues(self._values)


def _extract_filter_value(query, column_name: str):
    whereclause = getattr(query, "whereclause", None)
    if whereclause is None:
        return None

    clauses = list(getattr(whereclause, "clauses", [whereclause]))
    for clause in clauses:
        left = getattr(clause, "left", None)
        if getattr(left, "name", None) != column_name:
            continue
        right = getattr(clause, "right", None)
        if hasattr(right, "value"):
            return right.value
    return None


class _TenantScopedOrderDB:
    def __init__(self, orders):
        self._orders = list(orders)

    async def execute(self, query):
        tenant_id = _extract_filter_value(query, "tenant_id")
        filtered = [
            order
            for order in self._orders
            if tenant_id is None or order.tenant_id == tenant_id
        ]
        filtered.sort(key=lambda order: order.created_at, reverse=True)
        return _QueryResult(filtered)


@pytest.mark.asyncio
async def test_tenant_isolation():
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    db = _TenantScopedOrderDB(
        [
            SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_a, created_at=datetime(2026, 3, 14, 10, 0, 0)),
            SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_b, created_at=datetime(2026, 3, 14, 9, 0, 0)),
        ]
    )

    orders = await order_service.get_orders(
        db,
        principal=SimpleNamespace(tenant_id=tenant_a),
        limit=50,
    )

    assert len(orders) == 1
    assert orders[0].tenant_id == tenant_a
