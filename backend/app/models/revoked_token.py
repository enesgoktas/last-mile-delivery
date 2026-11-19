from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
