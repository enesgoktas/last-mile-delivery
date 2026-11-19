import secrets
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import UserRole
from app.utils.compat import utcnow_naive

# Verification tokens are URL-safe random hex strings (256-bit entropy)
EMAIL_VERIFICATION_TOKEN_BYTES = 32


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", "tenant_id", name="uq_users_email_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)

    # ── Email verification (8.6) ──────────────────────────────────────
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verification_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="users")
    courier: Mapped["Courier | None"] = relationship("Courier", back_populates="user", uselist=False)

    def generate_verification_token(self) -> str:
        """Generate and store a fresh email verification token; returns the raw token."""
        raw = secrets.token_urlsafe(EMAIL_VERIFICATION_TOKEN_BYTES)
        self.email_verification_token = raw
        self.email_verification_sent_at = utcnow_naive()
        return raw
