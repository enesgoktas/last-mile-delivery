import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.order import OrderResponse
from app.schemas.route import OptimizationRunResponse


class OrderStatusBreakdown(BaseModel):
    """Per-status order counts.

    Surfaced in `AnalyticsSummaryResponse` so the Insights page can render
    the full lifecycle (not just delivered / pending / failed).
    """

    DRAFT: int = 0
    PENDING: int = 0
    ASSIGNED: int = 0
    PICKED_UP: int = 0
    IN_TRANSIT: int = 0
    DELIVERED: int = 0
    FAILED: int = 0
    RESCHEDULED: int = 0
    CANCELLED: int = 0
    EXPIRED: int = 0


class AnalyticsSummaryResponse(BaseModel):
    total_orders: int
    delivered_orders: int
    failed_orders: int
    pending_orders: int
    completion_rate: float
    failed_rate: float
    on_time_rate: float
    status_breakdown: OrderStatusBreakdown = Field(default_factory=OrderStatusBreakdown)


class CourierPerformanceRow(BaseModel):
    courier_id: uuid.UUID
    assigned_orders: int
    delivered_orders: int
    failed_orders: int


class CourierAnalyticsTableResponse(BaseModel):
    rows: list[CourierPerformanceRow]


class MetricSeriesSummary(BaseModel):
    count: int
    p50_ms: float | None = None
    p95_ms: float | None = None
    avg_ms: float | None = None
    median_ms: float | None = None


class RuntimePerformanceSnapshotResponse(BaseModel):
    dispatch_batch_latency_ms: MetricSeriesSummary
    dispatch_on_demand_latency_ms: MetricSeriesSummary
    eta_compute_latency_ms: MetricSeriesSummary
    eta_error_delta_ms: MetricSeriesSummary
    osrm: dict


class ExceptionAlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    route_id: uuid.UUID
    stop_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    courier_id: uuid.UUID
    severity: str
    status: str
    alert_type: str
    message: str
    estimated_delay_seconds: int
    created_at: str
    updated_at: str
    acknowledged_at: str | None = None


class ExceptionCenterCountsResponse(BaseModel):
    pending_approval_runs: int
    critical_alerts: int
    failed_orders: int
    unassigned_orders: int
    total_open_items: int


class ExceptionCenterSummaryResponse(BaseModel):
    counts: ExceptionCenterCountsResponse
    pending_run: OptimizationRunResponse | None = None
    critical_alerts: list[ExceptionAlertResponse] = Field(default_factory=list)
    failed_orders: list[OrderResponse] = Field(default_factory=list)
    unassigned_orders: list[OrderResponse] = Field(default_factory=list)


class OptimizationSavingsTotals(BaseModel):
    """Big-number totals comparing the human-dispatcher baseline against the
    operator-selected scenario across every finished comparison in the range.
    """

    baseline_distance_km: float
    optimized_distance_km: float
    distance_saved_km: float
    # 0.226 → 22.6% — render-side multiplies by 100.
    distance_saved_pct: float
    baseline_duration_min: float
    optimized_duration_min: float
    duration_saved_min: float
    duration_saved_pct: float
    # Weighted by assigned_count so heavy days dominate the average.
    baseline_on_time_rate: float
    optimized_on_time_rate: float
    # positive = improvement (optimized > baseline).
    on_time_rate_delta: float
    late_minutes_saved: int
    energy_cost_saved_try: float
    fuel_saved_liters: float
    co2_saved_kg: float
    routes_saved: int


class OptimizationSavingsDayRow(BaseModel):
    date: str
    selected_scenario_key: str
    baseline_distance_km: float
    optimized_distance_km: float
    baseline_duration_min: float
    optimized_duration_min: float
    baseline_on_time_rate: float
    optimized_on_time_rate: float
    baseline_energy_cost_try: float
    optimized_energy_cost_try: float
    baseline_co2_kg: float
    optimized_co2_kg: float


class OptimizationSavingsSolverMixRow(BaseModel):
    scenario_key: str
    count: int


