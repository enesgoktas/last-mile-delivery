import sqlalchemy as sa
from sqlalchemy import ForeignKey, Table
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base

# Association: couriers ↔ depots (many-to-many).
#
# A courier's primary / home depot stays on `couriers.depot_id`. This
# table holds *additional* depots a courier is allowed to reload from
# mid-shift. Empty rows = single-depot, current behavior. Populated
# only when the tenant has `feature_flags.multi_depot_vehicles_enabled`
# (the API rejects writes when the flag is off).
courier_depots = Table(
    "courier_depots",
    Base.metadata,
    sa.Column(
        "courier_id",
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "depot_id",
        UUID(as_uuid=True),
        ForeignKey("depots.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
