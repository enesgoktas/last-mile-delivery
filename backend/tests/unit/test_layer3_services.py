"""Layer 3 — verify empirical-service-time math, driver-event mapping,
and the source-level integration guards.

Schema-only items (`order_ratings`, `demand_observations`, exception
taxonomy columns on `Order`) get integration coverage via their
respective endpoint suites when those flows ship; here we focus on the
arithmetic + hook integration the dashboards actually depend on.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from types import SimpleNamespace

import pytest

from app.models.enums import AvailabilityStatus
from app.services.empirical_service_time_service import (
    _MIN_SAMPLES_FOR_SOLVER_READ,
    address_hash_for,
)
from app.services.lifecycle_history_service import (
    _driver_event_type_for_transition,
)
from app.models.driver_event import DriverEventType


def _run(coro):
    return asyncio.run(coro)


# ── address_hash_for / geohash ────────────────────────────────────────


def test_address_hash_is_deterministic_for_same_point():
    a = address_hash_for(41.015, 28.979)
    b = address_hash_for(41.015, 28.979)
    assert a == b
    # 8-char precision by default.
    assert len(a) == 8


def test_address_hash_differs_for_different_buildings():
    # ~100 m apart — well above the 19 m precision threshold.
    a = address_hash_for(41.015, 28.979)
    b = address_hash_for(41.016, 28.980)
    assert a != b


def test_address_hash_tolerant_to_gps_jitter():
    """Two readings ~1 m apart should land in the same geohash bucket
    so repeat visits to the same door don't fragment the median."""
    # ~0.5 m difference at Istanbul latitude.
    a = address_hash_for(41.0150000, 28.9790000)
    b = address_hash_for(41.0150045, 28.9790045)
    assert a == b


# ── _driver_event_type_for_transition mapping ──────────────────────────


def test_driver_event_shift_start_on_offline_to_online():
    assert (
        _driver_event_type_for_transition(
            AvailabilityStatus.OFFLINE, AvailabilityStatus.ONLINE
        )
        == DriverEventType.SHIFT_START
    )


def test_driver_event_shift_end_on_online_to_offline():
    assert (
        _driver_event_type_for_transition(
            AvailabilityStatus.ONLINE, AvailabilityStatus.OFFLINE
        )
        == DriverEventType.SHIFT_END
    )


def test_driver_event_break_start_and_end():
    assert (
        _driver_event_type_for_transition(
            AvailabilityStatus.ONLINE, AvailabilityStatus.ON_BREAK
        )
        == DriverEventType.BREAK_START
    )
    assert (
        _driver_event_type_for_transition(
            AvailabilityStatus.ON_BREAK, AvailabilityStatus.ONLINE
        )
        == DriverEventType.BREAK_END
    )


def test_driver_event_returns_none_for_unmapped_transition():
    # OFFLINE → ON_BREAK has no semantic event; the audit trail captures
    # it through CourierAvailabilityHistory only.
    assert (
        _driver_event_type_for_transition(
            AvailabilityStatus.OFFLINE, AvailabilityStatus.ON_BREAK
        )
        is None
    )


# ── Source-level guards on integration hooks ──────────────────────────


def test_apply_courier_change_emits_driver_event():
    """`record_courier_availability_change` must add a DriverEvent for
    shift/break transitions on top of the availability_history row."""
    from app.services import lifecycle_history_service

    src = inspect.getsource(
        lifecycle_history_service.record_courier_availability_change
    )
    assert "DriverEvent" in src
    assert "_driver_event_type_for_transition" in src


def test_complete_stop_records_empirical_service_time():
    """The stop-completion path must call `record_observation` for
    DELIVERY stops so the empirical service-time table fills naturally
    over time."""
    from app.services import route_execution_service

    src = inspect.getsource(route_execution_service.complete_stop)
    assert "record_observation" in src
    assert "empirical_service_time_service" in src


def test_lookup_threshold_constant_is_stable():
    """Solver should not read empirical service times until we have
    enough samples. Threshold is part of the API contract — tighten
    deliberately, not by accident."""
    assert _MIN_SAMPLES_FOR_SOLVER_READ >= 3


# ── Order rating endpoint hook surface ───────────────────────────────


def test_orders_api_exposes_rating_endpoint():
    from app.api import orders

    src = inspect.getsource(orders)
    assert "/{order_id}/rating" in src
    assert "OrderRatingResponse" in src
