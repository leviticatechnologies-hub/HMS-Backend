"""Schemas for Doctor Portal sidebar modules (prescriptions, lab, IPD, messaging, profile)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class DoctorPrescriptionSummaryOut(BaseModel):
    prescription_id: str
    prescription_number: str
    patient_ref: str
    patient_name: str
    prescription_date: str
    diagnosis: Optional[str] = None
    total_medicines: int = 0
    is_dispensed: bool = False
    created_at: str


class DoctorLabResultItemOut(BaseModel):
    medical_record_id: str
    patient_ref: str
    patient_name: str
    recorded_at: Optional[str] = None
    lab_orders: List[Any] = Field(default_factory=list)


class DoctorInpatientVisitOut(BaseModel):
    admission_id: str
    admission_number: str
    patient_ref: str
    patient_name: str
    admission_date: str
    admission_type: str
    status: str
    ward: Optional[str] = None
    room_number: Optional[str] = None
    bed_number: Optional[str] = None
    chief_complaint: str
    is_active: bool


class DoctorMessageOut(BaseModel):
    id: str
    source: Literal["telemed", "prescription"]
    title: Optional[str] = None
    body: Optional[str] = None
    event_type: Optional[str] = None
    read_at: Optional[str] = None
    created_at: str


class DoctorMessageReadRequest(BaseModel):
    source: Literal["telemed", "prescription"]
    message_id: UUID
    message_id: str


class DoctorProfileOut(BaseModel):
    user_id: str
    doctor_profile_id: str
    email: str
    phone: str
    first_name: str
    last_name: str
    staff_id: Optional[str] = None
    department: Optional[str] = None
    specialization: Optional[str] = None
    designation: Optional[str] = None
    qualifications: List[Any] = Field(default_factory=list)
    consultation_fee: Optional[float] = None
    availability_time: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None


class DoctorProfileUpdate(BaseModel):
    phone: Optional[str] = Field(None, max_length=20)
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    bio: Optional[str] = None
    avatar_url: Optional[str] = Field(None, max_length=500)
