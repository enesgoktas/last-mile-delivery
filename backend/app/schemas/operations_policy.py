from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.models.enums import FailureReason, FuelType, OrderPriority, OrderType, UserRole, VehicleType

FieldMode = Literal["OFF", "OPTIONAL", "REQUIRED"]
VisibilityMode = Literal["HIDDEN", "OPTIONAL", "REQUIRED"]
DispatchMode = Literal["AUTOMATIC", "MANUAL", "HYBRID"]
AssignmentStrategy = Literal["NEAREST_COURIER", "LEAST_BUSY", "ZONE_BASED", "PREFERRED_COURIER"]
RouteApprovalMode = Literal["NONE", "LARGE_ROUTES_ONLY", "ALL_ROUTES"]
PreArrivalCallMode = Literal["OFF", "SUGGESTED", "REQUIRED"]
SafeDropPolicy = Literal["DISALLOWED", "ALLOWED", "SELECTED_CUSTOMERS"]
FailureAction = Literal["DISPATCH_REVIEW", "AUTOMATIC_RETRY", "RETURN_TO_DEPOT", "CUSTOMER_RESCHEDULE"]
RetryTiming = Literal["SAME_DAY", "NEXT_DAY", "MANUAL_ONLY"]
SelfServiceMode = Literal["OFF", "RESCHEDULE_ONLY", "RESCHEDULE_AND_INSTRUCTIONS"]
EtaVisibility = Literal["NONE", "ETA_RANGE", "LIVE_MAP"]
CustomFieldType = Literal["TEXT", "NUMBER", "DATE", "SELECT", "BOOLEAN"]
OptimizationPresetKey = Literal["COST_SAVER", "BALANCED", "SERVICE_FIRST"]
FleetUtilization = Literal["EFFICIENT", "FULL_FLEET"]
RoutingObjective = Literal["FUEL_AWARE", "SERVICE_FIRST"]


DEFAULT_STATUS_LABELS: dict[str, str] = {
    "DRAFT": "Draft",
    "PENDING": "Pending dispatch",
    "ASSIGNED": "Driver assigned",
    "PICKED_UP": "Picked up",
    "IN_TRANSIT": "On the way",
    "DELIVERED": "Delivered",
    "FAILED": "Delivery issue",
    "RESCHEDULED": "Redelivery requested",
    "CANCELLED": "Cancelled",
}


class RouteApprovalSettings(BaseModel):
    mode: RouteApprovalMode = "NONE"
    large_route_stop_threshold: int = Field(default=12, ge=2, le=200)


class DispatchSettings(BaseModel):
    dispatch_mode: DispatchMode = "HYBRID"
    assignment_strategy: AssignmentStrategy = "NEAREST_COURIER"
    route_approval: RouteApprovalSettings = Field(default_factory=RouteApprovalSettings)


class VehicleClassEconomicCosts(BaseModel):
    fixed_route_cost: int = Field(default=0, ge=0, le=10_000_000)
    distance_cost_per_km: int = Field(default=0, ge=0, le=1_000_000)
    labor_cost_per_minute: int = Field(default=0, ge=0, le=1_000_000)
    fuel_cost_per_km: int = Field(default=0, ge=0, le=1_000_000)
    overtime_cost_per_minute: int = Field(default=0, ge=0, le=1_000_000)


