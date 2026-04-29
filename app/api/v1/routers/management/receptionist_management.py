"""
Receptionist Management API
Dedicated receptionist functionality for front desk operations, patient registration, and appointment management.

BUSINESS RULES:
- Receptionists are created by Hospital Admin only
- Receptionists belong to one hospital AND one department
- Receptionists handle OPD operations (patient registration, appointments, check-in)
- Receptionists CAN: Register patients, Schedule appointments, Modify appointments, Check-in patients, Access billing
- Receptionists CANNOT: Access medical records, Prescribe medicines, Modify lab results
"""
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_receptionist
from app.core.database import get_db_session
from app.core.security import get_current_user
from app.core.utils import absolute_public_asset_url
from app.models.hospital import Department
from app.models.user import User
from app.schemas.receptionist import ReceptionistProfileSelfUpdate
from app.services.clinical_service import ClinicalService, send_opd_portal_credentials_email_task
from app.services.email_service import EmailService
from app.schemas.clinical import (
    PatientRegistrationCreate,
    AppointmentSchedulingCreate,
    AppointmentUpdate,
    PatientCheckInCreate,
    ReceptionistPatientDetailOut,
)
from app.core.response_utils import success_response

router = APIRouter(prefix="/receptionist", tags=["Receptionist - OPD Management"])
logger = logging.getLogger(__name__)


async def _receptionist_user_for_write(db: AsyncSession, current_user: User) -> User:
    """Load authenticated user in this session for updates."""
    row = await db.get(User, current_user.id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "USER_NOT_FOUND", "message": "User not found"},
        )
    return row


def _receptionist_metadata_fields(current_user: User) -> dict:
    """Extra profile fields stored on user_metadata at create (receptionist-specific)."""
    md = current_user.user_metadata or {}
    av = absolute_public_asset_url(current_user.avatar_url)
    return {
        "mobile_number": current_user.phone or "",
        "joining_date": md.get("joining_date"),
        "blood_group": md.get("blood_group"),
        "gender": md.get("gender"),
        "shift_timing": md.get("shift_timing"),
        "address": md.get("address"),
        "profile_photo": av,
    }


def _receptionist_profile_base_dict(current_user: User) -> dict:
    """Common user-level fields for profile responses."""
    return {
        "user_id": str(current_user.id),
        "first_name": current_user.first_name or "",
        "last_name": current_user.last_name or "",
        "full_name": f"{current_user.first_name or ''} {current_user.last_name or ''}".strip(),
        "email": current_user.email or "",
        "phone": current_user.phone or "",
        "staff_id": current_user.staff_id,
        "avatar_url": absolute_public_asset_url(current_user.avatar_url),
        "status": getattr(current_user, "status", None),
        "is_active": (getattr(current_user, "status", None) or "").upper() == "ACTIVE",
        **_receptionist_metadata_fields(current_user),
    }


# ============================================================================
# RECEPTIONIST DASHBOARD
# ============================================================================

@router.get("/dashboard")
async def get_receptionist_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get receptionist dashboard with key metrics and information.
    
    Access Control:
    - Only Receptionists can access dashboard
    - Shows OPD-specific metrics for their hospital
    
    Returns:
    - Today's appointments count
    - Checked-in patients
    - Waiting patients
    - Completed consultations
    - Pending registrations
    - Department-wise breakdown
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.get_opd_dashboard(current_user)
    return success_response(message="Dashboard loaded successfully", data=result)


# ============================================================================
# PATIENT REGISTRATION
# ============================================================================

