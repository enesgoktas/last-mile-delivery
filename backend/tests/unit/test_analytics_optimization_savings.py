"""Unit tests for `analytics_service.get_optimization_savings`.

Builds lightweight stand-ins for `RouteScenarioComparison` / `RouteScenario`
(plain `SimpleNamespace`s with the attribute surface the service touches)
plus a fake `AsyncSession` that returns them, and asserts that the savings
rollup matches what we'd expect mathematically.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.services import analytics_service
from app.services.route_scenario_service import HUMAN_DISPATCHER_BASELINE_KEY


def _scenario(*, key: str, summary: dict, scenario_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=scenario_id or uuid.uuid4(), key=key, summary=summary)


def _comparison(*, target_date: date, scenarios: list[SimpleNamespace], selected_idx: int | None) -> SimpleNamespace:
    selected_id = scenarios[selected_idx].id if selected_idx is not None else None
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        target_date=target_date,
        scenarios=scenarios,
        selected_scenario_id=selected_id,
        created_at=datetime.combine(target_date, datetime.min.time()),
    )


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeDB:
    def __init__(self, comparisons):
        self._comparisons = comparisons

    async def execute(self, _query):
        return _FakeResult(self._comparisons)


def _principal():
    return SimpleNamespace(tenant_id=uuid.uuid4())


def _run(coro):
    return asyncio.run(coro)


def test_savings_rollup_diffs_baseline_against_selected_scenario():
    """Two comparisons, both with an optimized winner: totals & per-day series
    should match the hand-computed deltas; solver mix should count winners."""
    baseline_day1 = _scenario(
        key=HUMAN_DISPATCHER_BASELINE_KEY,
        summary={
            "distance_km": 200.0,
            "duration_minutes": 500.0,
            "on_time_rate": 0.70,
            "assigned_count": 50,
            "late_minutes": 120,
            "realized_energy_cost_try": 300.0,
            "fuel_liters": 25.0,
            "co2_kg": 60.0,
            "route_count": 6,
        },
    )
    selected_day1 = _scenario(
        key="rich_alns_service_first",
        summary={
            "distance_km": 150.0,
            "duration_minutes": 400.0,
            "on_time_rate": 0.92,
            "assigned_count": 50,
            "late_minutes": 30,
            "realized_energy_cost_try": 220.0,
            "fuel_liters": 18.0,
            "co2_kg": 45.0,
            "route_count": 5,
        },
    )
    comparison1 = _comparison(
        target_date=date(2026, 5, 9),
        scenarios=[baseline_day1, selected_day1],
        selected_idx=1,
    )

    baseline_day2 = _scenario(
        key=HUMAN_DISPATCHER_BASELINE_KEY,
        summary={
            "distance_km": 100.0,
            "duration_minutes": 250.0,
            "on_time_rate": 0.60,
            "assigned_count": 25,
            "late_minutes": 80,
            "realized_energy_cost_try": 150.0,
            "fuel_liters": 12.0,
            "co2_kg": 30.0,
            "route_count": 4,
        },
    )
    selected_day2 = _scenario(
        key="rich_alns_fuel_aware",
        summary={
            "distance_km": 80.0,
            "duration_minutes": 220.0,
            "on_time_rate": 0.88,
            "assigned_count": 25,
            "late_minutes": 25,
            "realized_energy_cost_try": 110.0,
            "fuel_liters": 9.0,
            "co2_kg": 22.0,
            "route_count": 3,
        },
    )
    comparison2 = _comparison(
        target_date=date(2026, 5, 10),
        scenarios=[baseline_day2, selected_day2],
        selected_idx=1,
    )

    db = _FakeDB([comparison1, comparison2])
    result = _run(analytics_service.get_optimization_savings(db, _principal()))

    assert result["comparisons_count"] == 2
    totals = result["totals"]
    # 200 + 100 = 300 baseline km; 150 + 80 = 230 optimised km → 70 km saved.
    assert totals["baseline_distance_km"] == 300.0
    assert totals["optimized_distance_km"] == 230.0
    assert totals["distance_saved_km"] == 70.0
    assert totals["distance_saved_pct"] == pytest.approx(70.0 / 300.0, rel=1e-3)
    # 500 + 250 = 750 baseline min; 400 + 220 = 620 optimised min → 130 saved.
    assert totals["duration_saved_min"] == 130.0
    # Weighted on-time: baseline = (0.70*50 + 0.60*25) / 75 = 0.667; optimised
    # = (0.92*50 + 0.88*25) / 75 = 0.907; delta ≈ 0.240.
    assert totals["baseline_on_time_rate"] == pytest.approx(0.6667, abs=1e-3)
    assert totals["optimized_on_time_rate"] == pytest.approx(0.9067, abs=1e-3)
    assert totals["on_time_rate_delta"] == pytest.approx(0.2400, abs=1e-3)
    # 120 + 80 = 200 baseline late min; 30 + 25 = 55 optimised → 145 saved.
    assert totals["late_minutes_saved"] == 145
    # 300 + 150 - (220 + 110) = 120 TRY energy saved.
    assert totals["energy_cost_saved_try"] == 120.0
    # 25 + 12 - (18 + 9) = 10 L fuel saved.
    assert totals["fuel_saved_liters"] == 10.0
    # 60 + 30 - (45 + 22) = 23 kg CO₂ saved.
    assert totals["co2_saved_kg"] == 23.0
    # 6 + 4 - (5 + 3) = 2 routes saved.
    assert totals["routes_saved"] == 2

    assert [d["date"] for d in result["per_day"]] == ["2026-05-09", "2026-05-10"]
    # Solver mix: one win each.
    assert result["solver_mix"] == [
        {"scenario_key": "rich_alns_service_first", "count": 1},
        {"scenario_key": "rich_alns_fuel_aware", "count": 1},
    ]


def test_savings_skips_comparisons_where_baseline_was_selected():
    """If the operator manually keeps the baseline, there are no savings to
    claim — that comparison must not bias the totals."""
    baseline = _scenario(
        key=HUMAN_DISPATCHER_BASELINE_KEY,
        summary={
            "distance_km": 100.0,
            "duration_minutes": 200.0,
            "on_time_rate": 0.5,
            "assigned_count": 10,
            "late_minutes": 50,
            "realized_energy_cost_try": 80.0,
            "fuel_liters": 8.0,
            "co2_kg": 20.0,
            "route_count": 3,
        },
    )
    optimised = _scenario(
        key="rich_alns_service_first",
        summary={"distance_km": 60.0, "assigned_count": 10},
    )
    # The operator selected the BASELINE, not the optimised one.
    comparison = _comparison(
        target_date=date(2026, 5, 9),
        scenarios=[baseline, optimised],
        selected_idx=0,
    )
    db = _FakeDB([comparison])
    result = _run(analytics_service.get_optimization_savings(db, _principal()))

    assert result["comparisons_count"] == 0
    assert result["totals"]["distance_saved_km"] == 0.0
    assert result["per_day"] == []
    assert result["solver_mix"] == []


def test_savings_skips_comparisons_missing_baseline_summary():
    """A comparison without a baseline scenario (or one with an empty
    summary) can't produce a delta — gracefully drop it."""
    optimised_only = _scenario(
        key="rich_alns_service_first",
        summary={"distance_km": 60.0, "assigned_count": 10},
    )
    comparison = _comparison(
        target_date=date(2026, 5, 9),
        scenarios=[optimised_only],
        selected_idx=0,
    )
    db = _FakeDB([comparison])
    result = _run(analytics_service.get_optimization_savings(db, _principal()))

    assert result["comparisons_count"] == 0
    assert result["per_day"] == []
