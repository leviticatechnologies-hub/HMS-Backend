"""
Hospital Admin API endpoints for hospital-level administrative operations.
Handles department management, staff management, and hospital operations.
CRITICAL: All operations are scoped to hospital_id from JWT token.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List, Dict, Any

from app.api.deps import (
    get_db_session,
    require_hospital_admin,
    require_hospital_admin_context,
    get_current_hospital_context,
)
from app.core.database import get_platform_db_session
from app.dependencies.auth import require_hospital_context
from app.schemas.plan_features import HospitalFeatureFlagsOut
from app.services.subscription_feature_service import get_plan_info_for_hospital
from app.services.hospital_admin_service import HospitalAdminService
from app.models.user import User, AuditLog
from app.core.enums import UserRole
from app.schemas.admin import (
    DepartmentCreate, DepartmentUpdate, DepartmentStatusUpdate,
    StaffCreate, StaffStatusUpdate, StaffUpdateResponse,
    DoctorStaffUpdate, NurseStaffUpdate, ReceptionistStaffUpdate, LabTechStaffUpdate, PharmacistStaffUpdate,
    AppointmentStatusUpdate,
    PatientStatusUpdate, WardCreate, WardUpdate, WardStatusUpdate,
    BedCreate, BedStatusUpdate, AdmissionCreate, BedAssignmentCreate,
    DischargeCreate, DepartmentAssignmentCreate, DepartmentUnassignmentCreate,
    DashboardOverviewOut, StaffStatisticsOut, AppointmentStatisticsOut,
    BedOccupancyReportOut, DepartmentPerformanceReportOut,     RevenueSummaryReportOut,
    HospitalAdminAuditLogListOut,
    HospitalAdminAuditSummaryOut,
    AdmissionListOut, WardListOut, BedListOut, BedDetailsOut,
    PatientListOut, AppointmentListOut, AppointmentDetailsOut,
    StaffListOut, StaffDetailsOut, DepartmentListOut, DepartmentDetailsOut
)

router = APIRouter(prefix="/hospital-admin")


# ============================================================================
# DEPENDENCY FUNCTIONS
# ============================================================================

async def get_hospital_admin_service(
    context: Dict[str, Any] = Depends(require_hospital_admin_context()),
    db: AsyncSession = Depends(get_platform_db_session)
) -> HospitalAdminService:
    """Get Hospital Admin service instance with proper access control"""
    return HospitalAdminService(db, context["hospital_id"])


# ============================================================================
# PLATFORM SETTINGS — subscription feature flags (Dashboard / module visibility)
# ============================================================================


@router.get(
    "/platform-settings/features",
    response_model=HospitalFeatureFlagsOut,
    tags=["Hospital Admin - Platform Settings"],
)
async def get_hospital_subscription_features(
    _user: User = Depends(require_hospital_admin()),
    context: Dict[str, Any] = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Effective flags for `lab_tests`, `video_consultation`, `pharmacy` from the hospital's plan
    (`subscription_plans.features` overrides defaults: Basic/STANDARD vs PREMIUM).
    """
    hid = uuid.UUID(context["hospital_id"])
    pname, display, feats = await get_plan_info_for_hospital(db, hid)
    return HospitalFeatureFlagsOut(
        plan_name=pname,
        plan_display_name=display,
        features=feats,
    )


# ============================================================================
# TASK 2.1 - DEPARTMENT MANAGEMENT ENDPOINTS
# ============================================================================

