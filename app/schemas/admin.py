"""
Admin schemas for super admin and hospital admin operations.
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


# ============================================================================
# SUPER ADMIN — MY PROFILE (UI: Personal, Security tabs)
# ============================================================================

class SuperAdminSessionOut(BaseModel):
    """Active login session row for the Security tab (populated when stored in user metadata)."""

    id: str
    device_name: Optional[str] = None
    browser: Optional[str] = None
    location: Optional[str] = None
    ip_address: Optional[str] = None
    is_current: bool = False
    last_active_at: Optional[str] = Field(
        None, description="ISO-8601 or human-readable label from client"
    )


class SuperAdminSecurityOut(BaseModel):
    """Security preferences + 2FA state for Super Admin profile settings UI."""

    is_two_factor_enabled: bool = Field(
        ..., description=" Mirrors TOTP enrollment; use /api/v1/auth/2fa/* to enable or disable."
    )
    enable_login_alerts: bool = True
    enable_suspicious_activity_alerts: bool = True
    inactivity_timeout_minutes: int = Field(30, ge=5, le=24 * 60)
    enable_account_auto_lock: bool = True
    active_sessions: List[SuperAdminSessionOut] = Field(
        default_factory=list,
        description="Session list when provided via metadata; empty until device sessions are tracked.",
    )


class SuperAdminMeOut(BaseModel):
    """Full Super Admin profile payload aligned with the dashboard settings screens."""

    first_name: str
    last_name: str
    full_name: str
    email: str
    phone_number: str
    profile_picture_url: Optional[str] = None
    middle_name: Optional[str] = None
    timezone: Optional[str] = None
    language: Optional[str] = None
    security: SuperAdminSecurityOut


class SuperAdminSecurityPreferencesUpdate(BaseModel):
    """Partial update for Security tab toggles (does not replace /auth/2fa for TOTP)."""

    enable_login_alerts: Optional[bool] = None
    enable_suspicious_activity_alerts: Optional[bool] = None
    inactivity_timeout_minutes: Optional[int] = Field(None, ge=5, le=24 * 60)
    enable_account_auto_lock: Optional[bool] = None


class SuperAdminMeUpdate(BaseModel):
    """Update Super Admin profile: personal fields and optional security preferences."""

    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = Field(
        None,
        max_length=20,
        description="Phone shown in UI; empty string clears display (stored as empty).",
    )
    profile_picture_url: Optional[str] = Field(None, max_length=500)
    middle_name: Optional[str] = Field(None, max_length=100)
    timezone: Optional[str] = Field(None, max_length=50)
    language: Optional[str] = Field(None, max_length=10)
    security: Optional[SuperAdminSecurityPreferencesUpdate] = None


class SuperAdminPasswordChange(BaseModel):
    """Change password (matches profile UI: current, new, confirm)."""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str = Field(..., min_length=8, max_length=128)

    @model_validator(mode="after")
    def _passwords_match(self):
        if self.new_password != self.confirm_password:
            raise ValueError("New password and confirmation must match")
        return self


# ============================================================================
# SUPER ADMIN INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class HospitalUpdate(BaseModel):
    """Hospital update request (all fields optional; send only what changes)."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(
        None,
        description="Contact phone (displayed as Contact in hospital lists)",
        pattern=r"^\+?[\d\s\-\(\)]{10,20}$",
    )
    address: Optional[str] = Field(None, min_length=5)
    city: Optional[str] = Field(None, min_length=2, max_length=100)
    state: Optional[str] = Field(None, min_length=2, max_length=100)
    country: Optional[str] = Field(None, min_length=2, max_length=100)
    pincode: Optional[str] = Field(None, min_length=3, max_length=10)
    license_number: Optional[str] = Field(None, max_length=100)
    website: Optional[str] = Field(None, max_length=255)
    logo_url: Optional[str] = Field(None, max_length=500)


class AdminStatusUpdate(BaseModel):
    """Admin status update request"""
    status: str = Field(..., description="New status: ACTIVE, BLOCKED, or PENDING")


