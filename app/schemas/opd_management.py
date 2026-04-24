"""Request/response schemas for OPD management APIs."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# --- Patient / visit ---
class OpdPatientCreate(BaseModel):
    patient_ref: Optional[str] = Field(
        None,
        description="Hospital patient id e.g. PAT-001 (preferred)",
    )
    patient_profile_id: Optional[UUID] = Field(None, description="Alternative to patient_ref")

    visit_type: str = Field(default="NEW", description="NEW, REGULAR, EMERGENCY, FOLLOW_UP")
    priority: str = Field(default="NORMAL", description="NORMAL, URGENT")
    department_name: Optional[str] = None
    doctor_user_id: Optional[UUID] = Field(None, description="Assign to doctor queue")
    appointment_id: Optional[UUID] = None
    arrival_time: Optional[datetime] = None


class OpdTokenCreate(BaseModel):
    patientId: Optional[str] = None
    patientName: Optional[str] = None
    phoneNo: Optional[str] = None
    email: Optional[str] = None
    age: Optional[int] = None
    gender: str = "Male"
    bloodGroup: Optional[str] = None
    Type: Optional[str] = None
    address: Optional[str] = None
    department: Optional[str] = None
    doctorId: Optional[UUID] = None
    type: str = "Regular"
    priority: str = "Normal"


class OpdStatusUpdate(BaseModel):
    status: str = Field(
        ...,
        description="WAITING, IN_CONSULTATION, COMPLETED, CANCELLED",
    )


# --- Consultation ---
class OpdConsultationCreate(BaseModel):
    opd_visit_id: UUID
    consultation_type: str = Field(default="NEW", description="NEW or FOLLOW_UP")
    symptoms: Optional[str] = None
    diagnosis: Optional[str] = None
    prescription: Optional[str] = None
    tests_recommended: List[Any] = Field(default_factory=list)
    remarks: Optional[str] = None
    next_visit_date: Optional[date] = None


class OpdVitalsCreate(BaseModel):
    bp: Optional[str] = None
    pulse: Optional[int] = None
    temperature: Optional[float] = None
    spo2: Optional[int] = None
    weight: Optional[float] = None
    height: Optional[float] = None


class OpdConsultationWithVitalsCreate(OpdConsultationCreate):
    doctor_user_id: Optional[UUID] = Field(
        None,
        description="Defaults to OPD visit doctor if omitted",
    )
    vitals: Optional[OpdVitalsCreate] = None


class OpdConsultationStart(BaseModel):
    patientId: str
    doctorId: UUID
    consultationType: str = "New"
    vitalSigns: Optional[OpdVitalsCreate] = None
    symptoms: Optional[str] = None
    history: Optional[str] = None
    allergies: Optional[str] = None
    examination: Optional[str] = None


class OpdConsultationComplete(BaseModel):
    diagnosis: Optional[str] = None
    prescription: Optional[str] = None
    testsRecommended: List[Any] = Field(default_factory=list)
    instructions: Optional[str] = None
    nextVisitDate: Optional[date] = None
    remarks: Optional[str] = None


# --- Doctor OPD settings (POST /opd/doctor) ---
class OpdDoctorConfigure(BaseModel):
    doctor_user_id: UUID
    opd_room: Optional[str] = Field(None, max_length=50)
    max_patients_per_day: Optional[int] = Field(None, ge=1, le=500)
    working_hours: Optional[str] = Field(None, max_length=200)


# --- Transfer ---
class OpdTransferCreate(BaseModel):
    opd_visit_id: UUID
    to_doctor_user_id: UUID
    reason: Optional[str] = Field(None, max_length=500)


class OpdTransferModal(BaseModel):
    patientId: str
    fromDoctorId: Optional[UUID] = None
    toDoctorId: UUID


class OpdDoctorCreate(BaseModel):
    name: str
    department: str
    opdRoom: Optional[str] = None
    specialization: str
    qualification: Optional[str] = None
    email: str
    contact: str
    isActive: bool = True


class OpdDoctorUpdate(BaseModel):
    name: Optional[str] = None
    department: Optional[str] = None
    opdRoom: Optional[str] = None
    specialization: Optional[str] = None
    qualification: Optional[str] = None
    email: Optional[str] = None
    contact: Optional[str] = None
    isActive: Optional[bool] = None


class OpdDoctorDeactivate(BaseModel):
    doctorId: UUID
    reassignToDoctorIds: List[UUID] = Field(default_factory=list)
