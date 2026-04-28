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


class DoctorAppointmentOut(BaseModel):
    appointment_ref: str
    patient_ref: str
    patient_name: str
    appointment_date: str
    appointment_time: str
    appointment_type: Optional[str] = None
    status: str
    chief_complaint: Optional[str] = None
    notes: Optional[str] = None


class DoctorPrescriptionCreateRequest(BaseModel):
    patient: str
    medicine: str
    dosage: str
    frequency: str
    duration: str
    instructions: Optional[str] = None
    date: str


class DoctorLabReviewRequest(BaseModel):
    status: str = Field(default="REVIEWED", description="REVIEWED or CRITICAL")
    notes: Optional[str] = None


class DoctorInpatientVitalsUpdate(BaseModel):
    bloodPressure: Optional[str] = None
    heartRate: Optional[str] = None
    temperature: Optional[str] = None
    oxygenSaturation: Optional[str] = None


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
    message_id: str


class DoctorMessageCreateRequest(BaseModel):
    recipient_user_id: str
    title: str
    body: str
    event_type: str = "NEW_MESSAGE"


class DoctorProfileOut(BaseModel):
    user_id: str
    doctor_profile_id: str
    hospital_id: Optional[str] = None
    email: str
    phone: str
    first_name: str
    last_name: str
    middle_name: Optional[str] = None
    staff_id: Optional[str] = None
    status: Optional[str] = None
    email_verified: bool = False
    phone_verified: bool = False
    avatar_url: Optional[str] = None
    timezone: Optional[str] = None
    language: Optional[str] = None
    user_metadata: Any = Field(default_factory=dict)
    doctor_id: Optional[str] = None
    medical_license_number: Optional[str] = None
    department_id: Optional[str] = None
    department: Optional[str] = None
    specialization: Optional[str] = None
    sub_specialization: Optional[str] = None
    designation: Optional[str] = None
    experience_years: Optional[int] = None
    qualifications: List[Any] = Field(default_factory=list)
    certifications: List[Any] = Field(default_factory=list)
    medical_associations: List[Any] = Field(default_factory=list)
    consultation_fee: Optional[float] = None
    follow_up_fee: Optional[float] = None
    consultation_type: Optional[str] = None
    availability_time: Optional[str] = None
    is_available_for_emergency: Optional[bool] = None
    is_accepting_new_patients: Optional[bool] = None
    languages_spoken: List[Any] = Field(default_factory=list)
    bio: Optional[str] = None


class DoctorProfileUpdate(BaseModel):
    phone: Optional[str] = Field(None, max_length=20)
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    middle_name: Optional[str] = Field(None, max_length=100)
    bio: Optional[str] = None
    avatar_url: Optional[str] = Field(None, max_length=500)
    timezone: Optional[str] = Field(None, max_length=50)
    language: Optional[str] = Field(None, max_length=10)
    specialization: Optional[str] = Field(None, max_length=255)
    sub_specialization: Optional[str] = Field(None, max_length=255)
    designation: Optional[str] = Field(None, max_length=100)
    availability_time: Optional[str] = None
    consultation_type: Optional[str] = Field(None, max_length=100)
    consultation_fee: Optional[float] = None
    follow_up_fee: Optional[float] = None
    is_available_for_emergency: Optional[bool] = None
    is_accepting_new_patients: Optional[bool] = None
    qualifications: Optional[List[Any]] = None
    certifications: Optional[List[Any]] = None
    medical_associations: Optional[List[Any]] = None
    languages_spoken: Optional[List[Any]] = None
