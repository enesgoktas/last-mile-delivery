import uuid
from datetime import datetime

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.utils.compat import utcnow_naive


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)

    # ── Branding (8.4) ────────────────────────────────────────────────
    # Optional white-label customization applied to the customer-facing
    # tracking page and outbound notification templates.
    logo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    primary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)   # hex e.g. "#1a73e8"
    secondary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)  # hex e.g. "#fbbc04"

    # Relationships
    users: Mapped[list["User"]] = relationship("User", back_populates="tenant")
