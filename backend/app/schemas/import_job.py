import uuid
from datetime import datetime

from pydantic import BaseModel


class ImportRowError(BaseModel):
    row_number: int
    errors: list[str]
    raw: dict


class ImportPreviewResponse(BaseModel):
    import_id: uuid.UUID
    total_rows: int
    valid_rows: int
    invalid_rows: int
    valid_preview: list[dict]
    errors: list[ImportRowError]


class ImportPublishResponse(BaseModel):
    import_id: uuid.UUID
    created_count: int
    created_order_ids: list[uuid.UUID]
    published_at: datetime