@router.post("/departments", status_code=status.HTTP_201_CREATED, tags=["Hospital Admin - Department Management"])
async def create_department(
    department_data: DepartmentCreate,
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Create a new department within the hospital.
    
    Creates a department with:
    - Unique department code within the hospital
    - Optional head doctor assignment
    - Department-specific settings and capabilities
    """
    result = await service.create_department(department_data.dict())
    return result


@router.get("/departments", response_model=DepartmentListOut, tags=["Hospital Admin - Department Management"])
async def list_departments(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    active_only: bool = Query(False, description="Show only active departments"),
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get paginated list of departments within the hospital.
    
    Returns departments with:
    - Basic department information
    - Head doctor details
    - Department status and capabilities
    """
    result = await service.get_departments(
        page=page,
        limit=limit,
        active_only=active_only
    )
    return result


@router.get("/departments/{department_id}", response_model=DepartmentDetailsOut, tags=["Hospital Admin - Department Management"])
async def get_department_details(
    department_id: str,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get detailed information about a specific department.
    
    Returns complete department information including:
    - Department configuration and settings
    - Head doctor information
    - Department statistics and metrics
    """
    try:
        dept_uuid = uuid.UUID(department_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_DEPARTMENT_ID", "message": "Invalid department ID format"}
        )
    
    result = await service.get_department_details(dept_uuid)
    return result


@router.put("/departments/{department_id}", tags=["Hospital Admin - Department Management"])
async def update_department(
    department_id: str,
    update_data: DepartmentUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Update department information.
    
    Allows updating department details with proper validation:
    - Ensures department code uniqueness within hospital
    - Validates head doctor assignment
    - Maintains department configuration integrity
    """
    try:
        dept_uuid = uuid.UUID(department_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_DEPARTMENT_ID", "message": "Invalid department ID format"}
        )
    
    # Convert to dict, excluding None values
    update_dict = {k: v for k, v in update_data.dict().items() if v is not None}
    
    if not update_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NO_UPDATE_DATA", "message": "No valid update data provided"}
        )
    
    result = await service.update_department(dept_uuid, update_dict)
    return result


@router.patch("/departments/{department_id}/status", tags=["Hospital Admin - Department Management"])
async def update_department_status(
    department_id: str,
    status_data: DepartmentStatusUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Enable or disable a department.
    
    Status changes affect:
    - Department availability for appointments
    - Staff assignment capabilities
    - Department visibility in the system
    """
    try:
        dept_uuid = uuid.UUID(department_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_DEPARTMENT_ID", "message": "Invalid department ID format"}
        )
    
    result = await service.update_department_status(dept_uuid, status_data.is_active)
    return result


# ============================================================================
# TASK 2.2 - STAFF MANAGEMENT ENDPOINTS
# ============================================================================

@router.post("/staff", status_code=status.HTTP_201_CREATED, tags=["Hospital Admin - Staff Management"])
async def create_staff_user(
    staff_data: StaffCreate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Create a new staff user (Doctor, Lab Tech, Pharmacist).
    
    Creates a staff user with:
    - Unique email and phone validation
    - Role assignment (DOCTOR, LAB_TECH, PHARMACIST)
    - Temporary password generation
    - Hospital-scoped access
    """
    payload = staff_data.model_dump(exclude_none=False)
    result = await service.create_staff_user(payload)
    return result


@router.get("/staff", response_model=StaffListOut, tags=["Hospital Admin - Staff Management"])
async def list_staff_users(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    role: Optional[str] = Query(None, description="Filter by role: DOCTOR, LAB_TECH, PHARMACIST"),
    active_only: bool = Query(False, description="Show only active staff"),
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get paginated list of staff users within the hospital.
    
    Returns staff with:
    - Basic user information
    - Role assignments
    - Account status and verification
    - Login activity
    """
    result = await service.get_staff_users(
        page=page,
        limit=limit,
        role_filter=role,
        active_only=active_only
    )
    return result


@router.get("/staff/{staff_id}", response_model=StaffDetailsOut, tags=["Hospital Admin - Staff Management"])
async def get_staff_details(
    staff_id: str,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get detailed information about a specific staff user.
    
    Returns complete staff information including:
    - User account details
    - Role and permission information
    - Professional profile data (for doctors)
    - Security and login status
    """
    try:
        staff_uuid = uuid.UUID(staff_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STAFF_ID", "message": "Invalid staff ID format"}
        )
    
    result = await service.get_staff_details(staff_uuid)
    return result


@router.patch("/staff/doctors/{staff_id}", response_model=StaffUpdateResponse, tags=["Hospital Admin - Staff Management"])
async def update_doctor_staff_profile(
    staff_id: str,
    update_data: DoctorStaffUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service),
):
    """Update doctor staff profile from hospital admin portal."""
    try:
        staff_uuid = uuid.UUID(staff_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STAFF_ID", "message": "Invalid staff ID format"},
        )
    result = await service.update_doctor_staff(staff_uuid, update_data.model_dump(exclude_none=True))
    return StaffUpdateResponse(**result)


@router.patch("/staff/nurses/{staff_id}", response_model=StaffUpdateResponse, tags=["Hospital Admin - Staff Management"])
async def update_nurse_staff_profile(
    staff_id: str,
    update_data: NurseStaffUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service),
):
    """Update nurse staff profile from hospital admin portal."""
    try:
        staff_uuid = uuid.UUID(staff_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STAFF_ID", "message": "Invalid staff ID format"},
        )
    result = await service.update_nurse_staff(staff_uuid, update_data.model_dump(exclude_none=True))
    return StaffUpdateResponse(**result)


@router.patch("/staff/receptionists/{staff_id}", response_model=StaffUpdateResponse, tags=["Hospital Admin - Staff Management"])
async def update_receptionist_staff_profile(
    staff_id: str,
    update_data: ReceptionistStaffUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service),
):
    """Update receptionist staff profile from hospital admin portal."""
    try:
        staff_uuid = uuid.UUID(staff_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STAFF_ID", "message": "Invalid staff ID format"},
        )
    result = await service.update_receptionist_staff(staff_uuid, update_data.model_dump(exclude_none=True))
    return StaffUpdateResponse(**result)


