from datetime import datetime, timedelta, timezone

from app.utils.compat import UTC, to_naive_utc, utcnow_naive


def test_to_naive_utc_strips_timezone():
    aware = datetime(2026, 3, 30, 12, 0, tzinfo=UTC)
    naive = to_naive_utc(aware)
    assert naive.tzinfo is None
    assert naive == datetime(2026, 3, 30, 12, 0)


def test_to_naive_utc_converts_non_utc_zone():
    # 12:00 UTC+3 -> 09:00 UTC naive
    plus3 = timezone(timedelta(hours=3))
    aware = datetime(2026, 3, 30, 12, 0, tzinfo=plus3)
    naive = to_naive_utc(aware)
    assert naive.tzinfo is None
    assert naive == datetime(2026, 3, 30, 9, 0)


def test_to_naive_utc_passes_through_naive():
    naive = utcnow_naive()
    assert to_naive_utc(naive) is naive
