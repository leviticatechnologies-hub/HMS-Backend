"""
Schemas for Lab Profile screen.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class LabProfileStats(BaseModel):
    total_tests: int = 0
    total_staff: int = 0
    equipment: int = 0
    branches: int = 0


class LabInfoBlock(BaseModel):
    lab_id: str
    lab_name: str
    lab_type: str
    registration_number: str
    established_date: str
    accreditation: str
    accreditation_number: Optional[str] = None


class ContactInfoBlock(BaseModel):
    address: str
    city: str
    state: str
    pincode: str
    phone: str
    emergency_phone: Optional[str] = None
    email: str
    website: Optional[str] = None


class FacilitiesBlock(BaseModel):
    total_area_sqft: int
    departments: List[str] = Field(default_factory=list)
    specialties: List[str] = Field(default_factory=list)
    rooms: List[str] = Field(default_factory=list)


class UserProfileBlock(BaseModel):
    name: str
    role: str
    email: str
    phone: str
    department: str
    joined: str
    last_login: str
    status: str


class OperationalHoursBlock(BaseModel):
    working_hours: str
    weekdays: str
    sunday: str
    emergency: str
    home_collection: str
    report_delivery: str


class ServicesBlock(BaseModel):
    sample_types: List[str] = Field(default_factory=list)
    routine_tat: str
    urgent_tat: str
    stat_tat: str


class LabSettingsBlock(BaseModel):
    auto_print_reports: bool = True
    email_notifications: bool = True
    sms_notifications: bool = True
    report_template: str = "Standard"


class LabProfileMeta(BaseModel):
    generated_at: datetime
    live_data: bool = False
    demo_data: bool = False


class LabProfileResponse(BaseModel):
    meta: LabProfileMeta
    stats: LabProfileStats
    lab_information: LabInfoBlock
    contact_information: ContactInfoBlock
    facilities: FacilitiesBlock
    user_profile: UserProfileBlock
    operational_hours: OperationalHoursBlock
    services: ServicesBlock
    settings: LabSettingsBlock

    model_config = ConfigDict(from_attributes=True)


class EditLabProfileRequest(BaseModel):
    lab_name: str = Field(..., min_length=2, max_length=180)
    lab_type: str = Field(..., min_length=2, max_length=120)
    registration_number: str = Field(..., min_length=2, max_length=80)
    established_date: str = Field(..., min_length=8, max_length=20)
    accreditation: str = Field(..., min_length=2, max_length=120)
    accreditation_number: Optional[str] = Field(None, max_length=120)


class EditLabProfileResponse(BaseModel):
    message: str
    updated_lab_name: str


class ConfigureLabSettingsRequest(BaseModel):
    auto_print_reports: bool
    email_notifications: bool
    sms_notifications: bool
    report_template: str = Field(..., min_length=2, max_length=80)


class ConfigureLabSettingsResponse(BaseModel):
    message: str
    settings: LabSettingsBlock


class ChangePasswordResponse(BaseModel):
    message: str


class LabProfileActionResponse(BaseModel):
    message: str
    action: str

