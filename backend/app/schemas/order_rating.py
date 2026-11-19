"""Pydantic schemas for the order-rating endpoint."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class OrderRatingCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="1 (worst) through 5 (best)")
    feedback_text: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional free-text feedback. Empty / NULL is fine.",
    )


class OrderRatingResponse(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    rating: int
    feedback_text: str | None
    captured_at: datetime

    model_config = {"from_attributes": True}