class HospitalStatusUpdate(BaseModel):
    """
    Hospital status update. Send either `status` (ACTIVE / SUSPENDED / INACTIVE) or `is_active`
    (true → ACTIVE, false → INACTIVE) for toggle-style UIs.
    """

    status: Optional[str] = Field(None, description="ACTIVE, SUSPENDED, or INACTIVE")
    is_active: Optional[bool] = Field(None, description="If set without status: true → ACTIVE, false → INACTIVE")

    @model_validator(mode="after")
    def _resolve_status(self):
        s = self.status
        if s is not None and str(s).strip() != "":
            self.status = str(s).strip().upper()
        elif self.is_active is not None:
            self.status = "ACTIVE" if self.is_active else "INACTIVE"
        if not self.status:
            raise ValueError("Provide either status or is_active")
        return self


class SubscriptionPlanCreate(BaseModel):
    """Subscription plan creation request"""
    name: str = Field(..., description="Plan name: FREE, STANDARD, or PREMIUM")
    display_name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    monthly_price: float = Field(..., ge=0)
    yearly_price: float = Field(..., ge=0)
    max_doctors: int = Field(..., ge=0, description="0 = unlimited")
    max_patients: int = Field(..., ge=0, description="0 = unlimited")
    max_appointments_per_month: int = Field(..., ge=0)
    max_storage_gb: int = Field(..., ge=1)
    features: Optional[Dict[str, Any]] = Field(default_factory=dict)


class SubscriptionPlanUpdate(BaseModel):
    """Subscription plan update request"""
    display_name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    monthly_price: Optional[float] = Field(None, ge=0)
    yearly_price: Optional[float] = Field(None, ge=0)
    max_doctors: Optional[int] = Field(None, ge=0)
    max_patients: Optional[int] = Field(None, ge=0)
    max_appointments_per_month: Optional[int] = Field(None, ge=0)
    max_storage_gb: Optional[int] = Field(None, ge=1)
    features: Optional[Dict[str, Any]] = None


class PlanAssignmentCreate(BaseModel):
    """Assign subscription plan to hospital"""
    plan_name: str = Field(..., description="Plan name: FREE, STANDARD, or PREMIUM")
    start_date: Optional[str] = Field(None, description="Start date in YYYY-MM-DD format")
    end_date: Optional[str] = Field(None, description="End date in YYYY-MM-DD format")
    is_trial: bool = False
    auto_renew: bool = True


# ============================================================================
# HOSPITAL ADMIN INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class HospitalAdminCreate(BaseModel):
    """Hospital admin creation request"""
    email: EmailStr = Field(..., description="Admin email address")
    phone: str = Field(..., pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    first_name: str = Field(..., min_length=2, max_length=100)
    last_name: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=8, max_length=128)
    hospital_id: str = Field(..., description="UUID of hospital to assign admin to")

class DepartmentCreate(BaseModel):
    """Department creation request"""
    name: str = Field(..., min_length=2, max_length=100, description="Department name")
    code: str = Field(..., min_length=2, max_length=20, description="Department code (e.g., 'CARD', 'ORTHO')")
    description: Optional[str] = Field(None, max_length=500)
    head_of_department: Optional[str] = Field(None, description="HOD name")
    location: Optional[str] = Field(None, max_length=200)
    phone: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    email: Optional[EmailStr] = None
    operating_hours: Optional[str] = Field(None, description="e.g., '9:00 AM - 5:00 PM'")
    bed_capacity: Optional[int] = Field(None, ge=0)
    specializations: Optional[List[str]] = Field(default_factory=list)
    equipment_list: Optional[List[str]] = Field(default_factory=list)
    emergency_services: bool = False


class DepartmentUpdate(BaseModel):
    """Department update request"""
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    head_of_department: Optional[str] = None
    location: Optional[str] = Field(None, max_length=200)
    phone: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    email: Optional[EmailStr] = None
    operating_hours: Optional[str] = None
    bed_capacity: Optional[int] = Field(None, ge=0)
    specializations: Optional[List[str]] = None
    equipment_list: Optional[List[str]] = None
    emergency_services: Optional[bool] = None


class DepartmentStatusUpdate(BaseModel):
    """Department status update request"""
    is_active: bool = Field(..., description="Enable (true) or disable (false) department")


