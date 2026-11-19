from app.utils.compat import StrEnum


class UserRole(StrEnum):
    SUPERADMIN = "SUPERADMIN"  # platform-level: cross-tenant control plane
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    DISPATCHER = "DISPATCHER"
    COURIER = "COURIER"
    COMPANY_ADMIN = "COMPANY_ADMIN"  # per-tenant read-only ops + reporting


# Platform-level roles bypass per-tenant scoping. Treat with care — anyone
# in this set can read/write across every tenant in the system.
PLATFORM_ROLES = frozenset({UserRole.SUPERADMIN})
# ADMIN_LIKE means writeable admin: can manage settings, invite users.
# COMPANY_ADMIN intentionally NOT in this set — they're read-only.
ADMIN_LIKE_ROLES = frozenset({UserRole.OWNER, UserRole.ADMIN})
# DISPATCH_ACCESS gates the operational dashboards (orders, routes, fleet,
# analytics). COMPANY_ADMIN belongs here since they need ops visibility,
# but per-tenant write paths gate further on operations_policy
# team_permissions which we initialise as all-false for COMPANY_ADMIN.
DISPATCH_ACCESS_ROLES = frozenset({
    *ADMIN_LIKE_ROLES,
    UserRole.DISPATCHER,
    UserRole.COMPANY_ADMIN,
})


class VehicleType(StrEnum):
    MOTORCYCLE = "MOTORCYCLE"
    CAR_VAN = "CAR_VAN"
    TRUCK = "TRUCK"


class FuelType(StrEnum):
    GASOLINE = "GASOLINE"
    DIESEL = "DIESEL"
    LPG = "LPG"
    ELECTRIC = "ELECTRIC"
    HYBRID = "HYBRID"


