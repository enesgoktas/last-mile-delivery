from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    action: str
    resource_type: str | None
    resource_id: str | None
    details: dict[str, Any] | None
    ip_address: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
