"""Pure cadence unit tests for `recurring_order_service`.

These cover the maths of `occurrences_in_range` / `next_occurrence_after`
without touching the database. The service-level integration test exercises
the end-to-end materialisation flow in `test_recurring_order_service.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

from app.models.enums import OrderPriority, OrderType
from app.models.recurring_order import RecurrenceCadence
from app.services import recurring_order_service as svc


# ---------------------------------------------------------------------------
# Lightweight stand-in for RecurringOrderTemplate. The service layer only
# accesses a small, well-defined set of attributes from the cadence
# computation path, so we avoid spinning up SQLAlchemy here.
# ---------------------------------------------------------------------------
@dataclass
class _TemplateStub:
    cadence: RecurrenceCadence
    start_date: date
    end_date: date | None = None
    interval_days: int = 1
    day_of_week_mask: int = 0
    day_of_month: int | None = None
    max_occurrences: int | None = None
    occurrences_generated: int = 0
    delivery_window_start: time = field(default_factory=lambda: time(9, 0))
    delivery_window_end: time = field(default_factory=lambda: time(17, 0))
    pickup_window_start: time | None = None
    pickup_window_end: time | None = None
    lead_time_days: int = 7
    order_type: OrderType = OrderType.SCHEDULED
    priority: OrderPriority = OrderPriority.NORMAL


def test_daily_cadence_emits_every_day_in_range():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.DAILY,
        start_date=date(2026, 4, 28),
    )
    out = svc.occurrences_in_range(
        tpl, range_start=date(2026, 4, 28), range_end=date(2026, 5, 1)
    )
    assert out == [date(2026, 4, 28), date(2026, 4, 29), date(2026, 4, 30), date(2026, 5, 1)]


def test_daily_cadence_clips_to_start_date():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.DAILY,
        start_date=date(2026, 5, 1),
    )
    out = svc.occurrences_in_range(
        tpl, range_start=date(2026, 4, 28), range_end=date(2026, 5, 2)
    )
    assert out == [date(2026, 5, 1), date(2026, 5, 2)]


def test_weekly_cadence_uses_mask():
    # Mon=0 + Wed=2 + Fri=4 → mask 0b0010101 = 21
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.WEEKLY,
        start_date=date(2026, 4, 27),  # Monday
        day_of_week_mask=0b0010101,
    )
    out = svc.occurrences_in_range(
        tpl, range_start=date(2026, 4, 27), range_end=date(2026, 5, 3)
    )
    # Mon 27, Wed 29, Fri 1
    assert out == [date(2026, 4, 27), date(2026, 4, 29), date(2026, 5, 1)]


def test_weekly_cadence_falls_back_to_start_weekday_when_mask_zero():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.WEEKLY,
        start_date=date(2026, 4, 28),  # Tuesday
        day_of_week_mask=0,
    )
    out = svc.occurrences_in_range(
        tpl, range_start=date(2026, 4, 28), range_end=date(2026, 5, 19)
    )
    assert out == [date(2026, 4, 28), date(2026, 5, 5), date(2026, 5, 12), date(2026, 5, 19)]


def test_biweekly_cadence_anchored_to_start_date():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.BIWEEKLY,
        start_date=date(2026, 4, 28),
    )
    out = svc.occurrences_in_range(
        tpl, range_start=date(2026, 4, 28), range_end=date(2026, 6, 30)
    )
    assert out == [
        date(2026, 4, 28),
        date(2026, 5, 12),
        date(2026, 5, 26),
        date(2026, 6, 9),
        date(2026, 6, 23),
    ]


def test_monthly_cadence_caps_day_to_month_length():
    """day_of_month=31 falls back to last day of February etc."""
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.MONTHLY,
        start_date=date(2026, 1, 31),
        day_of_month=31,
    )
    out = svc.occurrences_in_range(
        tpl, range_start=date(2026, 1, 1), range_end=date(2026, 5, 1)
    )
    # Jan 31, Feb 28, Mar 31, Apr 30
    assert out == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_custom_cadence_with_3_day_step():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.CUSTOM,
        interval_days=3,
        start_date=date(2026, 4, 28),
    )
    out = svc.occurrences_in_range(
        tpl, range_start=date(2026, 4, 28), range_end=date(2026, 5, 10)
    )
    assert out == [
        date(2026, 4, 28),
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 7),
        date(2026, 5, 10),
    ]


def test_custom_cadence_aligns_to_start_when_range_starts_off_grid():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.CUSTOM,
        interval_days=3,
        start_date=date(2026, 4, 28),
    )
    out = svc.occurrences_in_range(
        tpl, range_start=date(2026, 4, 30), range_end=date(2026, 5, 6)
    )
    # Closest grid-point >= 2026-04-30 is 2026-05-01
    assert out == [date(2026, 5, 1), date(2026, 5, 4)]


def test_end_date_clamps_output():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.DAILY,
        start_date=date(2026, 4, 28),
        end_date=date(2026, 4, 30),
    )
    out = svc.occurrences_in_range(
        tpl, range_start=date(2026, 4, 28), range_end=date(2026, 5, 5)
    )
    assert out == [date(2026, 4, 28), date(2026, 4, 29), date(2026, 4, 30)]


def test_max_occurrences_caps_remaining_slots():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.DAILY,
        start_date=date(2026, 4, 28),
        max_occurrences=5,
        occurrences_generated=3,
    )
    out = svc.occurrences_in_range(
        tpl, range_start=date(2026, 4, 28), range_end=date(2026, 5, 10)
    )
    # Only 5 - 3 = 2 slots remaining
    assert out == [date(2026, 4, 28), date(2026, 4, 29)]


def test_no_occurrences_when_range_before_start():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.DAILY,
        start_date=date(2026, 5, 1),
    )
    assert svc.occurrences_in_range(
        tpl, range_start=date(2026, 4, 1), range_end=date(2026, 4, 30)
    ) == []


def test_no_occurrences_when_max_already_reached():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.DAILY,
        start_date=date(2026, 4, 28),
        max_occurrences=2,
        occurrences_generated=2,
    )
    assert svc.occurrences_in_range(
        tpl, range_start=date(2026, 4, 28), range_end=date(2026, 5, 5)
    ) == []


def test_next_occurrence_after_returns_first_future_match():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.WEEKLY,
        start_date=date(2026, 4, 27),
        day_of_week_mask=0b0010101,  # Mon, Wed, Fri
    )
    nxt = svc.next_occurrence_after(tpl, date(2026, 4, 30))  # Thu
    assert nxt == date(2026, 5, 1)  # Fri


def test_next_occurrence_after_returns_none_past_end_date():
    tpl = _TemplateStub(
        cadence=RecurrenceCadence.DAILY,
        start_date=date(2026, 4, 28),
        end_date=date(2026, 4, 28),
    )
    assert svc.next_occurrence_after(tpl, date(2026, 4, 28)) is None
