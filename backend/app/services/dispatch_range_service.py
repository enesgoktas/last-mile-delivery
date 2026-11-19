"""Range dispatch — solve any date range as one VRP (horizon-agnostic).

`dispatch_batch` operates on **single-day** time semantics: every order's
`(earliest_delivery, latest_delivery)` is collapsed to *seconds-from-midnight*
of one calendar day. That's correct for the daily flow but rules out the
useful case where the customer doesn't care which day in the range the order
arrives — only that it's delivered before `latest_delivery`.

`dispatch_range` instead expresses time in **seconds-since-the-range-anchor**
so the solver can pick *which day* a courier serves an order. We expand each
courier into one virtual vehicle per day in the range, with that day's shift
expressed in range-seconds, and let the solver pick the best (courier, day)
combination per order while respecting:

  * `earliest_delivery` / `latest_delivery` (now stamped against the range,
    not the day, so an order due 2 days in at 17:00 simply has `latest_time_s =
    2*86_400 + 17*3600`).
  * Capacity (weight, volume, package count) per (courier, day).
  * Allowed vehicle profiles (truck/motorcycle/etc.).
  * Order priorities and operations-policy penalties.

Outputs are persisted as standard `Route` rows — one per (courier, day) that
has at least one stop — so the existing UI / reporting / SLA stack works
unchanged.

The planner is horizon-agnostic: 3-day, 30-day, 90-day ranges all flow through
the same pipeline. Beyond a soft threshold (see `range_chunking.py`) we fall
back to a rolling-window chunked solve rather than rejecting the request.

This module is intentionally separated from `dispatch_service.py` (the daily
flow) so a bug in the range path can never break Assign-Optimal.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.algorithms import osrm_client
from app.algorithms.constraint_diagnostics import diagnose_unassigned
from app.algorithms.solver_adapter import SolveOptions
from app.algorithms.solver_registry import get_solver
from app.algorithms.vrp_solver import (
    OrderData,
    VehicleData,
    VRPSolution,
    build_vrp_input,
)
from app.database import get_async_session_factory
from app.models.courier import Courier
from app.models.depot import Depot
from app.models.enums import (
    AvailabilityStatus,
    DispatchDecisionKind,
    OrderStatus,
    OrderType,
    RouteStatus,
    StopStatus,
    StopType,
)
from app.models.order import Order
from app.models.order_history import OrderStatusHistory
from app.models.route import Route, RouteStop
from app.models.sla_alert import SLAAlert
from app.services import operations_policy_service, traffic_profile_service
from app.services.counterfactual_service import run_and_persist_counterfactual
from app.services.dispatch_decision_log_service import (
    record_decisions_for_solution,
)
from app.services.dispatch_vrp_helpers import (
    build_location_index,
    build_vrp_data_structures,
    load_order_package_summaries,
)
from app.services.eta_prediction_log_service import record_planner_etas
from app.services.optimization_metrics_promotion import promote_solver_metrics
from app.services.plan_stability_service import record_plan_stability
from app.services.replay_journal_service import persist_replay
from app.services.route_performance_snapshot_service import (
    materialize_route_snapshot,
)
from app.services.order_service import apply_order_status_transition
from app.services.vehicle_routing_profile import resolve_osrm_profile_from_values
from app.utils.compat import UTC
from app.utils.tenant_context import tenant_filter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Horizon safety nets — these are obvious-typo guards, not quality caps.
# Beyond `MAX_RANGE_SPAN_DAYS` we genuinely can't produce a useful plan; under
# it the chunking orchestrator (range_chunking.py) handles the large tier.
# `ORDER_QUERY_LIMIT` is the hard upper bound on pending orders pulled per
# run; tenants with more than this in a single range get a structured warning
# logged so operations can react.
# ---------------------------------------------------------------------------
MAX_RANGE_SPAN_DAYS = 180
ORDER_QUERY_LIMIT = 10_000


# ---------------------------------------------------------------------------
# Time helpers — range-relative seconds
# ---------------------------------------------------------------------------


def datetime_to_range_seconds(dt: datetime, anchor: datetime) -> int:
    """Return seconds between `anchor` and `dt`, clamped to >= 0."""
    delta = (dt - anchor).total_seconds()
    if delta < 0:
        return 0
    return int(delta)


def range_seconds_to_datetime(seconds: int, anchor: datetime) -> datetime:
    return anchor + timedelta(seconds=int(seconds))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def dispatch_range(
    *,
    tenant_id: uuid.UUID,
    date_from: datetime,
    date_to: datetime,
    optimization_run_id: uuid.UUID | None = None,
    draft: bool = False,
    departure_time: datetime | None = None,
    _skip_chunking: bool = False,
    _backfill_mode: bool = False,
) -> dict:
    """Run a multi-day VRP over `[date_from, date_to)`.

    Inputs:
      * `tenant_id` — required, scopes orders & couriers.
      * `date_from`, `date_to` — naive UTC datetimes; we treat them as
        midnight-aligned bounds. Range must span at least one day and at
        most `MAX_RANGE_SPAN_DAYS` (180 by default — sanity guard only,
        not a quality cap).
      * `_skip_chunking` — internal escape hatch used by
        `range_chunking.solve_in_chunks` so its inner-loop calls don't
        re-tier and recurse forever.
      * `_backfill_mode` — internal escape hatch for one-shot historical
        backfills. Bypasses the `Order.latest_delivery > now_naive`
        filter so orders whose windows have closed in the past are still
        eligible. Production calls must leave this False; it would
        otherwise re-dispatch orders whose SLA has already lapsed.

    Returns the same shape as `dispatch_batch` for UI parity. For chunked
    runs (`span_days > LARGE_TIER_SPAN_THRESHOLD`), the response carries
    `chunked=True` plus per-chunk telemetry under `per_chunk`.
    """
    span_days = (date_to.date() - date_from.date()).days
    if not _skip_chunking and span_days > 0:
        # Lazy import to avoid a circular reference with range_chunking,
        # which itself imports `dispatch_range`.
        from app.services.range_chunking import is_large_tier, solve_in_chunks

        if is_large_tier(span_days):
            return await solve_in_chunks(
                tenant_id=tenant_id,
                date_from=date_from,
                date_to=date_to,
                optimization_run_id=optimization_run_id,
                draft=draft,
                departure_time=departure_time,
            )

    started = time.monotonic()

    if date_to <= date_from:
        raise ValueError("date_to must be strictly after date_from")
    span_days = (date_to.date() - date_from.date()).days
    if span_days < 1:
        raise ValueError("dispatch_range requires at least one full day")
    if span_days > MAX_RANGE_SPAN_DAYS:
        raise ValueError(
            f"dispatch_range span exceeds the {MAX_RANGE_SPAN_DAYS}-day safety net "
            f"(requested {span_days} days). Split the request into smaller windows."
        )

    if not await osrm_client.check_osrm_health():
        logger.error("Range dispatch aborted: OSRM is unreachable")
        return _result(
            started,
            {
                "routes_created": 0,
                "unassigned_count": 0,
                "unassigned_order_ids": [],
                "solver_status": "OSRM_UNAVAILABLE",
                "scope": "RANGE",
            },
        )

    anchor = date_from.replace(hour=0, minute=0, second=0, microsecond=0)

    async with get_async_session_factory()() as db:
        now_naive = datetime.now(UTC).replace(tzinfo=None)
        # Capture prior (order → courier, date) assignments BEFORE the
        # reset so Layer 2b's plan-stability score can compare. Empty
        # map on a first run; harmless to feed the stability service.
        prior_assignments, prior_run_id = await _snapshot_prior_assignments(
            db, tenant_id=tenant_id, date_from=date_from, date_to=date_to
        )
        await _reset_planned_assignments_for_range(
            db,
            tenant_id=tenant_id,
            date_from=date_from,
            date_to=date_to,
        )
        order_filters = [
            Order.status == OrderStatus.PENDING,
            Order.earliest_delivery < date_to,
            Order.latest_delivery >= date_from,
            tenant_filter(Order, tenant_id),
        ]
        if not _backfill_mode:
            # Production: skip orders whose SLA window already lapsed.
            # Backfill: include them so we can re-assign historical data.
            order_filters.append(Order.latest_delivery > now_naive)
        orders = list(
            (await db.execute(
                select(Order).where(*order_filters).limit(ORDER_QUERY_LIMIT)
            )).scalars().all()
        )
        if len(orders) >= ORDER_QUERY_LIMIT:
            # If we hit the limit there are almost certainly more pending
            # orders that didn't make it into this run. Surface as a
            # structured warning so operations can split the request or
            # raise the constant.
            logger.warning(
                "Range dispatch order query truncated at ORDER_QUERY_LIMIT=%d "
                "for tenant %s span_days=%d; some pending orders will not be "
                "considered. Recommend splitting the date range or raising "
                "the limit.",
                ORDER_QUERY_LIMIT,
                tenant_id,
                span_days,
            )
        if not orders:
            return _result(
                started,
                {
                    "routes_created": 0,
                    "unassigned_count": 0,
                    "unassigned_order_ids": [],
                    "solver_status": "NO_ORDERS",
                    "scope": "RANGE",
                    "span_days": span_days,
                },
            )

        couriers = list(
            (await db.execute(
                select(Courier)
                .where(
                    Courier.availability_status.in_(
                        [AvailabilityStatus.ONLINE, AvailabilityStatus.ON_BREAK]
                    ),
                    tenant_filter(Courier, tenant_id),
                )
                .options(selectinload(Courier.vehicle_spec))
            )).scalars().all()
        )
        if not couriers:
            logger.warning("Range dispatch: no couriers online for tenant %s", tenant_id)
            return _result(
                started,
                {
                    "routes_created": 0,
                    "unassigned_count": len(orders),
                    "unassigned_order_ids": [str(o.id) for o in orders],
                    "solver_status": "NO_COURIERS",
                    "scope": "RANGE",
                    "span_days": span_days,
                },
            )

        # ------------------------------------------------------------------
        # Build location index + OSRM matrices (one per profile, range-wide)
        # ------------------------------------------------------------------
        # Pull operations_policy up here (the downstream code below also
        # reads it) so we can gate the multi-depot location additions on
        # the tenant flag before we build the OSRM matrix.
        operations_policy = await operations_policy_service.get_operations_policy(
            db, tenant_id
        )
        locations, loc_index = build_location_index(
            orders,
            couriers,
            multi_depot_enabled=bool(
                operations_policy.feature_flags.multi_depot_vehicles_enabled
            ),
        )
        osrm_profiles = sorted(_resolve_courier_profiles(couriers))
        matrix_by_profile = {
            profile: await osrm_client.get_distance_matrix(locations, profile=profile)
            for profile in osrm_profiles
        }
        primary_matrix = matrix_by_profile.get("driving") or next(
            iter(matrix_by_profile.values())
        )
        target_time = departure_time or anchor
        adjusted_by_profile = {
            profile: await traffic_profile_service.adjust_duration_matrix(
                db,
                coords=locations,
                durations=mtx.durations,
                at_time=target_time,
            )
            for profile, mtx in matrix_by_profile.items()
        }
        primary_durations = (
            adjusted_by_profile.get("driving")
            or next(iter(adjusted_by_profile.values()))
        ).durations

        # ------------------------------------------------------------------
        # Build day-aware orders + (courier × day) virtual vehicles
        # ------------------------------------------------------------------
        from app.services.constraint_service import get_global_constraints

        global_constraints = await get_global_constraints(db, tenant_id)
        # operations_policy already loaded above (before build_location_index)
        # so the multi-depot flag could gate the OSRM matrix size.
        order_packages = await load_order_package_summaries(
            db, [o.id for o in orders], tenant_id=tenant_id
        )
        primary_optimization_settings = (
            operations_policy.optimization
            if operations_policy.optimization.economic_batch_optimizer_enabled
            else None
        )

        # Reuse the daily helper for the heavy mapping (priority weights,
        # vehicle costs, OSRM profile, etc.) and *override* the time fields.
        # `operations_policy` is required so multi-depot tenants get their
        # `additional_depots` / `end_depot_id` reflected on the resulting
        # VehicleData (Phase 2 multi-depot plumbing). Without this argument
        # the helper falls back to single-depot vehicles and the range path
        # silently ignores the tenant's multi-depot configuration.
        base_orders, base_vehicles = build_vrp_data_structures(
            orders,
            couriers,
            loc_index,
            global_constraints,
            order_packages,
            primary_optimization_settings,
            operations_policy=operations_policy,
        )

        vrp_orders = _re_anchor_orders(orders, base_orders, anchor=anchor, span_days=span_days)
        vrp_vehicles = _expand_couriers_per_day(
            base_vehicles, span_days=span_days, anchor=anchor
        )

        # ------------------------------------------------------------------
        # Build VRP input (per-profile distance + duration matrices passed
        # through unchanged — the solver chooses based on osrm_profile).
        # ------------------------------------------------------------------
        vrp_input = build_vrp_input(
            locations=locations,
            distance_matrix=primary_matrix.distances,
            duration_matrix=primary_durations,
            orders=vrp_orders,
            vehicles=vrp_vehicles,
            distance_matrices={
                profile: mtx.distances for profile, mtx in matrix_by_profile.items()
            },
            duration_matrices={
                profile: adj.durations
                for profile, adj in adjusted_by_profile.items()
            },
        )

        # ------------------------------------------------------------------
        # Solve via Rich ALNS. The daily flow has been on this adapter since
        # M9; weekly/range was the holdout. Cross-tenant: SAME adapter, same
        # `SolveOptions` shape — only the time-window encoding differs
        # (range-seconds-since-anchor instead of seconds-since-midnight),
        # which the kernel handles natively (64-bit time arithmetic).
        #
        # `initial_routes` warm-start is intentionally omitted in v1: the
        # warm_start_cache key is shaped for daily problems and cross-
        # horizon reuse risks malformed vnodes. The Phase B-2 chunked path
        # passes its own intra-run warm-start seed between chunks.
        # ------------------------------------------------------------------
        time_limit = _resolve_range_time_limit(len(vrp_orders), span_days)
        logger.info(
            "Range dispatch starting: %d orders × %d days × %d couriers (limit %ds)",
            len(vrp_orders),
            span_days,
            len(couriers),
            time_limit,
        )
        adapter = get_solver("rich_alns")
        solve_result = await asyncio.to_thread(
            adapter.solve,
            vrp_input,
            SolveOptions(
                time_budget_s=float(time_limit),
                spread_fleet=False,
            ),
        )
        solution: VRPSolution = solve_result.solution
        adapter_metrics = solve_result.metrics or {}
        adapter_elapsed_s = solve_result.elapsed_s
        logger.info(
            "Range dispatch ALNS finished in %.2fs: status=%s, "
            "iterations=%s, feasibility_ratio=%s, fallback=%s",
            adapter_elapsed_s,
            solution.solver_status.value,
            adapter_metrics.get("total_iterations"),
            adapter_metrics.get("feasibility_ratio"),
            adapter_metrics.get("fallback_invoked"),
        )

        # ------------------------------------------------------------------
        # Persist — group routes by (courier, day) inferred from each route's
        # first stop arrival time.
        # ------------------------------------------------------------------
        run_id = optimization_run_id or uuid.uuid4()
        warehouse_locs = await _load_warehouse_locs(db, tenant_id)
        routes_created, persisted_orders = await _persist_range_solution(
            db,
            solution,
            orders,
            run_id=run_id,
            anchor=anchor,
            draft=draft,
            warehouse_locs=warehouse_locs,
        )

        # Capture dispatch decisions for the forensics dashboard before
        # commit. Non-fatal: a decision-log write failure must never block
        # the actual route persistence.
        try:
            await _record_range_dispatch_decisions(
                db,
                solution=solution,
                orders=orders,
                run_id=run_id,
            )
        except Exception:
            logger.exception(
                "Range dispatch: decision log capture failed for run %s",
                run_id,
            )

        # Layer 2a — promote solver metrics + run counterfactual baseline.
        # Both are non-fatal: failures must never block the real run's
        # commit, and the dashboards degrade gracefully when these
        # columns are NULL.
        try:
            await promote_solver_metrics(
                db,
                run_id=run_id,
                solver_kind="rich_alns",
                metrics=adapter_metrics,
                time_budget_allowed_s=float(time_limit),
                time_budget_used_s=adapter_elapsed_s,
            )
        except Exception:
            logger.exception(
                "Range dispatch: solver metrics promotion failed for run %s",
                run_id,
            )

        # Materialize per-route snapshots from the planned routes (the
        # daily flow does this at route-completion time via
        # `_maybe_complete_route`; range commits routes as PLANNED
        # without an immediate completion event, so we materialize on
        # commit here. Snapshots are idempotent on (route_id) so a later
        # auto-completion hook is a no-op).
        persisted_route_ids: list[uuid.UUID] = []
        try:
            from app.models.route import Route as _Route

            persisted_route_ids = list(
                (
                    await db.execute(
                        select(_Route.id).where(
                            _Route.optimization_run_id == run_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for rid in persisted_route_ids:
                await materialize_route_snapshot(db, rid)
        except Exception:
            logger.exception(
                "Range dispatch: route snapshot materialization failed for run %s",
                run_id,
            )

        try:
            await run_and_persist_counterfactual(
                db,
                vrp_input=vrp_input,
                tenant_id=tenant_id,
                optimization_run_id=run_id,
                real_solution=solution,
                operational_date=anchor.date(),
            )
        except Exception:
            logger.exception(
                "Range dispatch: counterfactual baseline failed for run %s",
                run_id,
            )

        # Layer 2b — replay journal, ETA log, plan stability. All
        # non-fatal: each is wrapped so a failure logs and continues
        # without blocking the real commit.
        try:
            await persist_replay(
                db,
                optimization_run_id=run_id,
                tenant_id=tenant_id,
                vrp_input=vrp_input,
                adapter_version="rich_alns",
                time_budget_allowed_s=float(time_limit),
                time_budget_used_s=adapter_elapsed_s,
            )
        except Exception:
            logger.exception(
                "Range dispatch: replay journal write failed for run %s",
                run_id,
            )

        try:
            from app.models.route import RouteStop as _RouteStop

            new_stops = (
                await db.execute(
                    select(_RouteStop).where(
                        _RouteStop.route_id.in_(persisted_route_ids)
                    )
                )
            ).scalars().all()
            await record_planner_etas(
                db,
                tenant_id=tenant_id,
                optimization_run_id=run_id,
                stops=new_stops,
            )
        except Exception:
            logger.exception(
                "Range dispatch: ETA log emission failed for run %s",
                run_id,
            )

        try:
            await _record_range_plan_stability(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                prior_assignments=prior_assignments,
                prior_run_id=prior_run_id,
            )
        except Exception:
            logger.exception(
                "Range dispatch: plan stability score failed for run %s",
                run_id,
            )

        await db.commit()

        # ------------------------------------------------------------------
        # Diagnostics + return
        # ------------------------------------------------------------------
        unassigned_diagnostics = []
        if solution.unassigned_orders:
            try:
                diagnostics = diagnose_unassigned(
                    vrp_input=vrp_input,
                    unassigned_order_ids=list(solution.unassigned_orders),
                )
                unassigned_diagnostics = [d.to_dict() for d in diagnostics]
            except Exception:
                logger.warning(
                    "Range dispatch: diagnose_unassigned failed", exc_info=True
                )

        return _result(
            started,
            {
                "routes_created": routes_created,
                "assigned_count": persisted_orders,
                "unassigned_count": len(solution.unassigned_orders),
                "unassigned_order_ids": list(solution.unassigned_orders),
                "solver_status": solution.solver_status.value,
                "scope": "RANGE",
                "span_days": span_days,
                "anchor_iso": anchor.isoformat(),
                "total_distance_m": solution.total_distance_m,
                "total_duration_s": solution.total_duration_s,
                "optimization_run_id": str(run_id),
                "unassigned_diagnostics": unassigned_diagnostics,
                # Surface ALNS telemetry on the run result so the optimization-
                # gains dashboard + audit logs can observe solver quality.
                "solver_kind": "rich_alns",
                "solver_elapsed_s": adapter_elapsed_s,
                "solver_metrics": adapter_metrics,
            },
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _result(started: float, payload: dict) -> dict:
    payload["dispatch_range_latency_ms"] = round((time.monotonic() - started) * 1000, 2)
    return payload


def _resolve_courier_profiles(couriers: list[Courier]) -> set[str]:
    profiles: set[str] = set()
    for courier in couriers:
        spec = getattr(courier, "vehicle_spec", None)
        profiles.add(
            resolve_osrm_profile_from_values(
                courier.vehicle_type,
                spec=spec,
                gross_weight_kg=round(courier.max_weight_kg),
            )
        )
    return profiles or {"driving"}


def _re_anchor_orders(
    orders: list[Order],
    base_orders: list[OrderData],
    *,
    anchor: datetime,
    span_days: int,
) -> list[OrderData]:
    """Convert order time windows from seconds-from-midnight to seconds-from-anchor.

    `build_vrp_data_structures` already produced `OrderData` rows whose
    `earliest_time_s`/`latest_time_s` fields are seconds-from-midnight of the
    delivery day. We rewrite them with the *real* range-relative bounds, while
    keeping the priority/penalty wiring unchanged.
    """
    rebuilt: list[OrderData] = []
    by_id = {str(o.id): o for o in orders}
    range_horizon = span_days * 86_400
    for src in base_orders:
        order = by_id[src.order_id]
        earliest = max(0, datetime_to_range_seconds(order.earliest_delivery, anchor))
        latest = min(
            range_horizon,
            datetime_to_range_seconds(order.latest_delivery, anchor),
        )
        if latest <= earliest:
            # Window degenerated (latest already past or equal earliest in the
            # selected range) — clamp to a 1h delivery window so the solver
            # still has something feasible to attempt.
            latest = min(range_horizon, earliest + 3600)
        if order.earliest_pickup and order.latest_pickup:
            pickup_earliest = max(
                0, datetime_to_range_seconds(order.earliest_pickup, anchor)
            )
            pickup_latest = min(
                range_horizon,
                datetime_to_range_seconds(order.latest_pickup, anchor),
            )
            if pickup_latest <= pickup_earliest:
                pickup_latest = min(range_horizon, pickup_earliest + 3600)
        else:
            pickup_earliest = 0
            pickup_latest = latest
        rebuilt.append(
            replace(
                src,
                earliest_time_s=earliest,
                latest_time_s=latest,
                hard_latest_time_s=latest,
                pickup_earliest_time_s=pickup_earliest,
                pickup_latest_time_s=pickup_latest,
            )
        )
    return rebuilt


def _expand_couriers_per_day(
    base_vehicles: list[VehicleData],
    *,
    span_days: int,
    anchor: datetime,
) -> list[VehicleData]:
    """For each courier, emit `span_days` virtual vehicles whose shifts are
    anchored to absolute range-seconds.

    The `physical_courier_id` stays the same so we can group per-day routes
    back to one courier on persist; `courier_id` carries the day suffix.

    Multi-trip expansion (intra-day reload trips) is intentionally NOT
    applied here: range bulk planning chooses *which day* to serve an
    order; day-internal slicing is the daily flow's job and the two
    interact poorly.
    """
    expanded: list[VehicleData] = []
    for src in base_vehicles:
        # `start_time_s`/`end_time_s` from the daily build are
        # seconds-from-midnight. Use them as the per-day shift template and
        # offset by `day_index * 86_400`.
        for day_idx in range(span_days):
            offset = day_idx * 86_400
            expanded.append(
                replace(
                    src,
                    courier_id=f"{src.courier_id}__day{day_idx}",
                    start_time_s=offset + src.start_time_s,
                    end_time_s=offset + src.end_time_s,
                    hard_end_time_s=(
                        offset + src.hard_end_time_s
                        if src.hard_end_time_s is not None
                        else None
                    ),
                    # Breaks are likewise anchored to this day.
                    break_earliest_s=offset + src.break_earliest_s,
                    break_latest_s=offset + src.break_latest_s,
                    physical_courier_id=src.physical_courier_id or src.courier_id,
                    # Only the first day pays the fixed activation cost so the
                    # solver doesn't double-bill couriers across days.
                    fixed_route_cost=src.fixed_route_cost if day_idx == 0 else 0,
                    trip_index=day_idx,
                )
            )
    return expanded


# Time-budget tier table for `_resolve_range_time_limit`. Each row is
# (max_span_days, max_order_count, base_seconds, ceiling_seconds). The
# function picks the FIRST row whose thresholds are not exceeded.
#
# Small tier (3–14 days, light volume) is the dispatcher's "quick plan"
# case — kept snappy. Medium tier (up to 30 days / 1500 orders) lets
# decomposition do real work. Above the medium tier the chunked solver
# (Phase B-2) takes over; this table's ceiling is only reached for solves
# that decided not to chunk (e.g. span just-above 30 with low volume).
_RANGE_TIME_BUDGET_TIERS = [
    # (max_span, max_orders, base_s, ceiling_s)
    (14, 100, 30, 90),     # quick small-tier
    (14, 500, 60, 240),    # full small-tier
    (30, 1500, 240, 900),  # medium-tier
    (180, 10_000, 600, 1_800),  # large fallback (typically chunked)
]


def _resolve_range_time_limit(order_count: int, span_days: int) -> int:
    """Heuristic: pick a per-solve time budget from the tier table.

    Scales with both span and volume — search space is roughly
    `span_days × order_count`. Returns the tier's `base` plus a per-day
    premium clamped at the tier's `ceiling`.
    """
    for max_span, max_orders, base, ceiling in _RANGE_TIME_BUDGET_TIERS:
        if span_days <= max_span and order_count <= max_orders:
            premium = base * span_days // 4
            return min(ceiling, base + premium)
    # Above every tier — return the largest tier's ceiling.
    return _RANGE_TIME_BUDGET_TIERS[-1][-1]


async def _load_warehouse_locs(
    db: AsyncSession, tenant_id: uuid.UUID
) -> set[tuple[float, float]]:
    rows = (
        await db.execute(
            select(Depot.lat, Depot.lon).where(
                Depot.is_active.is_(True),
                Depot.tenant_id == tenant_id,
            )
        )
    ).all()
    return {(round(lat, 4), round(lon, 4)) for lat, lon in rows}


def _is_near(lat: float, lon: float, locs: set[tuple[float, float]]) -> bool:
    return (round(lat, 4), round(lon, 4)) in locs


async def _persist_range_solution(
    db: AsyncSession,
    solution: VRPSolution,
    orders: list[Order],
    *,
    run_id: uuid.UUID,
    anchor: datetime,
    draft: bool,
    warehouse_locs: set[tuple[float, float]],
) -> tuple[int, int]:
    """Persist the solver output as one Route per (courier, day).

    Each route's stop arrival/departure times are stored as absolute UTC
    datetimes (`anchor + seconds`).  Initial preload PICKUPs at warehouses
    are collapsed (same heuristic as the daily flow) so a courier loading 5
    orders shows up as one warehouse visit, not five.
    """
    if not orders:
        return 0, 0
    order_map = {str(o.id): o for o in orders}
    route_status = RouteStatus.DRAFT if draft else RouteStatus.PLANNED
    tenant_id = orders[0].tenant_id

    routes_created = 0
    orders_assigned = 0
    for planned_route in solution.routes:
        if not planned_route.stops:
            continue
        # Strip the `__day{n}` suffix to recover the physical courier id.
        physical_id, _, _ = planned_route.courier_id.partition("__day")
        route_service_date = _route_service_date(planned_route, anchor)
        try:
            courier_uuid = uuid.UUID(physical_id)
        except ValueError:
            logger.warning(
                "Range dispatch: skipping route with non-uuid courier id %s",
                planned_route.courier_id,
            )
            continue

        route = Route(
            tenant_id=tenant_id,
            courier_id=courier_uuid,
            status=route_status,
            planned_distance_km=round(planned_route.total_distance_m / 1000.0, 2),
            optimization_run_id=run_id,
        )
        db.add(route)
        await db.flush()
        routes_created += 1

        seq = 0
        route_work_started = False
        for stop in planned_route.stops:
            # Hide a leading PICKUP at any known warehouse to avoid the
            # "courier visits depot 5 times in a row" UI artefact, exactly
            # as the daily flow does in dispatch_service._should_hide....
            order = order_map.get(stop.order_id)
            is_pickup = stop.stop_type == "PICKUP"
            hide = (
                is_pickup
                and order is not None
                and order.type != OrderType.REVERSE
                and not route_work_started
                and _is_near(stop.lat, stop.lon, warehouse_locs)
            )
            if hide:
                if order and not draft and order.status == OrderStatus.PENDING:
                    order.assigned_courier_id = courier_uuid
                    order.route_id = route.id
                    order.scheduled_for_date = route_service_date
                    await apply_order_status_transition(
                        db,
                        order,
                        OrderStatus.ASSIGNED,
                        reason="Range bulk optimisation",
                    )
                    orders_assigned += 1
                continue

            db.add(
                RouteStop(
                    tenant_id=tenant_id,
                    route_id=route.id,
                    order_id=order.id if order else None,
                    sequence_number=seq,
                    stop_type=StopType(stop.stop_type),
                    lat=stop.lat,
                    lon=stop.lon,
                    address=(
                        order.delivery_address
                        if (order and stop.stop_type == "DELIVERY")
                        else (order.pickup_address if order else "Depot")
                    ),
                    planned_arrival=range_seconds_to_datetime(stop.arrival_time_s, anchor),
                    planned_departure=range_seconds_to_datetime(stop.departure_time_s, anchor),
                    status=StopStatus.PENDING,
                )
            )
            seq += 1
            route_work_started = True

            if (
                order
                and stop.stop_type == "DELIVERY"
                and not draft
                and order.status == OrderStatus.PENDING
            ):
                order.assigned_courier_id = courier_uuid
                order.route_id = route.id
                order.scheduled_for_date = route_service_date
                await apply_order_status_transition(
                    db,
                    order,
                    OrderStatus.ASSIGNED,
                    reason="Range bulk optimisation",
                )
                orders_assigned += 1

    return routes_created, orders_assigned


def _route_service_date(planned_route, anchor: datetime) -> date:
    """Return the operational date for a virtual courier-day route."""
    _physical, marker, day_suffix = planned_route.courier_id.partition("__day")
    if marker and day_suffix.isdigit():
        return (anchor + timedelta(days=int(day_suffix))).date()
    route_start = getattr(planned_route, "route_start_time_s", None)
    if route_start is not None:
        return range_seconds_to_datetime(route_start, anchor).date()
    first_stop = min(planned_route.stops, key=lambda stop: stop.arrival_time_s)
    return range_seconds_to_datetime(first_stop.arrival_time_s, anchor).date()


def _scheduled_or_window_range_filter(date_from: datetime, date_to: datetime):
    start_date = date_from.date()
    end_date = date_to.date()
    return or_(
        and_(
            Order.scheduled_for_date.is_not(None),
            Order.scheduled_for_date >= start_date,
            Order.scheduled_for_date < end_date,
        ),
        and_(
            Order.scheduled_for_date.is_(None),
            Order.earliest_delivery < date_to,
            Order.latest_delivery >= date_from,
        ),
    )


async def _reset_planned_assignments_for_range(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    date_from: datetime,
    date_to: datetime,
) -> int:
    """Clear existing planned assignments in the range before replanning.

    Only ASSIGNED orders on PLANNED/DRAFT routes are touched. Work already in
    progress or completed stays intact.
    """
    rows = (
        await db.execute(
            select(Order.id, Order.route_id)
            .join(Route, Order.route_id == Route.id)
            .where(
                Order.status == OrderStatus.ASSIGNED,
                Route.status.in_([RouteStatus.PLANNED, RouteStatus.DRAFT]),
                _scheduled_or_window_range_filter(date_from, date_to),
                tenant_filter(Order, tenant_id),
                tenant_filter(Route, tenant_id),
            )
        )
    ).all()
    if not rows:
        return 0

    order_ids = [row[0] for row in rows]
    route_ids = list({row[1] for row in rows if row[1] is not None})

    if route_ids:
        stop_ids = (
            await db.execute(
                select(RouteStop.id).where(
                    RouteStop.route_id.in_(route_ids),
                    RouteStop.order_id.in_(order_ids),
                )
            )
        ).scalars().all()
        if stop_ids:
            await db.execute(delete(SLAAlert).where(SLAAlert.stop_id.in_(stop_ids)))
            await db.execute(delete(RouteStop).where(RouteStop.id.in_(stop_ids)))

    await db.execute(
        delete(OrderStatusHistory).where(
            OrderStatusHistory.order_id.in_(order_ids),
            OrderStatusHistory.to_status == "ASSIGNED",
        )
    )
    await db.execute(
        update(Order)
        .where(Order.id.in_(order_ids))
        .values(
            status=OrderStatus.PENDING,
            assigned_courier_id=None,
            route_id=None,
            scheduled_for_date=None,
        )
    )

    for route_id in route_ids:
        has_stops = (
            await db.execute(
                select(RouteStop.id).where(RouteStop.route_id == route_id).limit(1)
            )
        ).scalar()
        if has_stops is None:
            await db.execute(delete(Route).where(Route.id == route_id))

    logger.info(
        "Range dispatch reset %d planned assignment(s) in %s..%s before replanning",
        len(order_ids),
        date_from.date(),
        date_to.date(),
    )
    return len(order_ids)


async def _snapshot_prior_assignments(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    date_from: datetime,
    date_to: datetime,
) -> tuple[dict[uuid.UUID, tuple[uuid.UUID, date]], uuid.UUID | None]:
    """Return ({order_id → (courier_id, scheduled_date)}, prior_run_id).

    Reads the state that's about to be wiped by `_reset_planned_assignments_for_range`
    so we can compute plan stability after the new solve. The prior_run_id is
    inferred from the most-recent route in the range (good enough — the
    re-run typically belongs to one optimization run).
    """
    rows = (
        await db.execute(
            select(
                Order.id,
                Order.assigned_courier_id,
                Order.scheduled_for_date,
                Route.optimization_run_id,
            )
            .join(Route, Order.route_id == Route.id)
            .where(
                Order.status == OrderStatus.ASSIGNED,
                Route.status.in_([RouteStatus.PLANNED, RouteStatus.DRAFT]),
                _scheduled_or_window_range_filter(date_from, date_to),
                tenant_filter(Order, tenant_id),
                tenant_filter(Route, tenant_id),
            )
        )
    ).all()
    assignments: dict[uuid.UUID, tuple[uuid.UUID, date]] = {}
    prior_run_id: uuid.UUID | None = None
    for order_id, courier_id, scheduled_date, run_id in rows:
        if courier_id is None or scheduled_date is None:
            continue
        assignments[order_id] = (courier_id, scheduled_date)
        prior_run_id = run_id  # last one wins; fine for v1
    return assignments, prior_run_id


async def _record_range_plan_stability(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    prior_assignments: dict[uuid.UUID, tuple[uuid.UUID, date]],
    prior_run_id: uuid.UUID | None,
) -> None:
    """Query the freshly-persisted state for this run and call
    `record_plan_stability` with both prior + new assignments."""
    new_rows = (
        await db.execute(
            select(Order.id, Order.assigned_courier_id, Order.scheduled_for_date)
            .join(Route, Order.route_id == Route.id)
            .where(
                Route.optimization_run_id == run_id,
                Order.assigned_courier_id.is_not(None),
                Order.scheduled_for_date.is_not(None),
            )
        )
    ).all()
    new_assignments: dict[uuid.UUID, tuple[uuid.UUID, date]] = {
        order_id: (courier_id, scheduled_date)
        for order_id, courier_id, scheduled_date in new_rows
        if courier_id is not None and scheduled_date is not None
    }
    await record_plan_stability(
        db,
        tenant_id=tenant_id,
        optimization_run_id=run_id,
        prior_run_id=prior_run_id,
        prior_assignments=prior_assignments,
        new_assignments=new_assignments,
    )


async def _record_range_dispatch_decisions(
    db: AsyncSession,
    *,
    solution: VRPSolution,
    orders: list[Order],
    run_id: uuid.UUID,
) -> None:
    """Capture per-order assignments produced by the range solver as
    `DispatchDecisionLog` rows. Mirror of `_record_dispatch_decisions_from_solution`
    in the daily flow, accounting for `__day{n}` courier-id suffixes."""
    if not orders:
        return
    order_map = {str(o.id): o for o in orders}
    tenant_id = orders[0].tenant_id

    assignments: list[tuple[uuid.UUID, uuid.UUID, float | None]] = []
    for planned_route in solution.routes:
        physical_id, _, _ = planned_route.courier_id.partition("__day")
        try:
            chosen_courier_id = uuid.UUID(physical_id)
        except ValueError:
            continue
        route_score = (
            float(planned_route.total_distance_m)
            if planned_route.total_distance_m
            else None
        )
        for stop in planned_route.stops:
            if not stop.order_id or stop.stop_type != "DELIVERY":
                continue
            order = order_map.get(stop.order_id)
            if order is None:
                continue
            assignments.append((order.id, chosen_courier_id, route_score))

    unassigned: list[tuple[uuid.UUID, str | None]] = []
    for raw_id in solution.unassigned_orders:
        order = order_map.get(str(raw_id))
        if order is not None:
            unassigned.append((order.id, None))

    await record_decisions_for_solution(
        db,
        tenant_id=tenant_id,
        optimization_run_id=run_id,
        kind=DispatchDecisionKind.RANGE_DISPATCH,
        assignments=assignments,
        unassigned=unassigned,
    )
