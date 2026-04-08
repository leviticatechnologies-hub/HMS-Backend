"""
Clinical operations schemas for OPD, IPD, and nursing management.
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, EmailStr, field_validator, model_validator


# ============================================================================
# OPD INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class PatientRegistrationCreate(BaseModel):
    """Register new patient for OPD"""
    first_name: str
    last_name: str
    phone: str
    email: Optional[EmailStr] = None
    date_of_birth: str  # YYYY-MM-DD
    gender: str  # MALE, FEMALE, OTHER
    address: Optional[str] = None
    city: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    password: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        description=(
            "Optional. If set, patient can log in via POST /auth/patient/login with this email and password "
            "(same as online registration). Requires email."
        ),
    )

    @field_validator("password", mode="before")
    @classmethod
    def empty_password_to_none(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @model_validator(mode="after")
    def password_requires_email(self):
        if self.password and not self.email:
            raise ValueError("email is required when password is set so the patient can use patient login")
        return self

    send_credentials_email: bool = Field(
        default=True,
        description=(
            "If true (default), attempts to email portal login details after registration. "
            "Registration always saves even if SMTP is misconfigured or sending fails — check `credentials_email_sent` in the response."
        ),
    )


class AppointmentSchedulingCreate(BaseModel):
    """Schedule appointment for an existing patient (register via POST /receptionist/patients/register first)."""
    patient_ref: str = Field(..., min_length=1, description="Patient MRN / PAT-... from registration")
    doctor_name: str  # "Dr. John Smith"
    department_name: str  # "Cardiology"
    appointment_date: str  # YYYY-MM-DD
    appointment_time: str  # HH:MM
    appointment_type: str = "CONSULTATION"  # CONSULTATION, FOLLOW_UP, EMERGENCY
    chief_complaint: Optional[str] = None
    notes: Optional[str] = None


class AppointmentUpdate(BaseModel):
    """Modify existing appointment"""
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    doctor_name: Optional[str] = None
    department_name: Optional[str] = None
    chief_complaint: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None  # CONFIRMED, CANCELLED, RESCHEDULED


class PatientCheckInCreate(BaseModel):
    """Check-in patient for appointment"""
    appointment_ref: str
    arrival_time: Optional[str] = None  # HH:MM, defaults to current time
    notes: Optional[str] = None


# ============================================================================
# IPD INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class PatientAdmissionCreate(BaseModel):
    """Admit patient to IPD"""
    patient_ref: str
    admission_type: str = "IPD"  # IPD, EMERGENCY
    chief_complaint: str
    provisional_diagnosis: Optional[str] = None
    admission_notes: Optional[str] = None
    ward: Optional[str] = None
    room_number: Optional[str] = None
    bed_number: Optional[str] = None
    expected_length_of_stay: Optional[int] = None  # days


class BedAssignmentCreate(BaseModel):
    """Assign bed to patient"""
    admission_number: str
    ward: str
    room_number: str
    bed_number: str
    notes: Optional[str] = None


class TreatmentPlanCreate(BaseModel):
    """Create treatment plan for admitted patient"""
    admission_number: str
    treatment_objectives: List[str]
    medications: List[Dict[str, Any]]
    procedures: List[Dict[str, Any]]
    diet_instructions: Optional[str] = None
    activity_restrictions: Optional[str] = None
    monitoring_requirements: List[str]
    expected_outcomes: Optional[str] = None


class MedicationAdministrationCreate(BaseModel):
    """Record medication administration"""
    admission_number: str
    medication_name: str
    dosage: str
    route: str  # ORAL, IV, IM, SC
    administered_time: str  # HH:MM
    administered_by: Optional[str] = None  # Auto-filled from JWT
    patient_response: Optional[str] = None
    side_effects: Optional[str] = None
    notes: Optional[str] = None


class NursingAssessmentCreate(BaseModel):
    """Comprehensive nursing assessment"""
    admission_number: str
    assessment_type: str  # ADMISSION, DAILY, SHIFT_CHANGE, DISCHARGE
    general_condition: str  # STABLE, CRITICAL, IMPROVING, DETERIORATING
    consciousness_level: str  # ALERT, DROWSY, UNCONSCIOUS
    mobility_status: str  # AMBULATORY, BEDBOUND, ASSISTED
    pain_assessment: Dict[str, Any]  # {"level": 3, "location": "chest", "type": "sharp"}
    skin_condition: Optional[str] = None
    wound_assessment: Optional[List[Dict[str, Any]]] = None
    nutritional_status: Optional[str] = None
    elimination_status: Optional[Dict[str, str]] = None  # {"bowel": "normal", "bladder": "normal"}
    psychosocial_status: Optional[str] = None
    family_involvement: Optional[str] = None
    discharge_planning_needs: Optional[List[str]] = None
    nursing_interventions: List[str]
    goals_for_next_shift: Optional[List[str]] = None


class DoctorRoundsCreate(BaseModel):
    """Doctor rounds documentation"""
    admission_number: str
    round_type: str  # MORNING, EVENING, EMERGENCY, CONSULTATION
    patient_condition: str  # STABLE, CRITICAL, IMPROVING, DETERIORATING
    clinical_findings: str
    assessment_and_plan: str
    medication_changes: Optional[List[Dict[str, Any]]] = None
    new_orders: Optional[List[str]] = None
    follow_up_instructions: Optional[str] = None
    discharge_planning: Optional[str] = None
    family_discussion: Optional[str] = None


# ============================================================================
# NURSING INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class VitalSignsUpdate(BaseModel):
    """Update patient vital signs"""
    patient_ref: str
    blood_pressure_systolic: Optional[int] = None
    blood_pressure_diastolic: Optional[int] = None
    pulse_rate: Optional[int] = None
    temperature: Optional[float] = None  # Celsius
    respiratory_rate: Optional[int] = None
    oxygen_saturation: Optional[int] = None  # Percentage
    weight: Optional[float] = None  # kg
    height: Optional[float] = None  # cm
    pain_scale: Optional[int] = None  # 1-10 scale
    notes: Optional[str] = None


class NursingNoteCreate(BaseModel):
    """Create nursing note"""
    patient_ref: str
    note_type: str  # "ASSESSMENT", "INTERVENTION", "OBSERVATION", "MEDICATION_ADMIN", "DISCHARGE_PREP"
    note_content: str
    priority: Optional[str] = "NORMAL"  # "LOW", "NORMAL", "HIGH", "URGENT"
    follow_up_required: Optional[bool] = False


# ============================================================================
# OUTPUT SCHEMAS (Out/Response)
# ============================================================================

class OPDAppointmentOut(BaseModel):
    """OPD appointment response"""
    appointment_ref: str
    patient_ref: str
    patient_name: str
    doctor_name: str
    department_name: str
    appointment_date: str
    appointment_time: str
    appointment_type: str
    status: str
    chief_complaint: Optional[str]
    is_checked_in: bool
    checked_in_at: Optional[str]
    created_at: str


class OPDPatientOut(BaseModel):
    """OPD patient response"""
    patient_ref: str
    patient_name: str
    phone: str
    email: Optional[str]
    date_of_birth: str
    gender: str
    address: Optional[str]
    emergency_contact: Optional[Dict[str, str]]
    total_appointments: int
    last_appointment_date: Optional[str]
    registration_date: str


class IPDPatientOut(BaseModel):
    """IPD patient response"""
    patient_ref: str
    patient_name: str
    admission_number: str
    admission_date: str
    admission_type: str
    department_name: str
    attending_doctor: str
    assigned_nurse: Optional[str]
    ward: Optional[str]
    room_number: Optional[str]
    bed_number: Optional[str]
    current_condition: Optional[str]
    length_of_stay: int
    chief_complaint: str
    provisional_diagnosis: Optional[str]
    is_active: bool


class IPDAdmissionDetailsOut(BaseModel):
    """Detailed IPD admission information"""
    admission_number: str
    patient_ref: str
    patient_name: str
    patient_age: int
    patient_gender: str
    admission_date: str
    admission_type: str
    department_name: str
    attending_doctor: str
    chief_complaint: str
    provisional_diagnosis: Optional[str]
    admission_notes: Optional[str]
    ward: Optional[str]
    room_number: Optional[str]
    bed_number: Optional[str]
    length_of_stay: int
    current_condition: Optional[str]
    vital_signs_summary: Dict[str, Any]
    current_medications: List[Dict[str, Any]]
    recent_assessments: List[Dict[str, Any]]


class PatientProfileViewOut(BaseModel):
    """Patient profile view for nurses"""
    patient_ref: str
    patient_name: str
    date_of_birth: str
    gender: str
    blood_group: Optional[str]
    allergies: List[str]
    chronic_conditions: List[str]
    current_medications: List[str]
    emergency_contact: Optional[Dict[str, str]]
    admission_status: Optional[str]
    room_number: Optional[str]
    bed_number: Optional[str]
    attending_doctor: Optional[str]


class VitalSignsHistoryOut(BaseModel):
    """Vital signs history entry"""
    recorded_at: str
    recorded_by: str
    blood_pressure: Optional[str]
    pulse_rate: Optional[int]
    temperature: Optional[float]
    respiratory_rate: Optional[int]
    oxygen_saturation: Optional[int]
    weight: Optional[float]
    height: Optional[float]
    pain_scale: Optional[int]
    notes: Optional[str]


class NursingNoteOut(BaseModel):
    """Nursing note response"""
    note_id: str
    patient_ref: str
    patient_name: str
    note_type: str
    note_content: str
    priority: str
    follow_up_required: bool
    recorded_by: str
    recorded_at: str