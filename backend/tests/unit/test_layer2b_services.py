"""Layer 2b — verify replay journal serialization, ETA log emission,
and plan-stability score math without a DB.

The DB-coupled paths (insert + on-conflict upsert) are exercised by
the integration suite. Here we pin the pure-Python logic so refactors
can't silently break the arithmetic.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.algorithms.vrp_solver import OrderData, VehicleData, VRPInput
from app.models.enums import OrderPriority, VehicleType
from app.services.plan_stability_service import record_plan_stability
from app.services.replay_journal_service import _vrp_input_to_payload


class _StubSession:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    def add_all(self, objs) -> None:
        self.added.extend(objs)

    async def flush(self) -> None:
        return None


def _run(coro):
    return asyncio.run(coro)


# ── replay_journal_service._vrp_input_to_payload ────────────────────────


def _make_vrp_input() -> VRPInput:
    return VRPInput(
        locations=[(41.0, 28.9), (41.1, 28.95)],
        distance_matrix=[[0, 1000], [1000, 0]],
        duration_matrix=[[0, 600], [600, 0]],
        orders=[
            OrderData(
                order_id="o-1",
                pickup_index=0,
                delivery_index=1,
                weight_grams=5000,
                earliest_time_s=0,
                latest_time_s=86400,
                hard_latest_time_s=86400,
                priority=OrderPriority.NORMAL,
                pickup_is_depot=True,
            )
        ],
        vehicles=[
            VehicleData(
                courier_id="c-1",
                vehicle_type=VehicleType.CAR_VAN,
                capacity_grams=50_000,
                depot_index=0,
                start_time_s=8 * 3600,
                end_time_s=18 * 3600,
            )
        ],
    )


def test_replay_payload_is_json_serializable_dict():
    payload = _vrp_input_to_payload(_make_vrp_input())
    assert isinstance(payload, dict)
    # Top-level keys for round-trip.
    assert "orders" in payload
    assert "vehicles" in payload
    assert "distance_matrix" in payload
    # Nested dataclasses get walked into dicts.
    assert payload["orders"][0]["order_id"] == "o-1"
    assert payload["vehicles"][0]["courier_id"] == "c-1"
    # Enums become their string value (StrEnum).
    assert payload["orders"][0]["priority"] == "NORMAL"
    assert payload["vehicles"][0]["vehicle_type"] == "CAR_VAN"


# ── plan_stability_service.record_plan_stability ─────────────────────────


def test_plan_stability_returns_none_for_empty_prior():
    db = _StubSession()
    result = _run(
        record_plan_stability(
            db,
            tenant_id=uuid.uuid4(),
            optimization_run_id=uuid.uuid4(),
            prior_run_id=None,
            prior_assignments={},
            new_assignments={
                uuid.uuid4(): (uuid.uuid4(), date(2026, 5, 13)),
            },
        )
    )
    # No prior plan → stability is undefined.
    assert result is None
    assert db.added == []


def test_plan_stability_pins_unchanged_count_and_pct():
    db = _StubSession()
    courier_a = uuid.uuid4()
    courier_b = uuid.uuid4()
    day1 = date(2026, 5, 13)
    day2 = date(2026, 5, 14)
    o1 = uuid.uuid4()
    o2 = uuid.uuid4()
    o3 = uuid.uuid4()
    o4 = uuid.uuid4()
    prior = {
        o1: (courier_a, day1),  # stays put
        o2: (courier_a, day1),  # moves to day2
        o3: (courier_b, day2),  # courier changes to A
        o4: (courier_a, day1),  # disappears entirely (dropped)
    }
    new = {
        o1: (courier_a, day1),  # unchanged
        o2: (courier_a, day2),  # same courier, different day
        o3: (courier_a, day2),  # different courier and day
        # o4 absent
    }
    row = _run(
        record_plan_stability(
            db,
            tenant_id=uuid.uuid4(),
            optimization_run_id=uuid.uuid4(),
            prior_run_id=uuid.uuid4(),
            prior_assignments=prior,
            new_assignments=new,
        )
    )
    assert row is not None
    assert row.orders_evaluated == 4
    assert row.orders_unchanged == 1            # only o1 (same courier + same day)
    assert row.orders_same_courier == 2         # o1, o2 both still on A
    assert row.orders_same_day == 2             # o1 (day1) + o3 (day2)
    assert row.stability_pct == pytest.approx(1 / 4)


def test_plan_stability_perfect_stability_returns_1():
    db = _StubSession()
    o = uuid.uuid4()
    pair = (uuid.uuid4(), date(2026, 5, 13))
    row = _run(
        record_plan_stability(
            db,
            tenant_id=uuid.uuid4(),
            optimization_run_id=uuid.uuid4(),
            prior_run_id=uuid.uuid4(),
            prior_assignments={o: pair},
            new_assignments={o: pair},
        )
    )
    assert row.stability_pct == 1.0
    assert row.orders_unchanged == 1


# ── eta_prediction_log_service.record_planner_etas ───────────────────────


def test_record_planner_etas_only_writes_delivery_stops():
    from app.services.eta_prediction_log_service import record_planner_etas

    db = _StubSession()
    now = datetime(2026, 5, 13, 8, 0, 0)
    delivery_stop = SimpleNamespace(
        id=uuid.uuid4(),
        stop_type=SimpleNamespace(value="DELIVERY"),
        planned_arrival=now + timedelta(hours=2),
    )
    pickup_stop = SimpleNamespace(
        id=uuid.uuid4(),
        stop_type=SimpleNamespace(value="PICKUP"),
        planned_arrival=now + timedelta(minutes=30),
    )
    count = _run(
        record_planner_etas(
            db,
            tenant_id=uuid.uuid4(),
            optimization_run_id=uuid.uuid4(),
            stops=[delivery_stop, pickup_stop],
        )
    )
    # Only the delivery stop was logged.
    assert count == 1
    assert len(db.added) == 1
    written = db.added[0]
    assert written.stop_id == delivery_stop.id
    assert written.prediction_horizon_s is not None


# ── dispatch_range_service integration guards ────────────────────────────


def test_dispatch_range_invokes_layer2b_services():
    """All three Layer 2b touchpoints must reach `dispatch_range`."""
    from app.services import dispatch_range_service

    src = inspect.getsource(dispatch_range_service.dispatch_range)
    assert "persist_replay" in src
    assert "record_planner_etas" in src
    assert "_record_range_plan_stability" in src


def test_apply_stop_status_transition_reconciles_eta_log():
    """Hook in lifecycle_history_service must invoke `reconcile_arrival`
    when a stop transitions to ARRIVED/COMPLETED with a known
    actual_arrival."""
    from app.services import lifecycle_history_service

    src = inspect.getsource(
        lifecycle_history_service.apply_stop_status_transition
    )
    assert "reconcile_arrival" in src
