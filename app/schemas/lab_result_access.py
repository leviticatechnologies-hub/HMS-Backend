"""
Schemas for Secure Result Access screen.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


AccessStatus = Literal["ACTIVE", "EXPIRED", "REVOKED"]
AccessType = Literal["VIEW_ONLY", "DOWNLOAD", "SHARE"]


class ResultAccessStatCards(BaseModel):
    active_access: int = 0
    doctor_access: int = 0
    todays_accesses: int = 0
    mobile_accesses: int = 0


class ResultAccessPatientRow(BaseModel):
    patient_ref: str = Field(
        validation_alias=AliasChoices("patient_ref", "patient_id"),
        serialization_alias="patient_ref",
    )
    patient_name: str
    email: str
    phone: str
    last_access: str
    access_count: int
    status: AccessStatus


class ResultAccessLogRow(BaseModel):
    patient_name: str
    accessed_by: str
    access_time: str
    action: str
    ip_address: str
    device_browser: str


class ResultAccessMeta(BaseModel):
    generated_at: datetime
    live_data: bool = False
    demo_data: bool = False


class ResultAccessDashboardResponse(BaseModel):
    meta: ResultAccessMeta
    stats: ResultAccessStatCards
    patients: List[ResultAccessPatientRow] = Field(default_factory=list)
    access_logs: List[ResultAccessLogRow] = Field(default_factory=list)
    security_features: List[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GrantResultAccessRequest(BaseModel):
    patient_ref: str = Field(
        ...,
        min_length=3,
        max_length=40,
        validation_alias=AliasChoices("patient_ref", "patient_id"),
        serialization_alias="patient_ref",
    )
    email: str = Field(..., min_length=5, max_length=255)
    access_type: AccessType = "VIEW_ONLY"
    expiry_date: Optional[str] = None


class GrantResultAccessResponse(BaseModel):
    message: str
    patient_ref: str = Field(
        validation_alias=AliasChoices("patient_ref", "patient_id"),
        serialization_alias="patient_ref",
    )
    email: str
    access_type: AccessType
    expiry_date: Optional[str] = None
    access_code: str

    model_config = ConfigDict(populate_by_name=True)

