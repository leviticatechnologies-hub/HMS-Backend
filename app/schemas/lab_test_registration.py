"""
Schemas for Lab Test Registration screen.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


TestStatus = Literal["SAMPLE_PENDING", "SAMPLE_COLLECTED", "IN_PROGRESS", "COMPLETED"]
PriorityType = Literal["URGENT", "ROUTINE"]


class TestRegistrationRow(BaseModel):
    test_id: str
    patient_name: str
    test_type: str
    sample_type: str
    registered_date: date
    status: TestStatus
    priority: PriorityType


class TestRegistrationSummary(BaseModel):
    total_tests_today: int = 0
    completed_tests: int = 0
    in_progress_tests: int = 0
    urgent_tests: int = 0


class TestRegistrationMeta(BaseModel):
    generated_at: datetime
    for_date: date
    live_data: bool = False
    demo_data: bool = False


class TestRegistrationListResponse(BaseModel):
    meta: TestRegistrationMeta
    summary: TestRegistrationSummary
    rows: List[TestRegistrationRow] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class RegisterTestRequest(BaseModel):
    patient_ref: Optional[str] = Field(
        None,
        min_length=2,
        max_length=80,
        validation_alias=AliasChoices("patient_ref", "patient_id"),
        description="Hospital patient identifier from search box.",
    )
    patient_name: str = Field(..., min_length=2, max_length=120)
    test_type: str = Field(..., min_length=2, max_length=120)
    sample_type: str = Field(..., min_length=2, max_length=40)
    priority: PriorityType = "ROUTINE"
    referring_doctor: Optional[str] = Field(None, max_length=120)
    special_instructions: Optional[str] = Field(None, max_length=1000)


class RegisterTestResponse(BaseModel):
    message: str
    test_id: str
    status: TestStatus
    patient_ref: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("patient_ref", "patient_id"),
        serialization_alias="patient_ref",
    )
    patient_name: str
    test_type: str
    sample_type: str
    priority: PriorityType
    referring_doctor: Optional[str] = None
    special_instructions: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)