class AvailabilityStatus(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    ON_BREAK = "ON_BREAK"


class OrderType(StrEnum):
    ON_DEMAND = "ON_DEMAND"
    SCHEDULED = "SCHEDULED"
    BULK = "BULK"
    REVERSE = "REVERSE"
    PICKUP_AND_DELIVERY = "PICKUP_AND_DELIVERY"
    RETURN = "RETURN"
    RIDE = "RIDE"  # passenger transport, gated by features.rides_enabled


# Passenger orders use a different validation surface than package orders
# (no weight/volume, but a `passenger_count`, name, phone). Helper set keeps
# the type check in one place.
RIDE_ORDER_TYPES = frozenset({OrderType.RIDE})


class OrderStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    RESCHEDULED = "RESCHEDULED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class OrderPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class RouteStatus(StrEnum):
    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


class StopType(StrEnum):
    PICKUP = "PICKUP"
    DELIVERY = "DELIVERY"
    DEPOT_RETURN = "DEPOT_RETURN"
    DEPOT_RELOAD = "DEPOT_RELOAD"
    CHARGING = "CHARGING"  # EV charging stop (Phase 2 ev_auto_charging_enabled)


class PackageStatus(StrEnum):
    AT_DEPOT = "AT_DEPOT"
    LOADED = "LOADED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    RETURNED_TO_DEPOT = "RETURNED_TO_DEPOT"
    LOST = "LOST"


class InventoryEventType(StrEnum):
    RECEIVED_AT_DEPOT = "RECEIVED_AT_DEPOT"
    LOADED_ONTO_VEHICLE = "LOADED_ONTO_VEHICLE"
    DEPARTED_DEPOT = "DEPARTED_DEPOT"
    DELIVERED_TO_CUSTOMER = "DELIVERED_TO_CUSTOMER"
    PICKED_UP_FROM_CUSTOMER = "PICKED_UP_FROM_CUSTOMER"
    RETURNED_TO_DEPOT = "RETURNED_TO_DEPOT"
    RETURN_RECEIVED = "RETURN_RECEIVED"
    MARKED_LOST = "MARKED_LOST"



class OrderLegKind(StrEnum):
    FIRST_MILE = "FIRST_MILE"
    LINEHAUL = "LINEHAUL"
    LAST_MILE = "LAST_MILE"


class OrderLegStatus(StrEnum):
    PENDING = "PENDING"
    PLANNED = "PLANNED"
    IN_TRANSIT = "IN_TRANSIT"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class StopStatus(StrEnum):
    PENDING = "PENDING"
    ARRIVED = "ARRIVED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FailureReason(StrEnum):
    NOT_HOME = "NOT_HOME"
    WRONG_ADDRESS = "WRONG_ADDRESS"
    REFUSED = "REFUSED"
    DAMAGED = "DAMAGED"
    ACCESS_DENIED = "ACCESS_DENIED"
    PASSENGER_NO_SHOW = "PASSENGER_NO_SHOW"
    # Operational failure causes — added with the pilot data strategy
    # so the failure-cause dashboard can split customer-side issues
    # (NOT_HOME, REFUSED, ACCESS_DENIED…) from courier/vehicle/system
    # issues that we can act on internally.
    COURIER_RAN_LATE = "COURIER_RAN_LATE"
    VEHICLE_BREAKDOWN = "VEHICLE_BREAKDOWN"
    WEATHER = "WEATHER"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    OTHER = "OTHER"


class FailureCauseCategory(StrEnum):
    """Top-level root cause for a failed delivery.

    Lets the failure-cause dashboard split "customer-side issues we can't
    fix" (CUSTOMER, ADDRESS) from "operational problems we can"
    (COURIER, VEHICLE, SYSTEM, WEATHER). The flat `FailureReason` enum
    stays for stop-level UX; this typed pair is for trend analysis.
    """

    CUSTOMER = "CUSTOMER"
    ADDRESS = "ADDRESS"
    COURIER = "COURIER"
    VEHICLE = "VEHICLE"
    WEATHER = "WEATHER"
    SYSTEM = "SYSTEM"
    OTHER = "OTHER"


class StopDelayCause(StrEnum):
    """Why a non-failed stop missed its planned arrival.

    Optional metadata captured at stop-completion time when actual_arrival
    deviates from planned_arrival by more than a threshold. Distinct from
    `FailureReason` (the stop succeeded; it was just late).
    """
    TRAFFIC = "TRAFFIC"
    LONG_SERVICE = "LONG_SERVICE"
    WAITING_AT_PICKUP = "WAITING_AT_PICKUP"
    REROUTE = "REROUTE"
    UPSTREAM_DELAY = "UPSTREAM_DELAY"
    OTHER = "OTHER"


class DispatchDecisionKind(StrEnum):
    """The flow that produced a dispatch decision row.

    Lets analytics filter "why was this order assigned this way" by the
    code path: batch vs range vs on-demand vs reoptimization. The decision
    log is shared across all four paths.
    """
    BATCH_DISPATCH = "BATCH_DISPATCH"
    RANGE_DISPATCH = "RANGE_DISPATCH"
    ON_DEMAND_INSERTION = "ON_DEMAND_INSERTION"
    REOPTIMIZATION = "REOPTIMIZATION"


class NotificationChannel(StrEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    PUSH = "PUSH"


class PushProvider(StrEnum):
    FCM = "FCM"
    EXPO = "EXPO"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


class WebhookDeliveryStatus(StrEnum):
    SUCCESS = "SUCCESS"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    DEAD_LETTER = "DEAD_LETTER"


class ImportJobStatus(StrEnum):
    PREVIEW_READY = "PREVIEW_READY"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class FailedOrderCustomerResponseStatus(StrEnum):
    PENDING = "PENDING"
    RESPONDED = "RESPONDED"
    ESCALATED = "ESCALATED"


class OptimizationRunType(StrEnum):
    BATCH = "BATCH"
    INSERTION = "INSERTION"
    REOPTIMIZATION = "REOPTIMIZATION"
    WEEKLY_BATCH = "WEEKLY_BATCH"


class SolverStatusLabel(StrEnum):
    """Mirror of algorithm-layer SolverStatus for API/metrics legibility."""
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    GREEDY_FALLBACK = "GREEDY_FALLBACK"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


class SLAAlertSeverity(StrEnum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class SLAAlertStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class OptimizationRunStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    LOCKED = "LOCKED"
    NO_OP = "NO_OP"
    PENDING_APPROVAL = "PENDING_APPROVAL"


# Valid order state transitions (from → set of allowed destinations)
ORDER_STATE_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.DRAFT: {OrderStatus.PENDING, OrderStatus.CANCELLED},
    OrderStatus.PENDING: {OrderStatus.ASSIGNED, OrderStatus.CANCELLED, OrderStatus.EXPIRED},
    OrderStatus.ASSIGNED: {OrderStatus.PICKED_UP, OrderStatus.CANCELLED, OrderStatus.PENDING},
    OrderStatus.PICKED_UP: {OrderStatus.IN_TRANSIT},
    OrderStatus.IN_TRANSIT: {OrderStatus.DELIVERED, OrderStatus.FAILED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.FAILED: {OrderStatus.RESCHEDULED, OrderStatus.CANCELLED},
    OrderStatus.RESCHEDULED: {OrderStatus.PENDING},
    OrderStatus.CANCELLED: set(),
    OrderStatus.EXPIRED: {OrderStatus.RESCHEDULED},
}
