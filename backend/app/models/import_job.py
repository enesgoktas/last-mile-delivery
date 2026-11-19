import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.enums import ImportJobStatus
from app.utils.compat import utcnow_naive


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[ImportJobStatus] = mapped_column(
        SAEnum(ImportJobStatus),
        nullable=False,
        default=ImportJobStatus.PREVIEW_READY,
        index=True,
    )
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    invalid_details: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