class OptimizationSavingsResponse(BaseModel):
    """Aggregated savings the operator unlocked by selecting an optimised
    scenario over the human-dispatcher baseline.

    `comparisons_count` is the number of comparisons that actually contributed
    (had both baseline and selected with summaries, and selected wasn't the
    baseline itself). Use it as the "empty state" signal on the frontend.
    """

    comparisons_count: int
    totals: OptimizationSavingsTotals
    per_day: list[OptimizationSavingsDayRow] = Field(default_factory=list)
    solver_mix: list[OptimizationSavingsSolverMixRow] = Field(default_factory=list)


# ───── Cost Savings (Layer 1 — pilot's headline KPI) ─────────────────────


class CostSavingsTotals(BaseModel):
    """Headline cost totals over the requested date range.

    Reads from materialized `route_performance_snapshot` rows so the
    dashboard doesn't re-aggregate raw orders/stops on every poll.
    Currency is whatever the energy-price snapshot used at run time
    (TRY today)."""

    total_fuel_cost_try: float
    total_fuel_liters: float
    total_co2_kg: float
    total_distance_km: float
    total_routes: int
    total_deliveries: int
    cost_per_delivery_try: float | None = None
    cost_per_km_try: float | None = None
    # Filled by Layer 2 when the counterfactual baseline starts running.
    fuel_cost_saved_try: float | None = None
    co2_saved_kg: float | None = None
    distance_saved_km: float | None = None


class CostSavingsDayRow(BaseModel):
    """One row per operational date — feeds the per-day chart."""

    date: str
    total_fuel_cost_try: float
    total_fuel_liters: float
    total_co2_kg: float
    total_distance_km: float
    routes: int
    fuel_cost_saved_try: float | None = None
    co2_saved_kg: float | None = None


class CostSavingsResponse(BaseModel):
    """The pilot's headline KPI surface: fuel cost in TRY (primary),
    CO₂ in kg (secondary derived), plus a per-day series for the chart."""

    routes_included: int
    totals: CostSavingsTotals
    per_day: list[CostSavingsDayRow] = Field(default_factory=list)


# ───── Solver Quality — Optimization-gains adjacent (Layer 2/3) ─────────


class ETAAccuracyStats(BaseModel):
    """How accurate the planner-time ETAs were over the range.

    Reads from `eta_prediction_log` rows whose `actual_arrival` has been
    reconciled (the stop completed). Empty `predictions` is the
    "no data yet" signal — typical until ~10 deliveries have finished
    against the new instrumentation."""

    predictions: int
    on_time_rate: float | None = None  # |error_s| ≤ on_time_window_s
    mean_abs_error_s: float | None = None
    p95_abs_error_s: float | None = None
    mean_error_signed_s: float | None = None  # +ve = systematically late
    on_time_window_s: int = 900  # 15 min by default


class FailureCauseRow(BaseModel):
    category: str
    count: int
    share: float  # 0..1 of total failures in range


class FailureCausesStats(BaseModel):
    """Breakdown of failed orders by typed cause category over the range."""

    total_failures: int
    by_category: list[FailureCauseRow] = Field(default_factory=list)


class PlanStabilityRow(BaseModel):
    created_at: datetime
    stability_pct: float | None
    orders_evaluated: int


class PlanStabilityStats(BaseModel):
    """Recent plan-stability scores. NULL stability_pct means the prior
    plan had no overlap to compare against (a first run, for example)."""

    samples: int
    avg_stability_pct: float | None = None
    recent: list[PlanStabilityRow] = Field(default_factory=list)


class SolverQualityResponse(BaseModel):
    """Three solver-quality lenses bundled into one round-trip — feeds
    the new cards in the Optimization-gains Insights tab.

    All three reuse data the pilot data-strategy layers already capture:
      * `eta_prediction_log` (Layer 2b) → ETA accuracy
      * `orders.failure_cause_category` (Layer 3) → failure causes
      * `plan_stability_scores` (Layer 2b) → plan stability
    """

    eta_accuracy: ETAAccuracyStats
    failure_causes: FailureCausesStats
    plan_stability: PlanStabilityStats
