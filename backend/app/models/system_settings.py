import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    global_constraints: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")
    operations_policy: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow_naive, onupdate=utcnow_naive, nullable=False)
