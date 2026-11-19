"""Seed demo route-scenario comparisons so the Insights → Optimization gains
panel has data to show.

Each generated `RouteScenarioComparison` has:
  - one `human_dispatcher_baseline` scenario (the "human dispatcher" run), and
  - one optimised scenario (one of `rich_alns_service_first`,
    `rich_alns_fuel_aware`, `rich_alns_efficient`), set as `selected_scenario_id`.

The summary numbers are *synthetic but realistic* (the ALNS scenario always
beats the baseline by ~15–30%) so the resulting deltas drive the Insights
charts. This script does NOT run the actual solver — it inserts fake-but-
sensible JSONB summaries directly, which is what we want for a fast demo
populate.

Usage (with backend venv active):
    cd backend && python -m app.seed_optimization_savings_demo [--force] [--days N]

`--force` wipes existing demo comparisons (those with `task_id == 'demo_savings'`)
before re-seeding.  Default day count is 7.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.models.vehicle_tag  # noqa: F401  # register association tables
from app.database import async_session
from app.models.route_scenario import RouteScenario, RouteScenarioComparison
from app.seed_helpers import ensure_default_tenant

DEMO_TASK_TAG = "demo_savings"

RNG_SEED = 7
rng = random.Random(RNG_SEED)

# The optimised scenarios the demo cycles through. Keys match
# `route_scenario_service.SCENARIO_DEFINITIONS`.
OPTIMISED_SCENARIOS = [
    {
        "key": "rich_alns_service_first",
        "label": "ALNS · service-first",
        "routing_objective": "SERVICE_FIRST",
        "fleet_utilization": "FULL_FLEET",
        "solver_kind": "rich_alns",
    },
    {
        "key": "rich_alns_fuel_aware",
        "label": "ALNS · fuel-aware",
        "routing_objective": "FUEL_AWARE",
        "fleet_utilization": "FULL_FLEET",
        "solver_kind": "rich_alns",
    },
    {
        "key": "rich_alns_efficient",
        "label": "ALNS · efficient fleet",
        "routing_objective": "FUEL_AWARE",
        "fleet_utilization": "EFFICIENT",
        "solver_kind": "rich_alns",
    },
]


def _baseline_summary() -> dict:
    """Build a realistic 'human dispatcher' day summary.

    Quantities are anchored to ~50 deliveries / 5–7 routes so the resulting
    distance/duration/cost numbers look plausible for a single warehouse.
    """
    distance = rng.uniform(180.0, 280.0)
    duration = rng.uniform(450.0, 650.0)
    on_time_rate = rng.uniform(0.62, 0.78)
    late_minutes = int(rng.uniform(60, 180))
    fuel = distance * rng.uniform(0.12, 0.16)  # L/km in the human-dispatcher range
    energy_cost = fuel * rng.uniform(40.0, 48.0)  # TRY/L diesel
    co2 = fuel * 2.31  # kg CO₂ per L diesel (well-mixed average)
    return {
        "distance_km": round(distance, 2),
        "duration_minutes": round(duration, 1),
        "on_time_rate": round(on_time_rate, 4),
        "assigned_count": rng.randint(45, 60),
        "late_minutes": late_minutes,
        "realized_energy_cost_try": round(energy_cost, 2),
        "fuel_liters": round(fuel, 2),
        "electric_kwh": 0.0,
        "co2_kg": round(co2, 2),
        "route_count": rng.randint(5, 7),
    }


def _optimised_summary(baseline: dict, scenario_key: str) -> dict:
    """Optimised scenarios always improve on the baseline by 12–28% — this is
    what makes the "Optimization gains" panel light up. We add small per-
    objective tilts so the three scenarios feel different (fuel-aware saves
    more fuel/CO₂, service-first improves on-time, efficient saves routes).
    """
    dist_factor = rng.uniform(0.72, 0.88)
    dur_factor = rng.uniform(0.74, 0.88)
    fuel_extra = 0.95 if scenario_key.endswith("fuel_aware") or scenario_key.endswith("efficient") else 1.0
    on_time_bump = rng.uniform(0.12, 0.22)
    if scenario_key.endswith("service_first"):
        on_time_bump += 0.05
    route_save = 1 if scenario_key.endswith("efficient") and baseline["route_count"] > 4 else 0

    distance = baseline["distance_km"] * dist_factor
    duration = baseline["duration_minutes"] * dur_factor
    fuel = distance * rng.uniform(0.10, 0.13) * fuel_extra
    energy_cost = fuel * rng.uniform(40.0, 48.0)
    co2 = fuel * 2.31
    on_time = min(0.99, baseline["on_time_rate"] + on_time_bump)
    late_minutes = max(10, int(baseline["late_minutes"] * rng.uniform(0.18, 0.45)))

    return {
        "distance_km": round(distance, 2),
        "duration_minutes": round(duration, 1),
        "on_time_rate": round(on_time, 4),
        "assigned_count": baseline["assigned_count"],
        "late_minutes": late_minutes,
        "realized_energy_cost_try": round(energy_cost, 2),
        "fuel_liters": round(fuel, 2),
        "electric_kwh": 0.0,
        "co2_kg": round(co2, 2),
        "route_count": max(1, baseline["route_count"] - route_save),
    }


async def _wipe_existing(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Drop comparisons we previously seeded (matched by `task_id`) so a
    re-run with `--force` doesn't pile up duplicates."""
    result = await db.execute(
        select(RouteScenarioComparison.id).where(
            RouteScenarioComparison.tenant_id == tenant_id,
            RouteScenarioComparison.task_id == DEMO_TASK_TAG,
        )
    )
    ids = [row[0] for row in result.all()]
    if not ids:
        return 0
    await db.execute(
        delete(RouteScenarioComparison).where(RouteScenarioComparison.id.in_(ids))
    )
    await db.commit()
    return len(ids)