_OPTIMIZATION_PRESET_COSTS: dict[OptimizationPresetKey, dict[VehicleType, dict[str, int]]] = {
    "COST_SAVER": {
        VehicleType.MOTORCYCLE: {
            "fixed_route_cost": 700,
            "distance_cost_per_km": 18,
            "labor_cost_per_minute": 12,
            "fuel_cost_per_km": 3,
            "overtime_cost_per_minute": 36,
        },
        VehicleType.CAR_VAN: {
            "fixed_route_cost": 950,
            "distance_cost_per_km": 28,
            "labor_cost_per_minute": 15,
            "fuel_cost_per_km": 8,
            "overtime_cost_per_minute": 45,
        },
        VehicleType.TRUCK: {
            "fixed_route_cost": 1_750,
            "distance_cost_per_km": 52,
            "labor_cost_per_minute": 22,
            "fuel_cost_per_km": 20,
            "overtime_cost_per_minute": 60,
        },
    },
    "BALANCED": {
        VehicleType.MOTORCYCLE: {
            "fixed_route_cost": 900,
            "distance_cost_per_km": 20,
            "labor_cost_per_minute": 14,
            "fuel_cost_per_km": 4,
            "overtime_cost_per_minute": 48,
        },
        VehicleType.CAR_VAN: {
            "fixed_route_cost": 1_200,
            "distance_cost_per_km": 32,
            "labor_cost_per_minute": 18,
            "fuel_cost_per_km": 10,
            "overtime_cost_per_minute": 60,
        },
        VehicleType.TRUCK: {
            "fixed_route_cost": 2_100,
            "distance_cost_per_km": 58,
            "labor_cost_per_minute": 26,
            "fuel_cost_per_km": 24,
            "overtime_cost_per_minute": 75,
        },
    },
    "SERVICE_FIRST": {
        VehicleType.MOTORCYCLE: {
            "fixed_route_cost": 1_050,
            "distance_cost_per_km": 22,
            "labor_cost_per_minute": 16,
            "fuel_cost_per_km": 5,
            "overtime_cost_per_minute": 60,
        },
        VehicleType.CAR_VAN: {
            "fixed_route_cost": 1_450,
            "distance_cost_per_km": 35,
            "labor_cost_per_minute": 22,
            "fuel_cost_per_km": 12,
            "overtime_cost_per_minute": 75,
        },
        VehicleType.TRUCK: {
            "fixed_route_cost": 2_400,
            "distance_cost_per_km": 64,
            "labor_cost_per_minute": 30,
            "fuel_cost_per_km": 28,
            "overtime_cost_per_minute": 90,
        },
    },
}


_OPTIMIZATION_PRESET_PENALTIES: dict[OptimizationPresetKey, dict[str, int]] = {
    "COST_SAVER": {
        "lateness_penalty_per_minute": 450,
        "overtime_penalty_per_minute": 36,
        "unserved_penalty": 220_000,
    },
    "BALANCED": {
        "lateness_penalty_per_minute": 750,
        "overtime_penalty_per_minute": 60,
        "unserved_penalty": 320_000,
    },
    "SERVICE_FIRST": {
        "lateness_penalty_per_minute": 1_200,
        "overtime_penalty_per_minute": 90,
        "unserved_penalty": 450_000,
    },
}


def optimization_vehicle_class_cost_defaults(
    preset_key: OptimizationPresetKey = "BALANCED",
) -> dict[VehicleType, VehicleClassEconomicCosts]:
    return {
        vehicle_type: VehicleClassEconomicCosts.model_validate(values)
        for vehicle_type, values in _OPTIMIZATION_PRESET_COSTS[preset_key].items()
    }


def optimization_priority_penalty_defaults() -> dict[OrderPriority, float]:
    return {
        OrderPriority.LOW: 0.75,
        OrderPriority.NORMAL: 1.0,
        OrderPriority.HIGH: 1.5,
        OrderPriority.URGENT: 2.0,
    }


