from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class TrafficCellProfile(Base):
    __tablename__ = "traffic_cell_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    geohash6: Mapped[str] = mapped_column(String(6), nullable=False)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # Monday=0 .. Sunday=6
    hour_bucket: Mapped[int] = mapped_column(Integer, nullable=False)  # 0..23
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ema_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
    )

    __table_args__ = (
        UniqueConstraint(
            "geohash6",
            "day_of_week",
            "hour_bucket",
            name="uq_traffic_cell_profiles_cell_time",
        ),
        Index(
            "ix_traffic_cell_profiles_lookup",
            "geohash6",
            "day_of_week",
            "hour_bucket",
        ),
    )