@router.patch("/staff/lab-techs/{staff_id}", response_model=StaffUpdateResponse, tags=["Hospital Admin - Staff Management"])
async def update_lab_tech_staff_profile(
    staff_id: str,
    update_data: LabTechStaffUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service),
):
    """Update lab tech staff profile from hospital admin portal."""
    try:
        staff_uuid = uuid.UUID(staff_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STAFF_ID", "message": "Invalid staff ID format"},
        )
    result = await service.update_lab_tech_staff(staff_uuid, update_data.model_dump(exclude_none=True))
    return StaffUpdateResponse(**result)


@router.patch("/staff/pharmacists/{staff_id}", response_model=StaffUpdateResponse, tags=["Hospital Admin - Staff Management"])
async def update_pharmacist_staff_profile(
    staff_id: str,
    update_data: PharmacistStaffUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service),
):
    """Update pharmacist staff profile from hospital admin portal."""
    try:
        staff_uuid = uuid.UUID(staff_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STAFF_ID", "message": "Invalid staff ID format"},
        )
    result = await service.update_pharmacist_staff(staff_uuid, update_data.model_dump(exclude_none=True))
    return StaffUpdateResponse(**result)


@router.patch("/staff/{staff_id}/status", tags=["Hospital Admin - Staff Management"])
async def update_staff_status(
    staff_id: str,
    status_data: StaffStatusUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Activate or deactivate a staff user.
    
    Status changes affect:
    - User login capability
    - System access permissions
    - Account visibility in staff lists
    """
    try:
        staff_uuid = uuid.UUID(staff_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STAFF_ID", "message": "Invalid staff ID format"}
        )
    
    result = await service.update_staff_status(staff_uuid, status_data.is_active)
    return result


@router.post("/staff/{staff_id}/reset-password", tags=["Hospital Admin - Staff Management"])
async def reset_staff_password(
    staff_id: str,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Reset staff user password.
    
    Password reset:
    - Generates new temporary password
    - Clears failed login attempts
    - Unlocks account if locked
    - Forces password change on next login
    """
    try:
        staff_uuid = uuid.UUID(staff_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STAFF_ID", "message": "Invalid staff ID format"}
        )
    
    result = await service.reset_staff_password(staff_uuid)
    return result


# ============================================================================
# TASK 2.2.1 - DEPARTMENT ASSIGNMENT ENDPOINTS
# ============================================================================

@router.post("/departments/assign-staff", tags=["Hospital Admin - Department Assignment"])
async def assign_staff_to_department(
    assignment_data: DepartmentAssignmentCreate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Assign staff member to a department.
    
    This is mandatory for staff to work within the hospital.
    Staff can only perform operations within their assigned departments.
    """
    result = await service.assign_staff_to_department(assignment_data.dict())
    return result


@router.post("/departments/unassign-staff", tags=["Hospital Admin - Department Assignment"])
async def unassign_staff_from_department(
    unassignment_data: DepartmentUnassignmentCreate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Unassign staff member from a department.
    
    Staff will lose access to department-specific operations.
    """
    result = await service.unassign_staff_from_department(unassignment_data.dict())
    return result


@router.get("/departments/{department_name}/staff", tags=["Hospital Admin - Department Assignment"])
async def get_department_staff(
    department_name: str,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get all staff members assigned to a specific department.
    
    Returns doctors, nurses, and other staff assigned to the department.
    """
    result = await service.get_department_staff(department_name)
    return {"staff": result}


@router.get("/staff/{staff_name}/departments", tags=["Hospital Admin - Department Assignment"])
async def get_staff_departments(
    staff_name: str,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get all departments assigned to a specific staff member.
    
    Shows which departments the staff member can work in.
    """
    result = await service.get_staff_departments(staff_name)
    return {"departments": result}


# ============================================================================
# TASK 2.4 - APPOINTMENT OVERSIGHT ENDPOINTS
# ============================================================================

@router.get("/appointments", response_model=AppointmentListOut, tags=["Hospital Admin - Appointment Oversight"])
async def list_appointments(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by appointment status"),
    doctor_id: Optional[str] = Query(None, description="Filter by doctor UUID"),
    department_id: Optional[str] = Query(None, description="Filter by department UUID"),
    date_from: Optional[str] = Query(None, description="Filter from date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter to date (YYYY-MM-DD)"),
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get paginated list of appointments for hospital oversight.
    
    Provides comprehensive appointment management with:
    - Multi-criteria filtering (status, doctor, department, date range)
    - Patient and doctor information
    - Appointment details and status tracking
    - Admin oversight capabilities
    """
    result = await service.get_appointments(
        page=page,
        limit=limit,
        status_filter=status,
        doctor_id=doctor_id,
        department_id=department_id,
        date_from=date_from,
        date_to=date_to
    )
    return result


@router.get("/appointments/{appointment_id}", response_model=AppointmentDetailsOut, tags=["Hospital Admin - Appointment Oversight"])
async def get_appointment_details(
    appointment_id: str,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get detailed appointment information for admin oversight.
    
    Returns complete appointment details including:
    - Patient information and medical history
    - Doctor and department details
    - Appointment timeline and status history
    - Payment and billing information
    """
    try:
        appointment_uuid = uuid.UUID(appointment_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_APPOINTMENT_ID", "message": "Invalid appointment ID format"}
        )
    
    result = await service.get_appointment_details(appointment_uuid)
    return result


@router.patch("/appointments/{appointment_id}/status", tags=["Hospital Admin - Appointment Oversight"])
async def update_appointment_status(
    appointment_id: str,
    status_update: AppointmentStatusUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Update appointment status with admin oversight.
    
    Supports comprehensive appointment management:
    - Status changes (cancel, reschedule, complete)
    - Doctor reassignment
    - Appointment rescheduling
    - Admin notes and audit trail
    """
    try:
        appointment_uuid = uuid.UUID(appointment_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_APPOINTMENT_ID", "message": "Invalid appointment ID format"}
        )
    
    result = await service.update_appointment_status(
        appointment_id=appointment_uuid,
        new_status=status_update.status,
        admin_notes=status_update.admin_notes,
        cancellation_reason=status_update.cancellation_reason,
        reschedule_date=status_update.reschedule_date,
        reschedule_time=status_update.reschedule_time,
        new_doctor_ref=status_update.new_doctor_ref,
        new_doctor_uuid=status_update.new_doctor_uuid,
    )
    return result


# ============================================================================
# TASK 2.5 - PATIENT MANAGEMENT (NON-MEDICAL) ENDPOINTS
# ============================================================================

@router.get("/patients", response_model=PatientListOut, tags=["Hospital Admin - Patient Management"])
async def list_patients(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    active_only: bool = Query(False, description="Show only active patients"),
    search: Optional[str] = Query(None, description="Search by name, email, phone, or patient ID"),
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get paginated list of patients for non-medical administrative oversight.
    
    IMPORTANT: This endpoint provides NON-MEDICAL data only:
    - Basic demographic information
    - Contact details and account status
    - Administrative metrics (appointment counts)
    - EXCLUDES: Medical history, allergies, medications, diagnoses
    
    Hospital admins can manage patient accounts but cannot access medical records.
    """
    result = await service.get_patients(
        page=page,
        limit=limit,
        active_only=active_only,
        search=search
    )
    return result


@router.patch("/patients/{patient_id}/status", tags=["Hospital Admin - Patient Management"])
async def update_patient_status(
    patient_id: str,
    status_data: PatientStatusUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Activate or deactivate patient account.
    
    This administrative action:
    - Controls patient login and system access
    - Does not affect medical records or history
    - Maintains audit trail for account changes
    - Ensures compliance with data protection policies
    
    NOTE: Medical records remain unchanged and accessible to authorized medical staff.
    """
    try:
        patient_uuid = uuid.UUID(patient_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_PATIENT_ID", "message": "Invalid patient ID format"}
        )
    
    result = await service.update_patient_status(patient_uuid, status_data.is_active)
    return result


# ============================================================================
# TASK 2.6 - BED & WARD MANAGEMENT ENDPOINTS
# ============================================================================

@router.post("/wards", status_code=status.HTTP_201_CREATED, tags=["Hospital Admin - Ward & Bed Management"])
async def create_ward(
    ward_data: WardCreate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Create a new ward/unit in the hospital.
    
    Creates a ward with:
    - Ward type classification (ICU, General, Emergency, Private)
    - Capacity and facility specifications
    - Staff assignments and contact information
    - Equipment and service capabilities
    """
    result = await service.create_ward(ward_data.dict())
    return result


@router.get("/wards", response_model=WardListOut, tags=["Hospital Admin - Ward & Bed Management"])
async def list_wards(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    ward_type: Optional[str] = Query(None, description="Filter by ward type"),
    active_only: bool = Query(False, description="Show only active wards"),
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get paginated list of hospital wards.
    
    Returns wards with:
    - Ward information and specifications
    - Bed statistics and occupancy rates
    - Staff assignments and facilities
    - Equipment and service capabilities
    """
    result = await service.get_wards(
        page=page,
        limit=limit,
        ward_type=ward_type,
        active_only=active_only
    )
    return result


@router.put("/wards/{ward_id}", tags=["Hospital Admin - Ward & Bed Management"])
async def update_ward(
    ward_id: str,
    update_data: WardUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Update ward information.
    
    Allows updating ward details with proper validation:
    - Ensures ward code uniqueness
    - Validates staff assignments
    - Maintains facility and equipment specifications
    """
    try:
        ward_uuid = uuid.UUID(ward_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_WARD_ID", "message": "Invalid ward ID format"}
        )
    
    # Convert to dict, excluding None values
    update_dict = {k: v for k, v in update_data.dict().items() if v is not None}
    
    if not update_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NO_UPDATE_DATA", "message": "No valid update data provided"}
        )
    
    result = await service.update_ward(ward_uuid, update_dict)
    return result


@router.patch("/wards/{ward_id}/status", tags=["Hospital Admin - Ward & Bed Management"])
async def update_ward_status(
    ward_id: str,
    status_data: WardStatusUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Enable or disable a ward.
    
    Status changes affect:
    - Ward availability for bed assignments
    - Patient admission capabilities
    - Ward visibility in the system
    """
    try:
        ward_uuid = uuid.UUID(ward_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_WARD_ID", "message": "Invalid ward ID format"}
        )
    
    result = await service.update_ward_status(ward_uuid, status_data.is_active)
    return result


@router.post("/beds", status_code=status.HTTP_201_CREATED, tags=["Hospital Admin - Ward & Bed Management"])
async def create_bed(
    bed_data: BedCreate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Create a new bed in a ward.
    
    Creates a bed with:
    - Ward identification by name (not ID)
    - Unique bed identification and coding
    - Equipment and facility specifications
    - Location and positioning details
    - Pricing for private beds
    """
    result = await service.create_bed(bed_data.dict())
    return result


@router.get("/beds", response_model=BedListOut, tags=["Hospital Admin - Ward & Bed Management"])
async def list_beds(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    ward_id: Optional[str] = Query(None, description="Filter by ward UUID"),
    status: Optional[str] = Query(None, description="Filter by bed status"),
    bed_type: Optional[str] = Query(None, description="Filter by bed type"),
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get paginated list of beds.
    
    Returns beds with:
    - Bed identification and location
    - Current status and occupancy
    - Equipment and facility details
    - Patient assignment information
    """
    result = await service.get_beds(
        page=page,
        limit=limit,
        ward_id=ward_id,
        status_filter=status,
        bed_type=bed_type
    )
    return result


@router.get("/beds/{bed_id}", response_model=BedDetailsOut, tags=["Hospital Admin - Ward & Bed Management"])
async def get_bed_details(
    bed_id: str,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get detailed bed information.
    
    Returns complete bed details including:
    - Bed specifications and equipment
    - Current occupancy and patient information
    - Maintenance history and notes
    - Ward and location details
    """
    try:
        bed_uuid = uuid.UUID(bed_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_BED_ID", "message": "Invalid bed ID format"}
        )
    
    result = await service.get_bed_details(bed_uuid)
    return result


@router.patch("/beds/{bed_id}/status", tags=["Hospital Admin - Ward & Bed Management"])
async def update_bed_status(
    bed_id: str,
    status_data: BedStatusUpdate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Update bed status.
    
    Supports comprehensive bed management:
    - Status changes (available, occupied, maintenance, reserved)
    - Patient assignment and discharge
    - Maintenance scheduling and notes
    - Bed availability tracking
    """
    try:
        bed_uuid = uuid.UUID(bed_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_BED_ID", "message": "Invalid bed ID format"}
        )
    
    result = await service.update_bed_status(
        bed_id=bed_uuid,
        new_status=status_data.status,
        maintenance_notes=status_data.maintenance_notes,
        patient_id=status_data.patient_id
    )
    return result


# ============================================================================
# TASK 2.7 - BED ASSIGNMENT (ADMISSION FLOW) ENDPOINTS
# ============================================================================

@router.post("/admissions", status_code=status.HTTP_201_CREATED, tags=["Hospital Admin - Admission Management"])
async def create_admission(
    admission_data: AdmissionCreate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Create a new patient admission.
    
    Creates admission record with:
    - Patient and doctor assignment
    - Department allocation
    - Admission type and urgency classification
    - Initial diagnosis and care instructions
    """
    result = await service.create_admission(admission_data.model_dump())
    return result


@router.get("/admissions", response_model=AdmissionListOut, tags=["Hospital Admin - Admission Management"])
async def list_admissions(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by admission status"),
    date_from: Optional[str] = Query(None, description="Filter from date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter to date (YYYY-MM-DD)"),
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get paginated list of patient admissions.
    
    Returns admissions with:
    - Patient and doctor information
    - Bed assignment details
    - Admission timeline and status
    - Length of stay tracking
    """
    result = await service.get_admissions(
        page=page,
        limit=limit,
        status_filter=status,
        date_from=date_from,
        date_to=date_to
    )
    return result


@router.patch("/admissions/{admission_id}/assign-bed", tags=["Hospital Admin - Admission Management"])
async def assign_bed_to_admission(
    admission_id: str,
    bed_assignment: BedAssignmentCreate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Assign bed to patient admission.
    
    Bed assignment process:
    - Validates bed availability
    - Updates admission status to ADMITTED
    - Marks bed as OCCUPIED
    - Records admission timeline
    - Prevents double assignments
    """
    try:
        admission_uuid = uuid.UUID(admission_id)
        bed_uuid = uuid.UUID(bed_assignment.bed_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_UUID", "message": "Invalid admission or bed ID format"}
        )
    
    result = await service.assign_bed_to_admission(
        admission_id=admission_uuid,
        bed_id=bed_uuid,
        admission_notes=bed_assignment.admission_notes
    )
    return result


@router.patch("/admissions/{admission_id}/discharge", tags=["Hospital Admin - Admission Management"])
async def discharge_patient(
    admission_id: str,
    discharge_data: DischargeCreate,
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Discharge patient and release bed.
    
    Discharge process:
    - Updates admission status to DISCHARGED
    - Releases assigned bed (marks as AVAILABLE)
    - Calculates length of stay
    - Creates discharge summary (optional)
    - Records discharge timeline and notes
    """
    try:
        admission_uuid = uuid.UUID(admission_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_ADMISSION_ID", "message": "Invalid admission ID format"}
        )
    
    result = await service.discharge_patient(
        admission_id=admission_uuid,
        discharge_data=discharge_data.dict()
    )
    return result


# ============================================================================
# TASK 2.8 - HOSPITAL REPORTS (SOW-ALIGNED) ENDPOINTS
# ============================================================================

@router.get("/reports/bed-occupancy", response_model=BedOccupancyReportOut, tags=["Hospital Admin - Reports & Analytics"])
async def get_bed_occupancy_report(
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    ward_id: Optional[str] = Query(None, description="Filter by ward UUID"),
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Generate bed occupancy report.
    
    Provides comprehensive bed utilization analytics:
    - Current occupancy rates and bed status breakdown
    - Ward-wise occupancy analysis
    - Daily admission/discharge trends
    - Average length of stay metrics
    - Bed availability forecasting data
    """
    result = await service.get_bed_occupancy_report(
        date_from=date_from,
        date_to=date_to,
        ward_id=ward_id
    )
    return result


@router.get("/reports/department-performance", response_model=DepartmentPerformanceReportOut, tags=["Hospital Admin - Reports & Analytics"])
async def get_department_performance_report(
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Generate department performance report.
    
    Provides operational performance metrics:
    - Appointment completion and cancellation rates
    - Department-wise patient volume analysis
    - Doctor productivity metrics
    - Revenue generation by department
    - Service efficiency indicators
    """
    result = await service.get_department_performance_report(
        date_from=date_from,
        date_to=date_to
    )
    return result


@router.get("/reports/revenue-summary", response_model=RevenueSummaryReportOut, tags=["Hospital Admin - Reports & Analytics"])
async def get_revenue_summary_report(
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Generate revenue summary report.

    Uses completed-appointment consultation fees (no invoice/billing module).
    Includes department breakdown and a 7-day revenue trend ending on ``date_to``.
    """
    result = await service.get_revenue_summary_report(
        date_from=date_from,
        date_to=date_to
    )
    return result


# ============================================================================
# TASK 2.9 - HOSPITAL DASHBOARD & REPORTS ENDPOINTS
# ============================================================================

@router.get("/dashboard/overview", response_model=DashboardOverviewOut, tags=["Hospital Admin - Dashboard"])
async def get_dashboard_overview(
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get hospital dashboard overview with key metrics.
    
    Provides comprehensive hospital-level insights:
    - Patient and staff metrics
    - Appointment and bed utilization
    - Revenue and facility statistics
    - Recent activity trends
    - Real-time operational indicators
    """
    result = await service.get_dashboard_overview()
    return result


@router.get("/dashboard/staff-stats", response_model=StaffStatisticsOut, tags=["Hospital Admin - Dashboard"])
async def get_staff_statistics(
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get detailed staff statistics and performance metrics.
    
    Provides staff management insights:
    - Staff distribution by role and department
    - Doctor performance and productivity metrics
    - Department-wise staff allocation
    - Activity and utilization rates
    - Staff engagement indicators
    """
    result = await service.get_staff_statistics()
    return result


@router.get("/dashboard/appointment-stats", response_model=AppointmentStatisticsOut, tags=["Hospital Admin - Dashboard"])
async def get_appointment_statistics(
    current_user: User = Depends(require_hospital_admin()),
    service: HospitalAdminService = Depends(get_hospital_admin_service)
):
    """
    Get comprehensive appointment statistics and trends.
    
    Provides appointment management insights:
    - Completion, cancellation, and no-show rates
    - Department-wise appointment breakdown
    - Daily trends and patterns
    - Appointment type distribution
    - Revenue generation from appointments
    """
    result = await service.get_appointment_statistics()
    return result


# ============================================================================
# AUDIT — Hospital Admin API access trail (middleware + list)
# ============================================================================


@router.get(
    "/audit-logs",
    response_model=HospitalAdminAuditLogListOut,
    tags=["Hospital Admin - Audit"],
)
async def list_hospital_admin_audit_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _user: User = Depends(require_hospital_admin()),
    context: Dict[str, Any] = Depends(require_hospital_admin_context()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """
    List audit records for Hospital Admin module activity (`resource_type` = HospitalAdmin),
    written automatically by middleware for `/api/v1/hospital-admin/*` requests.

    Returns **summary** card counts (total, access/read events, updates, creations, deletions)
    and **items** with `user_name`, display `action`, `resource`, and `timestamp` for the UI table.
    """
    from app.utils.hospital_admin_audit_labels import action_display_from_code, resource_from_row

    hid = uuid.UUID(context["hospital_id"])
    base_filt = and_(
        AuditLog.hospital_id == hid,
        AuditLog.resource_type == "HospitalAdmin",
    )

    async def _count_action(action_code: str) -> int:
        return (
            await db.execute(
                select(func.count(AuditLog.id)).where(
                    base_filt,
                    AuditLog.action == action_code,
                )
            )
        ).scalar() or 0

    total_logs = (
        await db.execute(select(func.count(AuditLog.id)).where(base_filt))
    ).scalar() or 0

    summary = HospitalAdminAuditSummaryOut(
        total_logs=total_logs,
        user_logins=await _count_action("VIEW"),
        updates=await _count_action("UPDATE"),
        creations=await _count_action("CREATE"),
        deletions=await _count_action("DELETE"),
    )

    row_result = await db.execute(
        select(AuditLog, User.first_name, User.last_name)
        .outerjoin(User, AuditLog.user_id == User.id)
        .where(base_filt)
        .order_by(desc(AuditLog.created_at))
        .offset(skip)
        .limit(limit)
    )
    items: List[Dict[str, Any]] = []
    for r, fn, ln in row_result.all():
        nv = r.new_values if isinstance(r.new_values, dict) else {}
        user_name = (f"{fn or ''} {ln or ''}").strip() or "Unknown user"
        action_code = (r.action or "").upper()
        resource = resource_from_row(nv, r.description or "")
        items.append(
            {
                "id": str(r.id),
                "user_id": str(r.user_id),
                "user_name": user_name,
                "action": action_display_from_code(action_code),
                "action_code": action_code,
                "resource": resource,
                "timestamp": r.created_at.isoformat() if r.created_at else "",
                "ip_address": r.ip_address,
                "description": r.description,
            }
        )

    return HospitalAdminAuditLogListOut(
        summary=summary,
        items=items,
        total=total_logs,
        skip=skip,
        limit=limit,
    )


# ============================================================================
# PLACEHOLDER ENDPOINTS (TO BE IMPLEMENTED IN LATER TASKS)
# ============================================================================