def optimization_settings_for_preset(
    preset_key: OptimizationPresetKey = "BALANCED",
    *,
    economic_batch_optimizer_enabled: bool = False,
    fuel_priority_multiplier: float = 1.0,
    shadow_mode_enabled: bool = True,
    internal_override_enabled: bool = False,
) -> OptimizationSettings:
    penalties = _OPTIMIZATION_PRESET_PENALTIES[preset_key]
    return OptimizationSettings(
        preset_key=preset_key,
        economic_batch_optimizer_enabled=economic_batch_optimizer_enabled,
        fuel_priority_multiplier=fuel_priority_multiplier,
        shadow_mode_enabled=shadow_mode_enabled,
        internal_override_enabled=internal_override_enabled,
        vehicle_class_costs=optimization_vehicle_class_cost_defaults(preset_key),
        lateness_penalty_per_minute=penalties["lateness_penalty_per_minute"],
        overtime_penalty_per_minute=penalties["overtime_penalty_per_minute"],
        unserved_penalty=penalties["unserved_penalty"],
        max_lateness_minutes=60,
        max_overtime_minutes=60,
        priority_penalty_multipliers=optimization_priority_penalty_defaults(),
        decomposition_order_threshold=250,
    )


class OptimizationSettings(BaseModel):
    routing_objective: RoutingObjective = "FUEL_AWARE"
    preset_key: OptimizationPresetKey = "BALANCED"
    economic_batch_optimizer_enabled: bool = False
    fuel_priority_multiplier: float = Field(default=1.0, ge=0.0, le=5.0)
    shadow_mode_enabled: bool = True
    internal_override_enabled: bool = False
    vehicle_class_costs: dict[VehicleType, VehicleClassEconomicCosts] = Field(
        default_factory=optimization_vehicle_class_cost_defaults
    )
    lateness_penalty_per_minute: int = Field(default=750, ge=0, le=10_000_000)
    overtime_penalty_per_minute: int = Field(default=60, ge=0, le=10_000_000)
    unserved_penalty: int = Field(default=320_000, ge=0, le=100_000_000)
    max_lateness_minutes: int = Field(default=60, ge=0, le=720)
    max_overtime_minutes: int = Field(default=60, ge=0, le=720)
    priority_penalty_multipliers: dict[OrderPriority, float] = Field(
        default_factory=optimization_priority_penalty_defaults
    )
    decomposition_order_threshold: int = Field(default=250, ge=25, le=2_000)
    fleet_utilization: FleetUtilization = "FULL_FLEET"
    fuel_prices: dict[FuelType, float] = Field(
        default_factory=lambda: {
            FuelType.GASOLINE: 35.0,
            FuelType.DIESEL: 38.0,
            FuelType.LPG: 20.0,
            FuelType.ELECTRIC: 12.0,
            FuelType.HYBRID: 30.0,
        }
    )

    @model_validator(mode="before")
    @classmethod
    def _infer_public_objective(cls, data: Any) -> Any:
        if not isinstance(data, dict) or data.get("routing_objective"):
            return data
        next_data = dict(data)
        preset_key = next_data.get("preset_key")
        fuel_multiplier = next_data.get("fuel_priority_multiplier", 1.0)
        try:
            resolved_fuel_multiplier = float(fuel_multiplier or 0)
        except (TypeError, ValueError):
            resolved_fuel_multiplier = 1.0
        next_data["routing_objective"] = (
            "SERVICE_FIRST"
            if preset_key == "SERVICE_FIRST" and resolved_fuel_multiplier <= 0
            else "FUEL_AWARE"
        )
        return next_data

    @model_validator(mode="after")
    def _hydrate_defaults(self) -> OptimizationSettings:
        preset_costs = optimization_vehicle_class_cost_defaults(self.preset_key)
        merged_costs: dict[VehicleType, VehicleClassEconomicCosts] = {}
        for vehicle_type in VehicleType:
            merged_costs[vehicle_type] = VehicleClassEconomicCosts.model_validate(
                {
                    **preset_costs[vehicle_type].model_dump(mode="json"),
                    **(
                        self.vehicle_class_costs.get(vehicle_type).model_dump(mode="json")
                        if self.vehicle_class_costs.get(vehicle_type) is not None
                        else {}
                    ),
                }
            )
        self.vehicle_class_costs = merged_costs

        preset_penalties = _OPTIMIZATION_PRESET_PENALTIES[self.preset_key]
        if self.lateness_penalty_per_minute <= 0:
            self.lateness_penalty_per_minute = preset_penalties["lateness_penalty_per_minute"]
        if self.overtime_penalty_per_minute <= 0:
            self.overtime_penalty_per_minute = preset_penalties["overtime_penalty_per_minute"]
        if self.unserved_penalty <= 0:
            self.unserved_penalty = preset_penalties["unserved_penalty"]

        multipliers = optimization_priority_penalty_defaults()
        for priority, multiplier in self.priority_penalty_multipliers.items():
            if multiplier <= 0:
                continue
            multipliers[priority] = multiplier
        self.priority_penalty_multipliers = multipliers
        return self


