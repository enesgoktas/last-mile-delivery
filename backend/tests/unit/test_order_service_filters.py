from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.models.order import Order
from app.services import order_service


def test_service_date_filter_prefers_scheduled_day_and_falls_back_to_window():
    query = order_service._build_order_filters(
        select(Order),
        service_date=date(2026, 4, 29),
    )

    sql = str(query.compile(compile_kwargs={"literal_binds": True}))

    assert "orders.scheduled_for_date = '2026-04-29'" in sql
    assert "orders.scheduled_for_date IS NULL" in sql
    assert "orders.latest_delivery >= '2026-04-29 00:00:00'" in sql
    assert "orders.earliest_delivery < '2026-04-30 00:00:00'" in sql

