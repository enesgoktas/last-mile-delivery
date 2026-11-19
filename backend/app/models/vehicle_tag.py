import uuid

import sqlalchemy as sa
from sqlalchemy import ForeignKey, String, Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Association: couriers ↔ vehicle_tags (many-to-many)
courier_vehicle_tags = Table(
    "courier_vehicle_tags",
    Base.metadata,
    sa.Column(
        "courier_id",
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "tag_id",
        UUID(as_uuid=True),
        ForeignKey("vehicle_tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

# Association: orders ↔ vehicle_tags (required tags)
order_required_vehicle_tags = Table(
    "order_required_vehicle_tags",
    Base.metadata,
    sa.Column(
        "order_id",
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "tag_id",
        UUID(as_uuid=True),
        ForeignKey("vehicle_tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

# Association: recurring_order_templates ↔ vehicle_tags
recurring_order_required_vehicle_tags = Table(
    "recurring_order_required_vehicle_tags",
    Base.metadata,
    sa.Column(
        "template_id",
        UUID(as_uuid=True),
        ForeignKey("recurring_order_templates.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "tag_id",
        UUID(as_uuid=True),
        ForeignKey("vehicle_tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class VehicleTag(Base):
    """Tenant-scoped tag catalog. Couriers are labeled with tags; orders require them."""

    __tablename__ = "vehicle_tags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="#6366f1")