class DeliveryWorkflowSettings(BaseModel):
    require_pickup_step: bool = True
    require_arrival_scan: bool = True
    pre_arrival_call: PreArrivalCallMode = "SUGGESTED"
    wait_time_minutes: int = Field(default=3, ge=0, le=180)
    safe_drop_policy: SafeDropPolicy = "ALLOWED"
    customer_status_labels: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_STATUS_LABELS))


class ProofOfDeliveryRequirements(BaseModel):
    require_photo: bool = True
    photo_count: int = Field(default=1, ge=0, le=5)
    require_signature: bool = False
    require_otp: bool = False
    receiver_name_mode: FieldMode = "OPTIONAL"
    notes_mode: FieldMode = "OPTIONAL"


class ProofOfDeliverySettings(BaseModel):
    defaults: ProofOfDeliveryRequirements = Field(default_factory=ProofOfDeliveryRequirements)
    by_order_type: dict[OrderType, ProofOfDeliveryRequirements] = Field(default_factory=dict)


class FailureHandlingSettings(BaseModel):
    allowed_standard_reasons: list[FailureReason] = Field(
        default_factory=lambda: [
            FailureReason.NOT_HOME,
            FailureReason.WRONG_ADDRESS,
            FailureReason.REFUSED,
            FailureReason.DAMAGED,
            FailureReason.ACCESS_DENIED,
            FailureReason.OTHER,
        ]
    )
    custom_reasons: list[str] = Field(default_factory=list)
    require_notes_for_other: bool = True
    post_failure_action: FailureAction = "CUSTOMER_RESCHEDULE"
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_timing: RetryTiming = "MANUAL_ONLY"
    escalation_hours: int = Field(default=48, ge=1, le=168)
    self_service_mode: SelfServiceMode = "RESCHEDULE_ONLY"

    @model_validator(mode="after")
    def _normalize_custom_reasons(self) -> FailureHandlingSettings:
        seen: set[str] = set()
        normalized: list[str] = []
        for raw in self.custom_reasons:
            label = " ".join(raw.split()).strip()
            if not label:
                continue
            lowered = label.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(label[:80])
        self.custom_reasons = normalized
        return self


class NotificationChannelPreferences(BaseModel):
    email: bool = True
    sms: bool = True
    push: bool = True


def _default_event_channels() -> dict[str, NotificationChannelPreferences]:
    return {
        "order.created": NotificationChannelPreferences(email=True, sms=False, push=False),
        "order.assigned": NotificationChannelPreferences(email=True, sms=True, push=True),
        "order.picked_up": NotificationChannelPreferences(email=False, sms=False, push=True),
        "order.in_transit": NotificationChannelPreferences(email=False, sms=True, push=True),
        "order.arrived_delivery": NotificationChannelPreferences(email=False, sms=True, push=True),
        "order.delivered": NotificationChannelPreferences(email=True, sms=True, push=True),
        "order.failed": NotificationChannelPreferences(email=True, sms=True, push=True),
        "order.failed.escalated": NotificationChannelPreferences(email=True, sms=False, push=False),
        "order.cancelled": NotificationChannelPreferences(email=True, sms=True, push=False),
    }


