from datetime import datetime
from types import SimpleNamespace

from app.services import dispatch_service


def test_resolve_pickup_window_prefers_explicit_pickup_window():
    order = SimpleNamespace(
        earliest_pickup=datetime(2026, 2, 24, 9, 15, 0),
        latest_pickup=datetime(2026, 2, 24, 10, 45, 0),
        earliest_delivery=datetime(2026, 2, 24, 11, 0, 0),
        latest_delivery=datetime(2026, 2, 24, 13, 0, 0),
    )

    earliest_s, latest_s = dispatch_service._resolve_pickup_window_seconds(order)
    assert earliest_s == (9 * 3600 + 15 * 60)
    assert latest_s == (10 * 3600 + 45 * 60)


def test_resolve_pickup_window_falls_back_to_delivery_window():
    order = SimpleNamespace(
        earliest_pickup=None,
        latest_pickup=None,
        earliest_delivery=datetime(2026, 2, 24, 11, 0, 0),
        latest_delivery=datetime(2026, 2, 24, 13, 0, 0),
    )

    earliest_s, latest_s = dispatch_service._resolve_pickup_window_seconds(order)
    assert earliest_s == 0
    assert latest_s == 13 * 3600