class StaffCreate(BaseModel):
    """Staff user creation request (uses first_name + last_name only)."""

    email: EmailStr = Field(..., description="Staff email address")
    phone: str = Field(..., pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    role: str = Field(
        ...,
        description="DOCTOR, NURSE, PHARMACIST, LAB_TECH, RECEPTIONIST",
    )
    password: str = Field(..., min_length=8, max_length=128)
    emergency_contact: Optional[str] = Field(
        None,
        pattern=r'^\+?[\d\s\-\(\)]{10,20}$',
        description="Emergency contact phone (UI)",
    )
    shift_timing: Optional[str] = Field(
        None,
        max_length=200,
        description="Shift label, e.g. 'Morning (7AM-3PM)' — mapped to DAY/NIGHT/ROTATING for nurse/receptionist",
    )
    joining_date: Optional[str] = Field(
        None,
        description="Joining date: DD-MM-YYYY or YYYY-MM-DD",
    )
    address: Optional[str] = Field(
        None,
        max_length=2000,
        description="Full postal address (stored in profile metadata)",
    )
    doctor_specialization: Optional[str] = Field(
        None,
        max_length=255,
        description="Optional; for doctors defaults to department name or General",
    )
    department_name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=100,
        description=(
            "For DOCTOR only: existing department name in this hospital. "
            "When set, creates doctor profile + primary department assignment on create."
        ),
    )
    doctor_experience_years: Optional[int] = Field(
        None,
        ge=0,
        le=70,
        description="For DOCTOR only: years of professional experience",
    )
    consultation_fee: Optional[float] = Field(
        None,
        ge=0,
        description="For DOCTOR only: standard consultation fee",
    )
    consultation_type: Optional[str] = Field(
        None,
        max_length=100,
        description="For DOCTOR only: e.g. IN_PERSON, ONLINE, HYBRID",
    )
    availability_time: Optional[str] = Field(
        None,
        max_length=2000,
        description="For DOCTOR only: human-readable availability (e.g. Mon-Fri 09:00-17:00)",
    )
    # --- RECEPTIONIST only (ignored for other roles; does not change doctor/nurse/lab/pharmacy create) ---
    receptionist_work_area: Optional[str] = Field(
        None,
        max_length=100,
        description="For RECEPTIONIST only: e.g. OPD, EMERGENCY",
    )
    receptionist_experience_years: Optional[int] = Field(
        None,
        ge=0,
        le=60,
        description="For RECEPTIONIST only: years of experience",
    )
    receptionist_designation: Optional[str] = Field(
        None,
        max_length=100,
        description="For RECEPTIONIST only: job title",
    )
    gender: Optional[str] = Field(None, max_length=30, description="For RECEPTIONIST only")
    blood_group: Optional[str] = Field(None, max_length=20, description="For RECEPTIONIST only")
    receptionist_profile_photo_url: Optional[str] = Field(
        None,
        max_length=500,
        description="For RECEPTIONIST only: public URL for profile photo (same as avatar)",
    )
    # Shared with PATCH staff endpoints (same names as *StaffUpdate models)
    middle_name: Optional[str] = Field(None, max_length=100)
    designation: Optional[str] = Field(
        None,
        max_length=100,
        description="For DOCTOR only: job title on doctor profile (e.g. Staff Physician)",
    )
    # NURSE — align with NurseStaffUpdate
    nurse_designation: Optional[str] = Field(None, max_length=100)
    nurse_specialization: Optional[str] = Field(None, max_length=255)
    nurse_experience_years: Optional[int] = Field(None, ge=0, le=70)
    # LAB_TECH — align with LabTechStaffUpdate
    lab_specialization: Optional[str] = Field(None, max_length=255)
    lab_designation: Optional[str] = Field(None, max_length=100)
    lab_experience_years: Optional[int] = Field(None, ge=0, le=70)
    # PHARMACIST — align with PharmacistStaffUpdate
    pharmacist_specialization: Optional[str] = Field(None, max_length=255)
    pharmacist_designation: Optional[str] = Field(None, max_length=100)
    pharmacist_experience_years: Optional[int] = Field(None, ge=0, le=70)

    @model_validator(mode="after")
    def _doctor_only_professional_fields(self):
        role = (self.role or "").strip().upper()
        if role != "DOCTOR":
            if self.doctor_experience_years is not None:
                raise ValueError("doctor_experience_years is only allowed when role is DOCTOR")
            if self.consultation_fee is not None:
                raise ValueError("consultation_fee is only allowed when role is DOCTOR")
            if self.consultation_type and str(self.consultation_type).strip():
                raise ValueError("consultation_type is only allowed when role is DOCTOR")
            if self.availability_time and str(self.availability_time).strip():
                raise ValueError("availability_time is only allowed when role is DOCTOR")
        if role not in ("DOCTOR", "NURSE", "RECEPTIONIST"):
            if self.department_name and str(self.department_name).strip():
                raise ValueError(
                    "department_name is only allowed when role is DOCTOR, NURSE, or RECEPTIONIST"
                )
        recv_any = (
            self.receptionist_work_area is not None
            or self.receptionist_experience_years is not None
            or (self.receptionist_designation is not None and str(self.receptionist_designation).strip())
            or (self.gender is not None and str(self.gender).strip())
            or (self.blood_group is not None and str(self.blood_group).strip())
            or (self.receptionist_profile_photo_url is not None and str(self.receptionist_profile_photo_url).strip())
        )
        if recv_any and role != "RECEPTIONIST":
            raise ValueError(
                "receptionist_* / gender / blood_group / receptionist_profile_photo_url are only for RECEPTIONIST"
            )
        if role != "DOCTOR" and self.designation and str(self.designation).strip():
            raise ValueError("designation is only allowed when role is DOCTOR")
        nurse_any = (
            (self.nurse_designation is not None and str(self.nurse_designation).strip())
            or (self.nurse_specialization is not None and str(self.nurse_specialization).strip())
            or (self.nurse_experience_years is not None)
        )
        if nurse_any and role != "NURSE":
            raise ValueError("nurse_designation, nurse_specialization, nurse_experience_years are only for NURSE")
        lab_any = (
            (self.lab_specialization is not None and str(self.lab_specialization).strip())
            or (self.lab_designation is not None and str(self.lab_designation).strip())
            or (self.lab_experience_years is not None)
        )
        if lab_any and role != "LAB_TECH":
            raise ValueError("lab_* fields are only allowed when role is LAB_TECH")
        pharm_any = (
            (self.pharmacist_specialization is not None and str(self.pharmacist_specialization).strip())
            or (self.pharmacist_designation is not None and str(self.pharmacist_designation).strip())
            or (self.pharmacist_experience_years is not None)
        )
        if pharm_any and role != "PHARMACIST":
            raise ValueError("pharmacist_* fields are only allowed when role is PHARMACIST")
        return self

    @field_validator("emergency_contact", "joining_date", "address", "shift_timing", mode="before")
    @classmethod
    def _empty_optional_to_none(cls, v):
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v


