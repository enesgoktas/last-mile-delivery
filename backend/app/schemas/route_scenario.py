from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.operations_policy import FleetUtilization, RoutingObjective


class RouteScenarioSummary(BaseModel):
    route_count: int = 0
    assigned_count: int = 0
    unassigned_count: int = 0
    vehicles_used: int = 0
    distance_km: float = 0.0
    duration_minutes: float = 0.0
    on_time_rate: float = 0.0
    late_minutes: int = 0
    realized_energy_cost_try: float = 0.0
    fuel_liters: float = 0.0
    electric_kwh: float = 0.0
    co2_kg: float | None = None
    solver_status: str = "PENDING"
    objective_breakdown: dict[str, Any] | None = None
    energy_price_sources: list[dict[str, Any]] = Field(default_factory=list)
    energy_price_freshness_status: str = "FALLBACK"
    warnings: list[str] = Field(default_factory=list)
    # Rich ALNS engine telemetry (None for OR-Tools / human-dispatcher scenarios)
    alns_telemetry: dict[str, Any] | None = None


class RouteScenarioResponse(BaseModel):
    id: uuid.UUID
    comparison_id: uuid.UUID
    key: str
    label: str
    routing_objective: RoutingObjective
    fleet_utilization: FleetUtilization
    solver_kind: str = "ortools_cvrptw"
    status: str
    optimization_run_id: uuid.UUID | None = None
    summary: RouteScenarioSummary | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class RouteScenarioComparisonResponse(BaseModel):
    id: uuid.UUID
    target_date: date
    status: str
    task_id: str | None = None
    selected_scenario_id: uuid.UUID | None = None
    current_policy_snapshot: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    applied_at: datetime | None = None
    scenarios: list[RouteScenarioResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class RouteScenarioCreateResponse(BaseModel):
    comparison: RouteScenarioComparisonResponse
    task_id: str
    message: str


class RouteScenarioSelectRequest(BaseModel):
    scenario_id: uuid.UUID


class RouteScenarioSelectResponse(BaseModel):
    comparison: RouteScenarioComparisonResponse
    approved_routes: int
    deleted_draft_routes: int
