import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import (
    FailureReason,
    OptimizationRunStatus,
    OptimizationRunType,
    OrderPriority,
    RouteStatus,
    StopStatus,
    StopType,
    VehicleType,
)
from app.schemas.operations_policy import FieldMode
from app.utils.compat import StrEnum


class FailureReasonOptionResponse(BaseModel):
    value: str
    label: str
    custom_reason_label: str | None = None


class StopProofPolicyResponse(BaseModel):
    require_photo: bool
    photo_count: int
    require_signature: bool
    require_otp: bool
    receiver_name_mode: FieldMode
    notes_mode: FieldMode


class StopFailurePolicyResponse(BaseModel):
    allowed_reasons: list[FailureReasonOptionResponse]
    require_notes_for_other: bool
    post_failure_action: str
    self_service_mode: str


class StopActionPolicyResponse(BaseModel):
    require_arrival_scan: bool
    pre_arrival_call: str
    wait_time_minutes: int
    safe_drop_policy: str
    proof_of_delivery: StopProofPolicyResponse
    failure_handling: StopFailurePolicyResponse


class RouteStopResponse(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID | None
    sequence_number: int
    stop_type: StopType
    lat: float
    lon: float
    address: str
    planned_arrival: datetime
    planned_departure: datetime | None = None
    actual_arrival: datetime | None
    status: StopStatus
    failure_reason: FailureReason | None
    failure_reason_detail: str | None = None
    notes: str | None
    dispatcher_note: str | None = None
    customer_phone: str | None = None
    action_policy: StopActionPolicyResponse | None = None

    model_config = {"from_attributes": True}


class RouteResponse(BaseModel):
    id: uuid.UUID
    courier_id: uuid.UUID
    status: RouteStatus
    planned_distance_km: float
    actual_distance_km: float | None
    optimization_run_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    stops: list[RouteStopResponse] = []

    model_config = {"from_attributes": True}


class StopFailRequest(BaseModel):
    failure_reason: FailureReason
    custom_reason_label: str | None = Field(default=None, max_length=80)
    notes: str | None = None


class StopCompleteRequest(BaseModel):
    photo_urls: list[str] | None = Field(default=None, max_length=5)
    photo_url: str | None = Field(default=None, max_length=1000)
    signature_url: str | None = Field(default=None, max_length=1000)
    receiver_name: str | None = Field(default=None, max_length=255)
    otp_code: str | None = Field(default=None, max_length=32)
    notes: str | None = None


class PodUploadUrlRequest(BaseModel):
    file_type: str = Field(pattern="^(photo|signature)$")
    content_type: str = Field(default="image/jpeg", max_length=120)


class PodUploadUrlResponse(BaseModel):
    upload_url: str
    file_url: str
    object_key: str
    expires_at: datetime


class OptimizationRunResponse(BaseModel):
    id: uuid.UUID
    task_id: str | None
    run_type: OptimizationRunType
    status: OptimizationRunStatus
    routes_created: int | None
    unassigned_count: int | None
    solver_status: str | None
    metrics: dict | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class GeoJSONGeometry(BaseModel):
    type: str
    coordinates: Any


class GeoJSONFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: GeoJSONGeometry
    properties: dict[str, Any] = Field(default_factory=dict)


class GeoJSONFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[GeoJSONFeature] = Field(default_factory=list)


class RouteGeometryResponse(BaseModel):
    route_id: uuid.UUID
    status: RouteStatus
    source: str
    generated_at: datetime
    geometry: GeoJSONFeatureCollection


# ---------------------------------------------------------------------------
# Optimization run preview schemas
# ---------------------------------------------------------------------------

class PreviewOrderSummary(BaseModel):
    order_id: uuid.UUID
    pickup_address: str
    delivery_address: str
    weight_kg: float
    is_fragile: bool
    is_temp_sensitive: bool
    priority: OrderPriority
    earliest_delivery: datetime
    latest_delivery: datetime
    on_time: bool


class PreviewStopResponse(BaseModel):
    stop_id: uuid.UUID
    sequence_number: int
    stop_type: StopType
    lat: float
    lon: float
    address: str
    planned_arrival: datetime
    planned_departure: datetime | None = None
    order: PreviewOrderSummary | None = None


class PreviewViolation(BaseModel):
    type: str
    severity: str
    message: str
    order_id: uuid.UUID | None = None


class PreviewETASummary(BaseModel):
    earliest_departure: datetime | None = None
    latest_arrival: datetime | None = None
    total_duration_minutes: float | None = None


class EconomicCostBreakdownResponse(BaseModel):
    total_generalized_cost: int = 0
    distance_cost: int = 0
    labor_cost: int = 0
    fuel_cost: int = 0
    fixed_route_cost: int = 0
    lateness_penalty: int = 0
    overtime_penalty: int = 0
    unserved_penalty: int = 0
    late_minutes: int = 0
    overtime_minutes: int = 0
    active_vehicle_count: int = 0
    estimated_fuel_liters: float | None = None
    estimated_co2_g: float | None = None


class EconomicCostDeltaResponse(BaseModel):
    total_generalized_cost: int = 0
    distance_cost: int = 0
    labor_cost: int = 0
    fuel_cost: int = 0
    fixed_route_cost: int = 0
    lateness_penalty: int = 0
    overtime_penalty: int = 0
    unserved_penalty: int = 0
    late_minutes: int = 0
    overtime_minutes: int = 0
    active_vehicle_count: int = 0


class PreviewExplainabilityChangeType(StrEnum):
    UNCHANGED = "UNCHANGED"
    ASSIGNED = "ASSIGNED"
    MOVED = "MOVED"
    UNASSIGNED = "UNASSIGNED"
    REORDERED = "REORDERED"


class PreviewRouteChangeType(StrEnum):
    UNCHANGED = "UNCHANGED"
    NEW_ROUTE = "NEW_ROUTE"
    MODIFIED_ROUTE = "MODIFIED_ROUTE"


class PreviewExplainabilityTone(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"


class PreviewExplainabilityItem(BaseModel):
    code: str
    tone: PreviewExplainabilityTone = PreviewExplainabilityTone.INFO
    message: str


class PreviewChangedOrder(BaseModel):
    order_id: uuid.UUID
    delivery_address: str
    priority: OrderPriority
    change_type: PreviewExplainabilityChangeType
    from_courier_id: uuid.UUID | None = None
    to_courier_id: uuid.UUID | None = None
    from_route_id: uuid.UUID | None = None
    to_route_id: uuid.UUID | None = None


class PreviewRouteExplainability(BaseModel):
    change_type: PreviewRouteChangeType
    summary: str
    previous_route_id: uuid.UUID | None = None
    previous_courier_id: uuid.UUID | None = None
    changed_orders: list[PreviewChangedOrder] = Field(default_factory=list)
    reasons: list[PreviewExplainabilityItem] = Field(default_factory=list)
    attention_items: list[PreviewExplainabilityItem] = Field(default_factory=list)


class PreviewRouteResponse(BaseModel):
    route_id: uuid.UUID
    courier_id: uuid.UUID
    vehicle_type: VehicleType
    planned_distance_km: float
    stop_count: int
    order_count: int
    total_weight_kg: float
    eta: PreviewETASummary
    cost_breakdown: EconomicCostBreakdownResponse | None = None
    cost_delta_vs_current: EconomicCostDeltaResponse | None = None
    violations: list[PreviewViolation] = []
    stops: list[PreviewStopResponse] = []
    explainability: PreviewRouteExplainability | None = None


class PreviewSummary(BaseModel):
    total_routes: int
    total_orders: int
    total_unassigned: int
    total_distance_km: float
    total_violations: int
    violation_breakdown: dict[str, int] = Field(default_factory=dict)
    on_time_rate: float | None
    cost_breakdown: EconomicCostBreakdownResponse | None = None
    cost_delta_vs_current: EconomicCostDeltaResponse | None = None


class RunPreviewExplainability(BaseModel):
    no_significant_changes: bool = False
    summary: str
    orders_newly_assigned: int = 0
    orders_moved_between_couriers: int = 0
    orders_now_unassigned: int = 0
    orders_reordered: int = 0
    routes_created: int = 0
    routes_removed: int = 0
    changed_orders: list[PreviewChangedOrder] = Field(default_factory=list)
    reasons: list[PreviewExplainabilityItem] = Field(default_factory=list)
    attention_items: list[PreviewExplainabilityItem] = Field(default_factory=list)


class RunPreviewResponse(BaseModel):
    optimization_run_id: uuid.UUID
    run_status: OptimizationRunStatus
    solver_status: str | None
    routes: list[PreviewRouteResponse]
    unassigned_order_ids: list[uuid.UUID]
    summary: PreviewSummary
    explainability: RunPreviewExplainability


class DispatcherNoteRequest(BaseModel):
    dispatcher_note: str | None = None
