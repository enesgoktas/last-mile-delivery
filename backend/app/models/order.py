import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import (
    FailedOrderCustomerResponseStatus,
    FailureCauseCategory,
    OrderPriority,
    OrderStatus,
    OrderType,
)
from app.utils.compat import utcnow_naive


def _parse_preferred_courier_id(custom_fields: dict | None) -> uuid.UUID | None:
    if not isinstance(custom_fields, dict):
        return None
    raw_value = custom_fields.get("preferred_courier_id")
    if raw_value in (None, ""):
        return None
    try:
        return uuid.UUID(str(raw_value))
    except (TypeError, ValueError):
        return None


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    type: Mapped[OrderType] = mapped_column(SAEnum(OrderType), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus), default=OrderStatus.PENDING, nullable=False)
    priority: Mapped[OrderPriority] = mapped_column(SAEnum(OrderPriority), default=OrderPriority.NORMAL, nullable=False)

    # Addresses
    pickup_address: Mapped[str] = mapped_column(String(500), nullable=False)
    pickup_lat: Mapped[float] = mapped_column(Float, nullable=False)
    pickup_lon: Mapped[float] = mapped_column(Float, nullable=False)
    delivery_address: Mapped[str] = mapped_column(String(500), nullable=False)
    delivery_lat: Mapped[float] = mapped_column(Float, nullable=False)
    delivery_lon: Mapped[float] = mapped_column(Float, nullable=False)

    # Package info
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    is_fragile: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_temp_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Time windows
    earliest_pickup: Mapped[datetime | None] = mapped_column(nullable=True)
    latest_pickup: Mapped[datetime | None] = mapped_column(nullable=True)
    earliest_delivery: Mapped[datetime] = mapped_column(nullable=False)
    latest_delivery: Mapped[datetime] = mapped_column(nullable=False)

    # Scheduled delivery date (set on assignment, cleared on unassign).
    # When PENDING this stays NULL and we filter by `earliest_delivery`'s
    # calendar date; once a route picks the order up — possibly on a
    # different day than originally requested — this records the actual
    # day so the orders list shows it on the correct day.  Indexed because
    # the orders list filters by it for ASSIGNED/IN_TRANSIT views.
    scheduled_for_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True
    )

    # Depot
    depot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("depots.id", ondelete="SET NULL"), nullable=True
    )

    # Assignment
    assigned_courier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("couriers.id", ondelete="SET NULL"), nullable=True
    )
    route_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("routes.id", ondelete="SET NULL"), nullable=True
    )
    return_of_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Customer
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Tracking
    tracking_token: Mapped[str] = mapped_column(String(1000), nullable=False)
    delivery_otp_code: Mapped[str | None] = mapped_column(String(12), nullable=True)

    # Integration
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    custom_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Delivery attempts
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    failed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Layer 3 — exception taxonomy. The flat `RouteStop.failure_reason`
    # remains for stop-level UX; this typed (category, subcategory) pair
    # is the trend-analysis surface ("your top 3 failure causes this
    # month are X, Y, Z"). `subcategory` is free text scoped by category
    # — keep flexible until the pilot tells us the real failure mix.
    failure_cause_category: Mapped[FailureCauseCategory | None] = mapped_column(
        SAEnum(FailureCauseCategory), nullable=True, index=True
    )
    failure_cause_subcategory: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    customer_response_status: Mapped[FailedOrderCustomerResponseStatus | None] = mapped_column(
        SAEnum(FailedOrderCustomerResponseStatus),
        nullable=True,
        index=True,
    )
    customer_response_due_at: Mapped[datetime | None] = mapped_column(nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Notes
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Phase 1 ride-mode fields (only meaningful when type == RIDE) ──
    # Defaulted to 1 to keep existing delivery rows valid; passenger
    # orders override it from the create payload. Validation lives in
    # `app/schemas/order.py`.
    passenger_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    passenger_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    passenger_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    flight_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    special_requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    pickup_buffer_minutes_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service_time_pickup_seconds_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When a large RIDE is split across multiple vehicles, every
    # resulting sibling shares this id (= first sibling's order id).
    # Standalone orders have it NULL. Frontend uses this to group
    # siblings under a single passenger-name header on the rides list.
    ride_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow_naive, onupdate=utcnow_naive, nullable=False)

    # Relationships
    required_vehicle_tags: Mapped[list["VehicleTag"]] = relationship(
        "VehicleTag",
        secondary="order_required_vehicle_tags",
        lazy="selectin",
    )
    depot: Mapped["Depot | None"] = relationship("Depot", back_populates="orders")
    assigned_courier: Mapped["Courier"] = relationship("Courier", back_populates="orders")
    route: Mapped["Route"] = relationship("Route", back_populates="orders")
    route_stops: Mapped[list["RouteStop"]] = relationship("RouteStop", back_populates="order")
    legs: Mapped[list["OrderLeg"]] = relationship(
        "OrderLeg",
        back_populates="order",
        cascade="all, delete-orphan",
    )
    packages: Mapped[list["Package"]] = relationship(
        "Package", back_populates="order", cascade="all, delete-orphan"
    )
    proof_of_delivery: Mapped["ProofOfDelivery"] = relationship("ProofOfDelivery", back_populates="order", uselist=False)
    status_history: Mapped[list["OrderStatusHistory"]] = relationship(
        "OrderStatusHistory",
        back_populates="order",
        order_by="OrderStatusHistory.created_at",
        cascade="all, delete-orphan",
    )

    @property
    def preferred_courier_id(self) -> uuid.UUID | None:
        return _parse_preferred_courier_id(self.custom_fields)
