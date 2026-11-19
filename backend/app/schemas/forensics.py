"""Schemas for the forensic order-timeline endpoint.

`GET /api/v1/orders/{id}/forensics` returns everything a dispatcher
needs to answer "what happened to this order?" in one round-trip:

  * Order header (status, scheduled_for_date, attempt_count, …)
  * Order status history (every PENDING → ASSIGNED → … transition)
  * Route + per-stop snapshots with planned vs actual arrival
  * Stop status history with lat/lon at each transition (Layer 0)
  * Dispatch decisions (chosen courier + score + alternatives) (Layer 0)
  * ETA prediction log (planner predictions vs actual) (Layer 2b)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ForensicsOrderHeader(BaseModel):
    id: uuid.UUID
    external_ref: str | None
    status: str
    priority: str
    scheduled_for_date: str | None  # ISO date
    earliest_delivery: datetime
    latest_delivery: datetime
    assigned_courier_id: uuid.UUID | None
    route_id: uuid.UUID | None
    attempt_count: int
    failed_at: datetime | None
    failure_cause_category: str | None
    failure_cause_subcategory: str | None


class ForensicsStatusHistoryRow(BaseModel):
    from_status: str | None
    to_status: str
    actor_user_id: uuid.UUID | None
    reason: str | None
    created_at: datetime


class ForensicsStopRow(BaseModel):
    id: uuid.UUID
    sequence_number: int
    stop_type: str
    status: str
    lat: float
    lon: float
    planned_arrival: datetime
    planned_departure: datetime | None
    actual_arrival: datetime | None
    failure_reason: str | None
    delay_cause: str | None


class ForensicsStopHistoryRow(BaseModel):
    stop_id: uuid.UUID
    from_status: str | None
    to_status: str
    reason: str | None
    lat: float | None
    lon: float | None
    created_at: datetime


class ForensicsDispatchDecisionRow(BaseModel):
    optimization_run_id: uuid.UUID | None
    kind: str
    chosen_courier_id: uuid.UUID | None
    chosen_score: float | None
    alternatives: list[dict[str, Any]] | None
    decision_reason: str | None
    created_at: datetime


class ForensicsETAPredictionRow(BaseModel):
    stop_id: uuid.UUID
    predicted_by: str
    predicted_at: datetime
    predicted_arrival: datetime
    actual_arrival: datetime | None
    prediction_horizon_s: int | None
    error_s: int | None


class ForensicsResponse(BaseModel):
    order: ForensicsOrderHeader
    status_history: list[ForensicsStatusHistoryRow] = Field(default_factory=list)
    stops: list[ForensicsStopRow] = Field(default_factory=list)
    stop_history: list[ForensicsStopHistoryRow] = Field(default_factory=list)
    dispatch_decisions: list[ForensicsDispatchDecisionRow] = Field(default_factory=list)
    eta_predictions: list[ForensicsETAPredictionRow] = Field(default_factory=list)
