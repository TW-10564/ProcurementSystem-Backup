"""Pydantic request/response contracts for the API layer."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

# Every timestamp this API emits is timezone-aware UTC — see onefcst/timeutil.
from ..timeutil import UtcDatetime


class CompatRuleOut(BaseModel):
    id: int
    part_a_id: int
    part_a_name: str
    part_b_id: int
    part_b_name: str
    relation: str
    confidence: float
    origin: str
    status: str
    confirmed_by: str | None = None
    note: str | None = None

    class Config:
        from_attributes = True


class DiscontinuationSuggestionOut(BaseModel):
    id: int
    item_id: int | None = None
    item_name: str | None = None
    category: str | None = None
    source_category_file: str | None = None
    detected_at: str | None = None
    status: str
    reviewed_by: str | None = None

    class Config:
        from_attributes = True


class OrderActionOut(BaseModel):
    id: int
    item_id: int
    status: str
    qty_ordered: float | None = None
    expected_arrival_date: date | None = None
    note: str | None = None
    ordered_by: str | None = None
    ordered_at: UtcDatetime | None = None
    received_by: str | None = None
    received_at: UtcDatetime | None = None

    class Config:
        from_attributes = True


class MarkOrderedRequest(BaseModel):
    author: str = Field(..., min_length=1)
    qty_ordered: float | None = None
    expected_arrival_date: date | None = None
    note: str = ""


class ExtendDateRequest(BaseModel):
    author: str = Field(..., min_length=1)
    expected_arrival_date: date


class ConfirmRequest(BaseModel):
    author: str = Field(..., min_length=1)


class RejectRequest(BaseModel):
    author: str = Field(..., min_length=1)
    reason: str = ""


class EditRuleRequest(BaseModel):
    author: str = Field(..., min_length=1)
    relation: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str | None = None


class AdjustmentIn(BaseModel):
    item_id: int
    month: date
    old_value: float | None = None
    new_value: float
    scope: str = Field(..., pattern="^(global|category|sku)$")
    reason: str = Field(..., min_length=1)
    author: str = Field(..., min_length=1)
    factor: float | None = None


class AdjustmentOut(BaseModel):
    id: int
    item_id: int | None
    item_name: str | None = None       # populated by the endpoint, not a DB column
    month: date
    old_value: float | None
    new_value: float
    scope: str
    reason: str
    author: str
    created_at: UtcDatetime

    class Config:
        from_attributes = True


class NotificationOut(BaseModel):
    id: int
    who: str | None
    what: str
    at: str