class NotificationSettings(BaseModel):
    language: str = Field(default="en", min_length=2, max_length=12)
    branding_name: str = Field(default="LastMile Delivery", min_length=1, max_length=80)
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "08:00"
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    eta_visibility: EtaVisibility = "LIVE_MAP"
    event_channels: dict[str, NotificationChannelPreferences] = Field(default_factory=_default_event_channels)


class StandardOrderFieldSettings(BaseModel):
    customer_phone: VisibilityMode = "OPTIONAL"
    notes: VisibilityMode = "OPTIONAL"
    external_ref: VisibilityMode = "OPTIONAL"
    depot_id: VisibilityMode = "OPTIONAL"
    is_fragile: VisibilityMode = "OPTIONAL"
    is_temp_sensitive: VisibilityMode = "OPTIONAL"


class CustomOrderFieldDefinition(BaseModel):
    key: str = Field(min_length=2, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=80)
    type: CustomFieldType
    required: bool = False
    options: list[str] = Field(default_factory=list)
    applies_to_order_types: list[OrderType] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_field(self) -> CustomOrderFieldDefinition:
        if self.type == "SELECT" and not self.options:
            raise ValueError("SELECT custom fields must define at least one option")
        if self.type != "SELECT" and self.options:
            self.options = []
        seen: set[str] = set()
        deduped_types: list[OrderType] = []
        for order_type in self.applies_to_order_types:
            key = order_type.value
            if key in seen:
                continue
            seen.add(key)
            deduped_types.append(order_type)
        self.applies_to_order_types = deduped_types
        return self


