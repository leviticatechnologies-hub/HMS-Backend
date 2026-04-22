"""
Schemas for Sample Tracking screen + barcode scan flow.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


SampleStatus = Literal["COLLECTED", "IN_TRANSIT", "IN_LAB", "PROCESSED", "COMPLETED"]
SampleAction = Literal["MARK_COLLECTED", "MARK_IN_TRANSIT", "START_PROCESSING", "COMPLETE_TEST"]


class SampleTrackingRow(BaseModel):
    barcode: str
    test_id: str
    patient_name: str
    test_type: str
    sample_type: str
    collection_time: str
    status: SampleStatus
    current_location: str


class SampleTrackingMeta(BaseModel):
    generated_at: datetime
    live_data: bool = False
    demo_data: bool = False


class SampleTrackingListResponse(BaseModel):
    meta: SampleTrackingMeta
    rows: List[SampleTrackingRow] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class BarcodeLookupResponse(BaseModel):
    found: bool
    sample: Optional[SampleTrackingRow] = None
    message: str


class SampleActionRequest(BaseModel):
    action: SampleAction
    barcode: str
    location: Optional[str] = None


class SampleActionResponse(BaseModel):
    message: str
    barcode: str
    status: SampleStatus
    current_location: str