async def _seed_day(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    target_date: date,
) -> None:
    baseline = _baseline_summary()
    optimised_def = rng.choice(OPTIMISED_SCENARIOS)
    optimised = _optimised_summary(baseline, optimised_def["key"])

    now = datetime.combine(target_date, datetime.min.time()) + timedelta(hours=8)
    comparison = RouteScenarioComparison(
        tenant_id=tenant_id,
        target_date=target_date,
        status="READY",
        task_id=DEMO_TASK_TAG,
        created_at=now,
        completed_at=now + timedelta(minutes=2),
    )

    baseline_scenario = RouteScenario(
        tenant_id=tenant_id,
        key="human_dispatcher_baseline",
        label="Human dispatcher baseline",
        routing_objective="SERVICE_FIRST",
        fleet_utilization="FULL_FLEET",
        solver_kind="human_dispatcher",
        status="COMPLETED",
        summary=baseline,
        created_at=now,
        completed_at=now + timedelta(minutes=1),
    )
    optimised_scenario = RouteScenario(
        tenant_id=tenant_id,
        key=optimised_def["key"],
        label=optimised_def["label"],
        routing_objective=optimised_def["routing_objective"],
        fleet_utilization=optimised_def["fleet_utilization"],
        solver_kind=optimised_def["solver_kind"],
        status="COMPLETED",
        summary=optimised,
        created_at=now,
        completed_at=now + timedelta(minutes=2),
    )
    comparison.scenarios = [baseline_scenario, optimised_scenario]
    db.add(comparison)
    await db.flush()  # populates IDs so we can set selected_scenario_id

    comparison.selected_scenario_id = optimised_scenario.id
    comparison.applied_at = now + timedelta(minutes=3)


async def main(days: int = 7, force: bool = False) -> int:
    today = date.today()
    seeded_dates = [today - timedelta(days=i) for i in range(days)]

    async with async_session() as db:
        tenant = await ensure_default_tenant(db)

        if force:
            removed = await _wipe_existing(db, tenant.id)
            print(f"[seed_optimization_savings_demo] removed {removed} existing demo comparisons")

        existing = await db.execute(
            select(RouteScenarioComparison.target_date).where(
                RouteScenarioComparison.tenant_id == tenant.id,
                RouteScenarioComparison.task_id == DEMO_TASK_TAG,
                RouteScenarioComparison.target_date.in_(seeded_dates),
            )
        )
        already = {row[0] for row in existing.all()}

        seeded = 0
        for d in seeded_dates:
            if d in already:
                continue
            await _seed_day(db, tenant.id, d)
            seeded += 1

        await db.commit()
        print(
            f"[seed_optimization_savings_demo] seeded {seeded} new comparisons "
            f"({len(already)} already present, range {seeded_dates[-1]} → {seeded_dates[0]})"
        )
        return seeded


def cli() -> None:
    parser = argparse.ArgumentParser(description="Seed demo route-scenario comparisons")
    parser.add_argument("--days", type=int, default=7, help="Number of past days to seed (default 7)")
    parser.add_argument("--force", action="store_true", help="Wipe existing demo comparisons before seeding")
    args = parser.parse_args()
    seeded = asyncio.run(main(days=args.days, force=args.force))
    sys.exit(0 if seeded >= 0 else 1)


if __name__ == "__main__":
    cli()