class StaffStatusUpdate(BaseModel):
    """Staff status update request"""
    is_active: bool = Field(..., description="Activate (true) or deactivate (false) staff user")


class StaffUpdateResponse(BaseModel):
    """Response after updating a staff profile from hospital admin portal."""
    user_id: str
    role: str
    updated_fields: List[str] = Field(default_factory=list)
    message: str


class DoctorStaffUpdate(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    middle_name: Optional[str] = Field(None, max_length=100)
    emergency_contact: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    shift_timing: Optional[str] = Field(None, max_length=200)
    joining_date: Optional[str] = Field(None, description="Joining date: DD-MM-YYYY or YYYY-MM-DD")
    address: Optional[str] = Field(None, max_length=2000)
    department_name: Optional[str] = Field(None, min_length=1, max_length=100)
    doctor_specialization: Optional[str] = Field(None, max_length=255)
    doctor_experience_years: Optional[int] = Field(None, ge=0, le=70)
    consultation_fee: Optional[float] = Field(None, ge=0)
    consultation_type: Optional[str] = Field(None, max_length=100)
    availability_time: Optional[str] = Field(None, max_length=2000)
    designation: Optional[str] = Field(None, max_length=100)


class NurseStaffUpdate(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    middle_name: Optional[str] = Field(None, max_length=100)
    emergency_contact: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    shift_timing: Optional[str] = Field(None, max_length=200)
    joining_date: Optional[str] = Field(None, description="Joining date: DD-MM-YYYY or YYYY-MM-DD")
    address: Optional[str] = Field(None, max_length=2000)
    department_name: Optional[str] = Field(None, min_length=1, max_length=100)
    nurse_designation: Optional[str] = Field(None, max_length=100)
    nurse_specialization: Optional[str] = Field(None, max_length=255)
    nurse_experience_years: Optional[int] = Field(None, ge=0, le=70)


class ReceptionistStaffUpdate(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    middle_name: Optional[str] = Field(None, max_length=100)
    emergency_contact: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    shift_timing: Optional[str] = Field(None, max_length=200)
    joining_date: Optional[str] = Field(None, description="Joining date: DD-MM-YYYY or YYYY-MM-DD")
    address: Optional[str] = Field(None, max_length=2000)
    department_name: Optional[str] = Field(None, min_length=1, max_length=100)
    receptionist_work_area: Optional[str] = Field(None, max_length=100)
    receptionist_experience_years: Optional[int] = Field(None, ge=0, le=60)
    receptionist_designation: Optional[str] = Field(None, max_length=100)
    gender: Optional[str] = Field(None, max_length=30)
    blood_group: Optional[str] = Field(None, max_length=20)
    receptionist_profile_photo_url: Optional[str] = Field(None, max_length=500)


class LabTechStaffUpdate(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    middle_name: Optional[str] = Field(None, max_length=100)
    emergency_contact: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    shift_timing: Optional[str] = Field(None, max_length=200)
    joining_date: Optional[str] = Field(None, description="Joining date: DD-MM-YYYY or YYYY-MM-DD")
    address: Optional[str] = Field(None, max_length=2000)
    department_name: Optional[str] = Field(None, min_length=1, max_length=100)
    lab_specialization: Optional[str] = Field(None, max_length=255)
    lab_designation: Optional[str] = Field(None, max_length=100)
    lab_experience_years: Optional[int] = Field(None, ge=0, le=70)


class PharmacistStaffUpdate(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    middle_name: Optional[str] = Field(None, max_length=100)
    emergency_contact: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    shift_timing: Optional[str] = Field(None, max_length=200)
    joining_date: Optional[str] = Field(None, description="Joining date: DD-MM-YYYY or YYYY-MM-DD")
    address: Optional[str] = Field(None, max_length=2000)
    department_name: Optional[str] = Field(None, min_length=1, max_length=100)
    pharmacist_specialization: Optional[str] = Field(None, max_length=255)
    pharmacist_designation: Optional[str] = Field(None, max_length=100)
    pharmacist_experience_years: Optional[int] = Field(None, ge=0, le=70)


class AppointmentStatusUpdate(BaseModel):
    """Appointment status update request"""
    status: str = Field(..., description="New appointment status")
    notes: Optional[str] = Field(None, description="Optional notes for status change")
    admin_notes: Optional[str] = Field(None, description="Admin notes for audit")
    cancellation_reason: Optional[str] = Field(None, description="Reason if cancelling")
    reschedule_date: Optional[str] = Field(None, description="New date if rescheduling (YYYY-MM-DD)")
    reschedule_time: Optional[str] = Field(None, description="New time if rescheduling (HH:MM)")
    new_doctor_ref: Optional[str] = Field(None, description="Doctor ref (e.g. DOC-xxx) or doctor name for reassignment")


class PatientStatusUpdate(BaseModel):
    """Patient status update request"""
    is_active: bool = Field(..., description="Activate (true) or deactivate (false) patient account")


class WardCreate(BaseModel):
    """Ward creation request"""
    name: str = Field(..., min_length=2, max_length=100, description="Ward name")
    ward_type: str = Field(..., description="Ward type: ICU, GENERAL, EMERGENCY, PRIVATE, MATERNITY, PEDIATRIC, SURGICAL, CARDIAC")
    floor_number: int = Field(..., ge=0, description="Floor number (0 for ground floor)")
    total_beds: int = Field(..., ge=1, le=100, description="Total number of beds")
    description: Optional[str] = Field(None, max_length=500)
    head_nurse: Optional[str] = Field(None, description="Head nurse name")
    phone: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    facilities: Optional[List[str]] = Field(default_factory=list, description="Available facilities")
    visiting_hours: Optional[str] = Field(None, description="e.g., '10:00 AM - 8:00 PM'")
    emergency_access: bool = False
    isolation_capability: bool = False
    oxygen_supply: bool = False
    nurse_station_location: Optional[str] = None


class WardUpdate(BaseModel):
    """Ward update request"""
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    ward_type: Optional[str] = None
    floor_number: Optional[int] = Field(None, ge=0)
    total_beds: Optional[int] = Field(None, ge=1, le=100)
    description: Optional[str] = Field(None, max_length=500)
    head_nurse: Optional[str] = None
    phone: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    facilities: Optional[List[str]] = None
    visiting_hours: Optional[str] = None
    emergency_access: Optional[bool] = None
    isolation_capability: Optional[bool] = None
    oxygen_supply: Optional[bool] = None
    nurse_station_location: Optional[str] = None


class WardStatusUpdate(BaseModel):
    """Ward status update request"""
    is_active: bool = Field(..., description="Enable (true) or disable (false) ward")


class BedCreate(BaseModel):
    """Bed creation request"""
    ward_name: str = Field(..., description="Name of ward")
    bed_number: str = Field(..., min_length=1, max_length=20, description="Bed number/identifier")
    bed_type: str = Field("GENERAL", description="Bed type: GENERAL, ICU, EMERGENCY, PRIVATE")
    equipment: Optional[List[str]] = Field(default_factory=list, description="Attached equipment")
    daily_rate: Optional[float] = Field(None, ge=0, description="Daily rate for this bed")
    notes: Optional[str] = Field(None, max_length=500)
    is_isolation: bool = False
    has_oxygen: bool = False
    has_monitor: bool = False


class BedStatusUpdate(BaseModel):
    """Bed status update request"""
    status: str = Field(..., description="New bed status (AVAILABLE, OCCUPIED, MAINTENANCE, RESERVED)")
    maintenance_notes: Optional[str] = Field(
        None, description="Notes when moving to MAINTENANCE"
    )
    patient_id: Optional[str] = Field(
        None,
        description="When status is OCCUPIED: patient_profiles.id (UUID) or hospital patient ref (e.g. PAT-001)",
    )


class AdmissionCreate(BaseModel):
    """Admission creation request"""
    patient_ref: str = Field(..., description="Patient reference (e.g. PAT-001)")
    admission_type: str = Field(..., description="Admission type: EMERGENCY, PLANNED, TRANSFER")
    admission_date: str = Field(..., description="Admission date (YYYY-MM-DD)")
    admission_time: str = Field(..., description="Admission time (HH:MM)")
    admitting_doctor: str = Field(..., description="Admitting doctor name")
    department: str = Field(..., description="Department name")
    diagnosis: str = Field(..., min_length=5, description="Initial diagnosis")
    symptoms: Optional[str] = Field(None, description="Presenting symptoms")
    medical_history: Optional[str] = Field(None, description="Relevant medical history")
    emergency_contact: Optional[str] = Field(None, description="Emergency contact details")
    insurance_details: Optional[str] = Field(None, description="Insurance information")
    estimated_stay_days: Optional[int] = Field(None, ge=1, description="Estimated length of stay")


class BedAssignmentCreate(BaseModel):
    """Bed assignment request"""
    bed_id: str = Field(..., description="UUID of bed to assign")


class DischargeCreate(BaseModel):
    """Patient discharge request"""
    discharge_type: str = Field("REGULAR", description="Discharge type (REGULAR, AMA, TRANSFER, DEATH)")
    discharge_date: str = Field(..., description="Discharge date (YYYY-MM-DD)")
    discharge_time: str = Field(..., description="Discharge time (HH:MM)")
    discharging_doctor: str = Field(..., description="Discharging doctor name")
    final_diagnosis: str = Field(..., min_length=5, description="Final diagnosis")
    treatment_summary: Optional[str] = Field(None, description="Summary of treatment provided")
    discharge_instructions: Optional[str] = Field(None, description="Instructions for patient")
    follow_up_required: bool = False
    follow_up_date: Optional[str] = Field(None, description="Follow-up date if required (YYYY-MM-DD)")
    medications_prescribed: Optional[List[str]] = Field(default_factory=list)


class DepartmentAssignmentCreate(BaseModel):
    """Department assignment request"""
    staff_name: str = Field(..., description="Staff member name (first name and last name)")
    department_name: str = Field(..., description="Department name to assign to")


class DepartmentUnassignmentCreate(BaseModel):
    """Department unassignment request"""
    staff_name: str = Field(..., description="Staff member name (first name and last name)")
    department_name: str = Field(..., description="Department name to unassign from")


# ============================================================================
# OUTPUT SCHEMAS (Out/Response)
# ============================================================================

class HospitalListOut(BaseModel):
    """Hospital list response"""
    hospitals: List[Dict[str, Any]]
    pagination: Dict[str, Any]


class HospitalDetailsOut(BaseModel):
    """Hospital details response (view/edit screens)."""
    id: str
    name: str
    registration_number: str
    email: str
    phone: str
    contact: Optional[str] = Field(None, description="Same as phone; alias for UI Contact column")
    address: str
    city: str
    state: str
    country: str
    pincode: str
    license_number: Optional[str]
    established_date: Optional[str]
    website: Optional[str]
    logo_url: Optional[str]
    status: str
    is_active: bool = True
    tenant_database_name: Optional[str] = Field(
        None, description="Dedicated Postgres database name on the same server (white-label tenant)"
    )
    created_at: str
    updated_at: str
    subscription: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None
    settings: Optional[Dict[str, Any]] = None


# --- Hospital Admin dashboard (matches HospitalAdminService dashboard methods) ---


class DashboardRecentActivityItem(BaseModel):
    date: str
    appointments: int
    admissions: int


class DashboardPatientMetrics(BaseModel):
    total_patients: int
    active_patients: int
    patient_activity_rate: float


class DashboardStaffMetrics(BaseModel):
    total_staff: int
    total_doctors: int
    active_doctors: int
    doctor_utilization_rate: float


class DashboardAppointmentMetrics(BaseModel):
    todays_appointments: int
    monthly_appointments: int
    completed_appointments: int
    appointment_completion_rate: float


class DashboardBedMetrics(BaseModel):
    total_beds: int
    occupied_beds: int
    available_beds: int
    bed_occupancy_rate: float
    current_admissions: int
    todays_admissions: int
    todays_discharges: int


class DashboardFacilityMetrics(BaseModel):
    total_departments: int
    total_wards: int


class DashboardRevenueMetrics(BaseModel):
    monthly_consultation_revenue: float
    monthly_payments: float
    total_monthly_revenue: float


class DashboardOverviewOut(BaseModel):
    """Hospital-scoped dashboard overview (single hospital from JWT)."""

    dashboard_type: str
    generated_at: str
    hospital_id: str
    patient_metrics: DashboardPatientMetrics
    staff_metrics: DashboardStaffMetrics
    appointment_metrics: DashboardAppointmentMetrics
    bed_metrics: DashboardBedMetrics
    facility_metrics: DashboardFacilityMetrics
    revenue_metrics: DashboardRevenueMetrics
    recent_activity: List[DashboardRecentActivityItem]


class StaffDoctorLast30Days(BaseModel):
    total_appointments: int
    completed_appointments: int
    completion_rate: float


class StaffDoctorPerformanceItem(BaseModel):
    doctor_id: str
    name: str
    specialization: Optional[str] = None
    department: str
    experience_years: Optional[int] = None
    is_active: bool
    last_30_days: StaffDoctorLast30Days


class StaffRoleBreakdownItem(BaseModel):
    role: str
    total_count: int
    active_count: int
    inactive_count: int


class StaffSummary(BaseModel):
    total_staff: int
    active_staff: int
    total_doctors: int
    total_departments: int


class StaffDepartmentDistributionItem(BaseModel):
    department_id: str
    department_name: str
    head_doctor: Optional[str] = None
    doctor_count: int
    is_active: bool


class StaffStatisticsOut(BaseModel):
    """Hospital staff statistics report."""

    report_type: str
    generated_at: str
    hospital_id: str
    summary: StaffSummary
    role_breakdown: List[StaffRoleBreakdownItem]
    doctor_performance: List[StaffDoctorPerformanceItem]
    department_distribution: List[StaffDepartmentDistributionItem]


class AppointmentDateRange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str


class AppointmentOverallStatistics(BaseModel):
    total_appointments: int
    completed_appointments: int
    cancelled_appointments: int
    no_show_appointments: int
    pending_appointments: int
    emergency_appointments: int
    completion_rate: float
    cancellation_rate: float
    no_show_rate: float


class AppointmentPeriodSlice(BaseModel):
    total: int
    completed: int


class AppointmentTimePeriodBreakdown(BaseModel):
    today: AppointmentPeriodSlice
    this_week: AppointmentPeriodSlice
    this_month: AppointmentPeriodSlice


class AppointmentDepartmentBreakdownItem(BaseModel):
    department_name: str
    department_id: str
    total_appointments: int
    completed_appointments: int
    cancelled_appointments: int
    no_show_appointments: int
    completion_rate: float
    revenue: float


class AppointmentDailyTrendItem(BaseModel):
    date: str
    total_appointments: int
    completed: int
    cancelled: int
    no_show: int


class AppointmentTypeCountItem(BaseModel):
    type: str
    count: int


class AppointmentStatisticsOut(BaseModel):
    """Hospital appointment statistics report."""

    report_type: str
    generated_at: str
    hospital_id: str
    date_range: AppointmentDateRange
    overall_statistics: AppointmentOverallStatistics
    time_period_breakdown: AppointmentTimePeriodBreakdown
    department_breakdown: List[AppointmentDepartmentBreakdownItem]
    daily_trends: List[AppointmentDailyTrendItem]
    appointment_types: List[AppointmentTypeCountItem]


class BedOccupancyReportOut(BaseModel):
    """Bed occupancy report response"""
    report_type: str
    total_beds: int
    occupied_beds: int
    available_beds: int
    occupancy_rate: float
    beds_by_ward: Dict[str, Dict[str, int]]


class DepartmentPerformanceReportOut(BaseModel):
    """Department performance report response"""
    report_type: str
    departments: List[Dict[str, Any]]
    top_performing_departments: List[str]


class RevenueSummaryReportOut(BaseModel):
    """Revenue summary report response"""
    report_type: str
    total_revenue: float
    revenue_this_month: float
    revenue_by_department: Dict[str, float]
    revenue_trend: List[Dict[str, Any]]


class AdmissionListOut(BaseModel):
    """Admission list response"""
    admissions: List[Dict[str, Any]]
    pagination: Dict[str, Any]


class WardListOut(BaseModel):
    """Ward list response"""
    wards: List[Dict[str, Any]]
    pagination: Dict[str, Any]


class BedListOut(BaseModel):
    """Bed list response"""
    beds: List[Dict[str, Any]]
    pagination: Dict[str, Any]


class BedWardSummaryOut(BaseModel):
    """Ward block returned with bed detail/list responses."""

    id: str
    name: str
    code: Optional[str] = None
    ward_type: Optional[str] = None
    floor: Optional[str] = None
    building: Optional[str] = None


class BedDetailsOut(BaseModel):
    """Bed details response (matches HospitalAdminService.get_bed_details)."""

    id: str
    bed_code: str
    bed_number: str
    ward: BedWardSummaryOut
    status: str
    bed_type: str
    floor: Optional[str] = None
    room_number: Optional[str] = None
    bed_position: Optional[str] = None
    equipment: Dict[str, bool]
    current_patient: Optional[Dict[str, Any]] = None
    occupied_since: Optional[str] = None
    last_cleaned: Optional[str] = None
    daily_rate: Optional[float] = None
    maintenance_notes: Optional[str] = None
    notes: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    created_at: str
    updated_at: str


class PatientListOut(BaseModel):
    """Patient list response (non-medical data only)"""
    patients: List[Dict[str, Any]]
    pagination: Dict[str, Any]


class AppointmentListOut(BaseModel):
    """Appointment list response"""
    appointments: List[Dict[str, Any]]
    pagination: Dict[str, Any]


class AppointmentDetailsOut(BaseModel):
    """Appointment details response"""
    id: str
    patient_name: str
    doctor_name: str
    department: str
    appointment_date: str
    appointment_time: str
    status: str
    appointment_type: str
    notes: Optional[str]
    created_at: str
    updated_at: str


class StaffListOut(BaseModel):
    """Staff list response"""
    staff: List[Dict[str, Any]]
    pagination: Dict[str, Any]


class StaffDetailsOut(BaseModel):
    """Staff details response"""

    id: str
    email: str
    first_name: str
    last_name: str
    phone: str
    role: str
    department: Optional[str] = None
    status: str
    hire_date: Optional[str] = None
    shift_timing: Optional[str] = None
    last_login: Optional[str] = None
    address: Optional[str] = None
    emergency_contact: Optional[str] = None
    # Doctor specialization is shown in the staff UI as "Doctor Specialization"
    specialization: Optional[str] = None
    created_at: str
    updated_at: str


class DepartmentListOut(BaseModel):
    """Department list response"""
    departments: List[Dict[str, Any]]
    pagination: Dict[str, Any]


class DepartmentDetailsOut(BaseModel):
    """Department details response"""
    id: str
    name: str
    description: Optional[str]
    head_of_department: Optional[str]
    location: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    operating_hours: Optional[str]
    bed_capacity: Optional[int]
    current_bed_occupancy: int
    staff_count: int
    specializations: List[str]
    equipment_list: List[str]
    emergency_services: bool
    is_active: bool
    created_at: str
    updated_at: str