@router.post("/patients/register")
async def register_patient(
    patient_data: PatientRegistrationCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Register new patient for OPD services.
    
    Access Control:
    - Only Receptionists can register patients
    
    Workflow:
    1. Create User account (optional `password` + `email` enables portal login via POST /auth/patient/login)
    2. Create PatientProfile
    3. Assign patient ID
    4. Set hospital association
    
    If `password` is omitted, a one-time `temp_password` is returned (email remains unverified for patient login).
    If `password` is set and `send_credentials_email` is true, the API first tries to send
    credentials immediately. If it fails, a background retry task is queued.
    
    Returns:
    - Patient ID, optional temp_password, portal_login_enabled, `credentials_email_queued` when email is scheduled
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.register_opd_patient(patient_data.model_dump(), current_user)

    pwd = (patient_data.password or "").strip() or None
    email = (str(patient_data.email).strip().lower() if patient_data.email else None)
    # Always queue portal credential email in background — SMTP can take several seconds and blocked registration UX.
    if pwd and email and patient_data.send_credentials_email:
        es = EmailService()
        if not es.is_smtp_configured():
            result["credentials_email_sent"] = False
            result["credentials_email_queued"] = False
            result["credentials_email_hint"] = (
                "SMTP is not configured on the server (set SMTP_USER and SMTP_PASS in Render/environment). "
                "Share login email and password with the patient manually."
            )
        else:
            background_tasks.add_task(
                send_opd_portal_credentials_email_task,
                email,
                patient_data.first_name,
                pwd,
                result.get("hospital_name"),
            )
            result["credentials_email_sent"] = False
            result["credentials_email_queued"] = True
            result["credentials_email_hint"] = (
                "Credentials email queued to send shortly. Check inbox/spam; "
                "if nothing arrives, verify SMTP_HOST/SMTP_USER/SMTP_PASS on the deployment."
            )

    return success_response(message="Patient registered successfully", data=result)


# ============================================================================
# APPOINTMENT MANAGEMENT
# ============================================================================

@router.post("/appointments/schedule")
async def schedule_appointment(
    appointment_data: AppointmentSchedulingCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Schedule appointment for an existing patient.
    
    Identify the patient with `patient_ref` (from registration) and/or `patient_name` (exact full name
    as stored: First Last). If only `patient_name` is sent, it must match exactly one registered patient
    in this hospital; otherwise use `patient_ref` from GET /receptionist/patients/search or
    GET /receptionist/patients/{patient_ref}/profile.
    
    Access Control:
    - Receptionist (or authenticated user with access to this router)
    
    Features:
    - Conflict detection
    - Doctor / department validation
    
    Returns:
    - appointment_ref and scheduling confirmation
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.schedule_opd_appointment(
        appointment_data.model_dump(), current_user
    )
    return success_response(message="Appointment scheduled successfully", data=result)


@router.get("/appointments/today")
async def get_todays_appointments(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    department_name: Optional[str] = Query(None, description="Filter by department"),
    doctor_name: Optional[str] = Query(None, description="Filter by doctor"),
    status: Optional[str] = Query(None, description="Filter by status: SCHEDULED, CHECKED_IN, IN_PROGRESS, COMPLETED, CANCELLED"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get today's appointments for the hospital.
    
    Access Control:
    - Only Receptionists can view appointments
    
    Features:
    - Filter by department
    - Filter by doctor
    - Filter by status
    - Pagination support
    
    Returns:
    - List of appointments
    - Patient details
    - Doctor details
    - Appointment status
    - Check-in status
    """
    clinical_service = ClinicalService(db)
    filters = {
        "page": page,
        "limit": limit,
        "department_name": department_name,
        "doctor_name": doctor_name,
        "status": status
    }
    result = await clinical_service.get_todays_opd_appointments(filters, current_user)
    return success_response(message="Appointments retrieved successfully", data=result)


@router.patch("/appointments/{appointment_ref}")
async def modify_appointment(
    appointment_ref: str,
    modification_data: AppointmentUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Modify existing appointment.
    
    Access Control:
    - Only Receptionists can modify appointments
    
    Features:
    - Change date/time
    - Change doctor
    - Change department
    - Update notes
    - Cannot modify completed appointments
    
    Returns:
    - Updated appointment details
    - Confirmation
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.modify_opd_appointment(
        appointment_ref, 
        modification_data.dict(exclude_unset=True), 
        current_user
    )
    return success_response(message="Appointment modified successfully", data=result)


# ============================================================================
# PATIENT CHECK-IN
# ============================================================================

@router.post("/appointments/{appointment_ref}/check-in")
async def check_in_patient(
    appointment_ref: str,
    checkin_data: PatientCheckInCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Check-in patient for their appointment.
    
    Access Control:
    - Only Receptionists can check-in patients
    
    Workflow:
    1. Verify appointment exists
    2. Check appointment is for today
    3. Record check-in time
    4. Update appointment status to CHECKED_IN
    5. Notify doctor of patient arrival
    
    Returns:
    - Check-in confirmation
    - Queue position
    - Estimated wait time
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.check_in_patient(appointment_ref, checkin_data.dict(), current_user)
    return success_response(message="Patient checked-in successfully", data=result)


# ============================================================================
# PATIENT SEARCH
# ============================================================================

@router.get("/patients/search")
async def search_patients(
    phone: Optional[str] = Query(None, description="Search by phone number"),
    email: Optional[str] = Query(None, description="Search by email"),
    name: Optional[str] = Query(None, description="Search by name"),
    patient_id: Optional[str] = Query(None, description="Search by patient ID"),
    mrn: Optional[str] = Query(None, description="Search by MRN"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Search for patients in the hospital.
    
    Access Control:
    - Only Receptionists can search patients
    
    Search Options:
    - By phone number
    - By email
    - By name (first or last)
    - By patient ID
    - By MRN (Medical Record Number)
    
    Returns:
    - List of matching patients
    - Patient details
    - Recent appointments
    """
    from app.services.appointment_service import AppointmentService
    
    appointment_service = AppointmentService(db)
    
    # Build search parameters
    search_params = {
        "phone": phone,
        "email": email,
        "name": name,
        "patient_id": patient_id,
        "mrn": mrn,
        "page": page,
        "limit": limit
    }
    
    result = await appointment_service.search_patients(search_params, current_user)
    return success_response(message="Search completed successfully", data=result)


@router.get("/patients/{patient_ref}/profile")
async def get_patient_profile_for_schedule(
    patient_ref: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Load full patient details for autofill (e.g. Schedule Appointment form after choosing a name).

    Returns the same registration fields as stored for the patient (no password). Use with
    GET /receptionist/patients/search?name=... to find `patient_ref`, then call this endpoint.
    """
    clinical_service = ClinicalService(db)
    data = await clinical_service.get_receptionist_patient_by_ref(patient_ref, current_user)
    validated = ReceptionistPatientDetailOut.model_validate(data)
    return success_response(message="Patient profile loaded successfully", data=validated.model_dump())


# ============================================================================
# APPOINTMENT STATISTICS
# ============================================================================

@router.get("/appointments/statistics")
async def get_appointment_statistics(
    date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format (default: today)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get appointment statistics for the day.
    
    Access Control:
    - Only Receptionists can view statistics
    
    Returns:
    - Total appointments
    - Checked-in count
    - Waiting count
    - In-consultation count
    - Completed count
    - Cancelled count
    - No-show count
    - Department-wise breakdown
    - Doctor-wise breakdown
    """
    from app.services.appointment_service import AppointmentService
    
    appointment_service = AppointmentService(db)
    result = await appointment_service.get_appointment_statistics(date, current_user)
    return success_response(message="Statistics retrieved successfully", data=result)


# ============================================================================
# QUICK ACTIONS
# ============================================================================

@router.get("/quick-actions")
async def get_quick_actions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get quick action items for receptionist.
    
    Access Control:
    - Only Receptionists can access quick actions
    
    Returns:
    - Pending check-ins
    - Upcoming appointments (next 2 hours)
    - Patients waiting
    - Recent registrations
    - Pending payments
    
    Useful for:
    - Quick overview
    - Priority tasks
    - Action items
    """
    clinical_service = ClinicalService(db)
    
    # Get quick action data
    result = {
        "pending_checkins": [],  # Appointments scheduled but not checked in
        "upcoming_appointments": [],  # Next 2 hours
        "patients_waiting": [],  # Checked in but not in consultation
        "recent_registrations": [],  # Last 10 registrations
        "quick_links": [
            {"action": "register_patient", "label": "Register New Patient", "icon": "user-plus"},
            {"action": "schedule_appointment", "label": "Schedule Appointment", "icon": "calendar-plus"},
            {"action": "search_patient", "label": "Search Patient", "icon": "search"},
            {"action": "view_appointments", "label": "Today's Appointments", "icon": "calendar"},
            {"action": "check_in", "label": "Check-in Patient", "icon": "check-circle"}
        ]
    }
    
    return success_response(message="Quick actions retrieved successfully", data=result)


# ============================================================================
# RECEPTIONIST PROFILE
# ============================================================================

@router.get("/profile")
async def get_receptionist_profile(
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Get receptionist profile information.
    
    Access Control:
    - Only Receptionists can access their profile
    
    Returns:
    - Receptionist details
    - Department assignment
    - Permissions
    - Work schedule
    - Performance metrics
    """
    from app.models.receptionist import ReceptionistProfile

    result = await db.execute(
        select(ReceptionistProfile)
        .options(selectinload(ReceptionistProfile.department))
        .where(ReceptionistProfile.user_id == current_user.id)
    )

    receptionist = result.scalar_one_or_none()

    base = _receptionist_profile_base_dict(current_user)
    base["role"] = "RECEPTIONIST"

    if not receptionist:
        base["note"] = "Profile not yet created"
        return success_response(
            message="Receptionist profile not found",
            data=base,
        )

    dept = getattr(receptionist, "department", None)
    profile_data = {
        **base,
        "receptionist_id": receptionist.receptionist_id,
        "employee_id": receptionist.employee_id,
        "designation": receptionist.designation,
        "work_area": receptionist.work_area,
        "department_id": str(receptionist.department_id),
        "department_name": dept.name if dept else None,
        "experience_years": receptionist.experience_years,
        "shift": receptionist.shift_type,
        "shift_type": receptionist.shift_type,
        "employment_type": receptionist.employment_type,
        "permissions": {
            "can_schedule_appointments": receptionist.can_schedule_appointments,
            "can_modify_appointments": receptionist.can_modify_appointments,
            "can_register_patients": receptionist.can_register_patients,
            "can_collect_payments": receptionist.can_collect_payments,
        },
        "profile_is_active": receptionist.is_active,
    }

    return success_response(message="Profile retrieved successfully", data=profile_data)


@router.patch("/profile")
async def update_receptionist_profile(
    body: ReceptionistProfileSelfUpdate,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Update receptionist-visible profile fields (name, email, phone, employee id, shift, work area, etc.).
    """
    from app.models.receptionist import ReceptionistProfile

    user = await _receptionist_user_for_write(db, current_user)
    payload = body.model_dump(exclude_unset=True)

    res = await db.execute(
        select(ReceptionistProfile).where(ReceptionistProfile.user_id == user.id)
    )
    receptionist = res.scalar_one_or_none()
    if not receptionist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "RECEPTIONIST_PROFILE_MISSING",
                "message": "Receptionist profile not found; contact hospital admin.",
            },
        )

    if "email" in payload and payload["email"] is not None:
        new_email = str(payload["email"]).strip().lower()
        cur = (user.email or "").strip().lower()
        if new_email != cur:
            dup = await db.execute(
                select(User.id).where(
                    and_(func.lower(User.email) == new_email, User.id != user.id)
                )
            )
            if dup.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "EMAIL_IN_USE",
                        "message": "This email is already registered",
                    },
                )
            user.email = new_email

    if "first_name" in payload:
        user.first_name = (payload["first_name"] or "").strip() or ""
    if "last_name" in payload:
        user.last_name = (payload["last_name"] or "").strip() or ""
    if "phone" in payload:
        new_phone = (payload["phone"] or "").strip() if payload["phone"] is not None else ""
        cur_p = (user.phone or "").strip()
        if new_phone != cur_p and new_phone:
            dup_p = await db.execute(
                select(User.id).where(
                    and_(User.phone == new_phone, User.id != user.id)
                )
            )
            if dup_p.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "PHONE_IN_USE",
                        "message": "This phone number is already registered",
                    },
                )
        user.phone = new_phone

    if "avatar_url" in payload and payload["avatar_url"] is not None:
        user.avatar_url = str(payload["avatar_url"]).strip() or None

    md_updates = {}
    for key in ("gender", "blood_group", "address", "shift_timing", "joining_date"):
        if key in payload and payload[key] is not None:
            md_updates[key] = str(payload[key]).strip()
    if md_updates:
        umd = dict(user.user_metadata or {})
        umd.update(md_updates)
        user.user_metadata = umd

    if "employee_id" in payload and payload["employee_id"] is not None:
        eid = str(payload["employee_id"]).strip()
        dup_e = await db.execute(
            select(ReceptionistProfile.id).where(
                and_(
                    ReceptionistProfile.hospital_id == receptionist.hospital_id,
                    ReceptionistProfile.employee_id == eid,
                    ReceptionistProfile.user_id != user.id,
                )
            )
        )
        if dup_e.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "EMPLOYEE_ID_IN_USE",
                    "message": "This employee ID is already in use",
                },
            )
        receptionist.employee_id = eid

    if "work_area" in payload:
        receptionist.work_area = payload["work_area"]
    if "shift_type" in payload:
        receptionist.shift_type = (payload["shift_type"] or "").strip().upper() or receptionist.shift_type
    if "employment_type" in payload:
        receptionist.employment_type = (
            (payload["employment_type"] or "").strip().upper() or receptionist.employment_type
        )
    if "experience_years" in payload and payload["experience_years"] is not None:
        receptionist.experience_years = int(payload["experience_years"])
    if "designation" in payload:
        receptionist.designation = (payload["designation"] or "").strip() or receptionist.designation

    await db.commit()
    await db.refresh(user)
    await db.refresh(receptionist)

    dept = None
    if receptionist.department_id:
        dr = await db.execute(
            select(Department).where(Department.id == receptionist.department_id)
        )
        dept = dr.scalar_one_or_none()

    data = {
        **_receptionist_profile_base_dict(user),
        "role": "RECEPTIONIST",
        "receptionist_id": receptionist.receptionist_id,
        "employee_id": receptionist.employee_id,
        "designation": receptionist.designation,
        "work_area": receptionist.work_area,
        "department_id": str(receptionist.department_id),
        "department_name": dept.name if dept else None,
        "experience_years": receptionist.experience_years,
        "shift": receptionist.shift_type,
        "shift_type": receptionist.shift_type,
        "employment_type": receptionist.employment_type,
        "permissions": {
            "can_schedule_appointments": receptionist.can_schedule_appointments,
            "can_modify_appointments": receptionist.can_modify_appointments,
            "can_register_patients": receptionist.can_register_patients,
            "can_collect_payments": receptionist.can_collect_payments,
        },
        "profile_is_active": receptionist.is_active,
    }

    return success_response(message="Profile updated successfully", data=data)
