from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class TrafficCellPrior(Base):
    """Historical traffic multipliers derived from external open-data sources.

    Acts as a prior/fallback for `TrafficCellProfile`: when the fleet has not
    yet accumulated enough observations for a cell+bucket, the routing
    pipeline falls back to this table. Keyed by region_code so additional
    cities can be added without schema changes.
    """

    __tablename__ = "traffic_cell_priors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region_code: Mapped[str] = mapped_column(String(8), nullable=False)
    geohash6: Mapped[str] = mapped_column(String(6), nullable=False)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # Monday=0..Sunday=6
    hour_bucket: Mapped[int] = mapped_column(Integer, nullable=False)  # 0..23
    prior_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    bucket_mean_speed_kmh: Mapped[float] = mapped_column(Float, nullable=False)
    bucket_vehicle_samples: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0,
    )
    computed_free_flow_kmh: Mapped[float] = mapped_column(Float, nullable=False)
    is_extrapolated_free_flow: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    source_name: Mapped[str] = mapped_column(String(32), nullable=False, default="IBB")
    source_first_seen: Mapped[date] = mapped_column(Date, nullable=False)
    source_last_seen: Mapped[date] = mapped_column(Date, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive,
    )

    __table_args__ = (
        UniqueConstraint(
            "region_code",
            "geohash6",
            "day_of_week",
            "hour_bucket",
            name="uq_traffic_cell_priors",
        ),
        Index(
            "ix_traffic_cell_priors_region_cell_time",
            "region_code",
            "geohash6",
            "day_of_week",
            "hour_bucket",
        ),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_traffic_cell_priors_dow"),
        CheckConstraint("hour_bucket BETWEEN 0 AND 23", name="ck_traffic_cell_priors_hour"),
        CheckConstraint(
            "prior_multiplier BETWEEN 0.6 AND 1.8",
            name="ck_traffic_cell_priors_multiplier_range",
        ),
    )
