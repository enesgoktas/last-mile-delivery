"""OIDCProvider model — stores per-tenant SSO/OIDC configuration (Phase 8.1)."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class OIDCProvider(Base):
    """One row per tenant; stores the OIDC identity provider settings.

    The ``client_secret`` is stored encrypted at the application layer.
    Consider using ``WEBHOOK_SECRET_ENCRYPTION_KEY`` or a dedicated key.
    """
    __tablename__ = "oidc_providers"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_oidc_providers_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issuer: Mapped[str] = mapped_column(String(2048), nullable=False)
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    # Encrypted value — decrypt before use
    client_secret_enc: Mapped[str] = mapped_column(String(2048), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    scopes: Mapped[str] = mapped_column(String(512), default="openid email profile", nullable=False)
    # Name of the JWT claim that carries the user's role (e.g. "lmd_role", "roles")
    role_claim: Mapped[str] = mapped_column(String(128), default="lmd_role", nullable=False)
    # Fallback role when no role claim is present
    default_role: Mapped[str] = mapped_column(String(64), default="DISPATCHER", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive,
        onupdate=utcnow_naive,
        nullable=False,
    )