class OrderFormSettings(BaseModel):
    standard_fields: StandardOrderFieldSettings = Field(default_factory=StandardOrderFieldSettings)
    custom_fields: list[CustomOrderFieldDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _dedupe_custom_keys(self) -> OrderFormSettings:
        seen: set[str] = set()
        deduped: list[CustomOrderFieldDefinition] = []
        for field in self.custom_fields:
            if field.key in seen:
                continue
            seen.add(field.key)
            deduped.append(field)
        self.custom_fields = deduped
        return self


class RolePermissionSet(BaseModel):
    cancel_order: bool = False
    edit_order: bool = False
    reassign_order: bool = False
    override_pod: bool = False
    manage_settings: bool = False


class TeamPermissionsSettings(BaseModel):
    OWNER: RolePermissionSet = Field(
        default_factory=lambda: RolePermissionSet(
            cancel_order=True,
            edit_order=True,
            reassign_order=True,
            override_pod=True,
            manage_settings=True,
        )
    )
    ADMIN: RolePermissionSet = Field(
        default_factory=lambda: RolePermissionSet(
            cancel_order=True,
            edit_order=True,
            reassign_order=True,
            override_pod=True,
            manage_settings=True,
        )
    )
    DISPATCHER: RolePermissionSet = Field(
        default_factory=lambda: RolePermissionSet(
            cancel_order=True,
            edit_order=True,
            reassign_order=True,
            override_pod=False,
            manage_settings=False,
        )
    )
    COURIER: RolePermissionSet = Field(default_factory=RolePermissionSet)
    # COMPANY_ADMIN is read-only by design — every write permission stays
    # off. They get into operational dashboards via DISPATCH_ACCESS_ROLES
    # (auth layer) but the per-action `manage_settings`, `cancel_order`,
    # etc. checks reject every write.
    COMPANY_ADMIN: RolePermissionSet = Field(default_factory=RolePermissionSet)

    def for_role(self, role: UserRole | str | None) -> RolePermissionSet:
        role_name = role.value if isinstance(role, UserRole) else str(role or "").upper()
        return getattr(self, role_name, RolePermissionSet())


class FeatureFlags(BaseModel):
    """Per-tenant feature gates.

    Each flag controls whether a behaviour or UI module is active for the
    tenant. Backwards-compatible defaults (`deliveries_enabled=True` and
    `multi_trip_enabled=True`) preserve existing tenants' behaviour.

    Adding a new flag here:
      1. Pick a sensible default (off for opt-in features, on for things
         existing tenants already get).
      2. The schema-driven settings UI picks it up automatically via
         `feature_flags_schema()` below.
      3. Runtime gates: read via `policy.feature_flags.<flag_name>` —
         never hardcode the default at the call site.
    """

    # ── Service modes ────────────────────────────────────────────────
    deliveries_enabled: bool = True
    rides_enabled: bool = False

    # ── Ride-specific feature gates ──────────────────────────────────
    ride_pickup_buffer_enabled: bool = False
    ride_strict_lateness_enabled: bool = False
    ride_airport_features_enabled: bool = False
    ride_no_show_handling_enabled: bool = False

    # ── EV / battery awareness ───────────────────────────────────────
    ev_battery_awareness_enabled: bool = False
    ev_low_battery_alerts_enabled: bool = False
    ev_auto_charging_enabled: bool = False  # Phase 2

    # ── Routing / fleet capabilities ─────────────────────────────────
    multi_depot_vehicles_enabled: bool = False

    # ── Existing platform features (gateable for new tenants) ────────
    multi_trip_enabled: bool = True
    traffic_aware_routing_enabled: bool = True


# Schema metadata for the schema-driven settings UI. Adding a flag above
# requires adding an entry here too — the UI iterates this list to render
# checkboxes with descriptions and dependency hints.
FEATURE_FLAG_SCHEMA: list[dict[str, Any]] = [
    # Service modes
    {
        "name": "deliveries_enabled",
        "label": "Package delivery mode",
        "description": "Tenant can create non-RIDE orders (ON_DEMAND / SCHEDULED / BULK / REVERSE) and dispatchers see the Orders page.",
        "category": "Service modes",
        "default": True,
        "depends_on": [],
    },
    {
        "name": "rides_enabled",
        "label": "Passenger transport mode",
        "description": "Tenant can create RIDE orders and dispatchers see the Rides page. Cannot disable both this and 'Package delivery mode'.",
        "category": "Service modes",
        "default": False,
        "depends_on": [],
    },
    # Ride-specific
    {
        "name": "ride_pickup_buffer_enabled",
        "label": "Driver-arrives-early hard constraint",
        "description": "Driver must arrive at pickup at least `pickup_buffer_minutes_for_rides` before the passenger ready time. Hard constraint — orders that can't satisfy it stay unassigned with a dispatcher alert.",
        "category": "Rides",
        "default": False,
        "depends_on": ["rides_enabled"],
    },
    {
        "name": "ride_strict_lateness_enabled",
        "label": "Strict ride lateness",
        "description": "Zero soft-grace on RIDE pickup arrivals (vs. the 300-second default for deliveries). Pickups must be on time, full stop.",
        "category": "Rides",
        "default": False,
        "depends_on": ["rides_enabled"],
    },
    {
        "name": "ride_airport_features_enabled",
        "label": "Airport pickup features",
        "description": "Adds `flight_info` field to ride orders + airport-punctuality report. Drivers see flight number + terminal banner.",
        "category": "Rides",
        "default": False,
        "depends_on": ["rides_enabled"],
    },
    {
        "name": "ride_no_show_handling_enabled",
        "label": "Passenger no-show handling",
        "description": "Driver app shows a wait timer at pickup; after `passenger_no_show_wait_minutes` the order can be marked no-show and the route continues.",
        "category": "Rides",
        "default": False,
        "depends_on": ["rides_enabled"],
    },
    # EV / battery
    {
        "name": "ev_battery_awareness_enabled",
        "label": "EV battery range awareness",
        "description": "Vehicle's `current_battery_pct × max_battery_range_km` becomes a hard `max_distance_m` cap. Half-charged vehicles get fewer / shorter assignments.",
        "category": "Electric vehicles",
        "default": False,
        "depends_on": [],
    },
    {
        "name": "ev_low_battery_alerts_enabled",
        "label": "Low-battery dispatcher alerts",
        "description": "Dashboard widget listing vehicles below `battery_low_threshold_pct`, so dispatchers can plug them in.",
        "category": "Electric vehicles",
        "default": False,
        "depends_on": ["ev_battery_awareness_enabled"],
    },
    {
        "name": "ev_auto_charging_enabled",
        "label": "Auto-insert charging stops (Phase 2)",
        "description": "Solver inserts charging stops mid-route when a vehicle's remaining range can't cover the planned route. Not yet implemented.",
        "category": "Electric vehicles",
        "default": False,
        "depends_on": ["ev_battery_awareness_enabled"],
    },
    # Routing / fleet capabilities
    {
        "name": "multi_depot_vehicles_enabled",
        "label": "Vehicles can serve multiple depots",
        "description": "Couriers may be assigned additional reload depots and an optional end-of-shift depot. The solver will reload mid-route at the closest allowed depot when cheaper than returning home.",
        "category": "Routing",
        "default": False,
        "depends_on": [],
    },
    # Platform features
    {
        "name": "multi_trip_enabled",
        "label": "Virtual multi-trip vehicle expansion",
        "description": "Allows a single physical courier to do multiple trips per day (depot reload between trips). Default on; legacy tenants depend on this.",
        "category": "Platform",
        "default": True,
        "depends_on": [],
    },
    {
        "name": "traffic_aware_routing_enabled",
        "label": "Traffic-aware routing",
        "description": "Second solve pass with traffic-corrected duration matrices. Default on for all tenants.",
        "category": "Platform",
        "default": True,
        "depends_on": [],
    },
]


def feature_flags_schema() -> list[dict[str, Any]]:
    """Public accessor for the flag schema. Frontend fetches this to render
    the schema-driven settings UI without ever hardcoding flag names."""
    return [dict(item) for item in FEATURE_FLAG_SCHEMA]


class RideSettings(BaseModel):
    """Numeric defaults for RIDE-mode behaviour (Phase 1).

    These are tenant-wide defaults; per-order overrides on the Order itself
    take precedence (e.g. `pickup_buffer_minutes_override`). Consulted only
    when the corresponding feature flag is on.
    """

    pickup_buffer_minutes: int = Field(default=5, ge=0, le=60)
    service_time_pickup_seconds: int = Field(default=60, ge=0, le=600)
    passenger_no_show_wait_minutes: int = Field(default=10, ge=1, le=60)
    lateness_grace_seconds: int = Field(default=0, ge=0, le=600)
    # Default per-vehicle passenger ceiling when a dispatcher ticks
    # 'allow_split' on a large RIDE without specifying a value. 4
    # matches typical sedan capacity. Per-order override is on
    # OrderCreate.passengers_per_vehicle.
    default_passengers_per_vehicle: int = Field(default=4, ge=1, le=20)


class EVSettings(BaseModel):
    """Numeric defaults for EV battery awareness (Phase 1) + auto-
    charging dispatch (Phase 2.3).
    """

    battery_low_threshold_pct: int = Field(default=20, ge=0, le=100)
    default_charge_target_pct: int = Field(default=80, ge=0, le=100)

    # ── Phase 2.3 auto-charging dispatch knobs ──
    # Network whitelist applies when the tenant only wants chargers from
    # specific operators (e.g. they have a corporate account with ZES).
    # None = any operator. Match is case-insensitive on
    # ChargingStation.operator.
    allowed_charger_networks: list[str] | None = None
    # Minimum kW for a charger to be considered. 50 kW is the de-facto
    # DC fast-charge floor; AC-only stations at ~22 kW would imply
    # multi-hour stops that aren't useful for executive transport.
    min_charger_kw: int = Field(default=50, ge=0, le=500)
    # Geographic envelope around the route's waypoints for candidate
    # selection. Larger radius = more candidates = bigger matrix.
    max_charger_search_radius_km: float = Field(default=5.0, ge=0.5, le=50.0)
    # State-of-charge to plan up to. Real charge curves taper above
    # ~80%, so we don't plan to 100% by default.
    charge_target_pct_for_planning: int = Field(default=80, ge=10, le=100)
    # Conservative ceiling on the kW we assume the courier will
    # actually receive (vs the charger's nameplate max). Vehicles
    # often peak below the charger's rating.
    estimated_kw_used_for_planning: int = Field(default=50, ge=10, le=500)
    # Flat overhead added to every charging stop's service time. Covers
    # parking, plug-in handshake, payment, and the (highly variable)
    # wait when the chosen stall is occupied. Conservative default —
    # tune down once we have real "arrived → plugged-in" telemetry.
    charging_setup_overhead_minutes: int = Field(default=15, ge=0, le=60)
    # ── Post-processor knobs (charging_post_processor.py) ──
    # Trigger fraction for the in-route refuel search. With 0.85 we
    # search for a charger when the next leg would consume 85% of
    # remaining range; tighten in winter (heater/cold reduces real
    # range) and relax in mild weather.
    route_safety_threshold: float = Field(default=0.85, ge=0.5, le=0.95)
    # Cap on how many of our own EVs we'll route through the same
    # station within the same one-hour window. Stations rarely have
    # more than 2-4 stalls; clumping causes queueing and undermines
    # the schedule. Set higher only if the operator confirms capacity.
    max_evs_per_station_per_hour: int = Field(default=2, ge=1, le=20)


class OperationsPolicy(BaseModel):
    dispatch: DispatchSettings = Field(default_factory=DispatchSettings)
    optimization: OptimizationSettings = Field(default_factory=OptimizationSettings)
    delivery_workflow: DeliveryWorkflowSettings = Field(default_factory=DeliveryWorkflowSettings)
    proof_of_delivery: ProofOfDeliverySettings = Field(default_factory=ProofOfDeliverySettings)
    failure_handling: FailureHandlingSettings = Field(default_factory=FailureHandlingSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    order_form: OrderFormSettings = Field(default_factory=OrderFormSettings)
    team_permissions: TeamPermissionsSettings = Field(default_factory=TeamPermissionsSettings)
    feature_flags: FeatureFlags = Field(default_factory=FeatureFlags)
    ride_settings: RideSettings = Field(default_factory=RideSettings)
    ev_settings: EVSettings = Field(default_factory=EVSettings)

    @model_validator(mode="after")
    def _validate_feature_flag_consistency(self) -> "OperationsPolicy":
        # A tenant must offer at least one service mode, otherwise they
        # can't do anything.
        flags = self.feature_flags
        if not (flags.deliveries_enabled or flags.rides_enabled):
            raise ValueError(
                "feature_flags: at least one of deliveries_enabled / "
                "rides_enabled must be True"
            )
        # Dependency checks per FEATURE_FLAG_SCHEMA.
        for entry in FEATURE_FLAG_SCHEMA:
            depends = entry.get("depends_on") or []
            if not depends:
                continue
            if not getattr(flags, entry["name"], False):
                continue
            for dep in depends:
                if not getattr(flags, dep, False):
                    raise ValueError(
                        f"feature_flags: '{entry['name']}' requires '{dep}' to be enabled"
                    )
        return self


class PolicyPreset(BaseModel):
    key: str
    label: str
    description: str
    policy: OperationsPolicy


def default_operations_policy() -> OperationsPolicy:
    return OperationsPolicy()


def default_operations_policy_dict() -> dict[str, Any]:
    return default_operations_policy().model_dump(mode="json")


def deep_merge_dicts(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = deep_merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged
