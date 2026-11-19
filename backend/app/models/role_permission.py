"""RolePermission model — per-tenant, per-role permission overrides (Phase 8.2)."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class RolePermission(Base):
    """Overrides a single permission for a role within a tenant.

    A row with ``granted=True`` explicitly grants the permission.
    A row with ``granted=False`` explicitly revokes it.
    Missing rows fall back to the global default matrix defined in
    ``app.services.rbac_service.DEFAULT_PERMISSIONS``.
    """
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "role", "permission", name="uq_role_permissions"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    permission: Mapped[str] = mapped_column(String(128), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False
    )
