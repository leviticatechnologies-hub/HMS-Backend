"""
Authentication API endpoints organized by user type.
Clean separation: Super Admin, Hospital Admin, Staff, Patient
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional, List, Dict, Any
import uuid

from app.api.deps import (
    require_super_admin,
    require_hospital_admin,
    require_patient,
    require_staff,
    get_current_user,
)
from app.core.database import get_platform_db_session
from app.services.auth_service import AuthService
from app.models.user import User
from app.models.tenant import Hospital
from app.core.enums import UserRole
from app.schemas.response import SuccessResponse, APIResponse
from app.schemas.auth import (
    LoginCreate, PasswordChangeUpdate, HospitalCreate, HospitalAdminCreate,
    PatientRegistrationCreate, OTPVerificationCreate, ForgotPasswordCreate,
    PasswordResetCreate, AuthOut, HospitalAdminOut, UserInfoOut, HospitalOut
)
 
router = APIRouter(prefix="/auth", tags=["Authentication"])
security = HTTPBearer()


# ============================================================================
# LOGIN ENDPOINTS
# ============================================================================

# Unified login for Super Admin, Hospital Admin, and Hospital Staff.
# Patients must use `POST /api/v1/auth/patient/login`.
@router.post("/login")
async def login(
    login_data: LoginCreate,
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[AuthOut]:
    auth_service = AuthService(db)
    result = await auth_service.staff_admin_super_admin_login(login_data.email, login_data.password)
    
    return SuccessResponse(
        success=True,
        message="Login successful",
        data=AuthOut(**result)
    ).dict()


# ============================================================================
# SUPER ADMIN MANAGEMENT ENDPOINTS (Hospital & Admin Creation)
# ============================================================================

@router.post("/super-admin/hospitals", status_code=status.HTTP_201_CREATED)
async def create_hospital(
    hospital_data: HospitalCreate,
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[Dict[str, Any]]:
    """
    Create hospital (Super Admin only)
    """
    auth_service = AuthService(db)
    # No need for manual validation - require_super_admin() handles it
    
    result = await auth_service.create_hospital(hospital_data.dict())
    
    return SuccessResponse(
        success=True,
        message="Hospital created successfully",
        data=result
    ).dict()


@router.post("/super-admin/hospitals/{hospital_id}/admins", status_code=status.HTTP_201_CREATED)
async def create_hospital_admin(
    hospital_id: str,
    admin_data: HospitalAdminCreate,
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[HospitalAdminOut]:
    """
    Create hospital admin (Super Admin only)
    Returns temporary password - no OTP needed
    """
    auth_service = AuthService(db)
    # No need for manual validation - require_super_admin() handles it
    
    result = await auth_service.create_hospital_admin(
        uuid.UUID(hospital_id),
        admin_data.dict()
    )
    
    return SuccessResponse(
        success=True,
        message="Hospital admin created successfully",
        data=HospitalAdminOut(
            user_id=result["user_id"],
            email=result["email"]
        )
    ).dict()


# ============================================================================
# HOSPITAL ADMIN ENDPOINTS
# ============================================================================

@router.post("/hospital-admin/change-password")
async def hospital_admin_change_password(
    change_data: PasswordChangeUpdate,
    current_user: User = Depends(require_hospital_admin()),
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[Dict[str, str]]:
    """
    Hospital Admin change password
    """
    auth_service = AuthService(db)
    # No need for manual validation - require_hospital_admin() handles it
    
    result = await auth_service.change_password(
        current_user.id,
        change_data.current_password,
        change_data.new_password
    )
    
    return SuccessResponse(
        success=True,
        message="Password changed successfully",
        data={"status": "success"}
    ).dict()


# ============================================================================
# STAFF ENDPOINTS
# ============================================================================

@router.post("/staff/change-password")
async def staff_change_password(
    change_data: PasswordChangeUpdate,
    current_user: User = Depends(require_staff()),
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[Dict[str, str]]:
    """
    Staff change password
    """
    auth_service = AuthService(db)
    # No need for manual validation - require_staff() handles it
    
    result = await auth_service.change_password(
        current_user.id,
        change_data.current_password,
        change_data.new_password
    )
    
    return SuccessResponse(
        success=True,
        message="Password changed successfully",
        data={"status": "success"}
    ).dict()


# ============================================================================
# PATIENT ENDPOINTS
# ============================================================================

@router.get("/hospitals")
async def get_available_hospitals(
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[List[HospitalOut]]:
    """
    Get list of available hospitals for patient registration
    Public endpoint - no authentication required
    """
    auth_service = AuthService(db)
    hospitals = await auth_service.get_available_hospitals()
    
    return SuccessResponse(
        success=True,
        message="Hospitals retrieved successfully",
        data=hospitals
    ).dict()


@router.post("/patient/register", status_code=status.HTTP_201_CREATED)
async def patient_register(
    registration_data: PatientRegistrationCreate,
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[Dict[str, str]]:
    """
    Patient self-registration - ONLY patients can register
    Sends OTP to email for verification
    """
    auth_service = AuthService(db)
    result = await auth_service.register_patient(registration_data.model_dump(mode="json"))
    # Include hospital_id and hospital_name when assigned (registration is the only place patients get hospital_id)
    return SuccessResponse(
        success=True,
        message="Patient registration successful. Please check your email for OTP verification.",
        data=result,
    ).dict()


@router.post("/patient/verify-otp")
async def patient_verify_otp(
    verification_data: OTPVerificationCreate,
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[Dict[str, str]]:
    """
    Patient email verification with OTP
    Account activated after successful verification
    """
    auth_service = AuthService(db)
    result = await auth_service.verify_email(
        verification_data.email,
        verification_data.otp_code
    )
    
    return SuccessResponse(
        success=True,
        message="Email verification successful. Your account is now active.",
        data={"status": "verified", "email": verification_data.email}
    ).dict()


@router.post("/patient/login")
async def patient_login(
    login_data: LoginCreate,
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[AuthOut]:
    """
    Patient login - requires email verification first
    """
    auth_service = AuthService(db)
    result = await auth_service.patient_login(login_data.email, login_data.password)
    
    return SuccessResponse(
        success=True,
        message="Patient login successful",
        data=AuthOut(**result)
    ).dict()


@router.post("/patient/forgot-password")
async def patient_forgot_password(
    forgot_data: ForgotPasswordCreate,
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[Dict[str, str]]:
    """
    Patient forgot password - sends OTP to email
    """
    auth_service = AuthService(db)
    result = await auth_service.forgot_password(forgot_data.email)
    
    return SuccessResponse(
        success=True,
        message="Password reset OTP sent to your email",
        data={"status": "otp_sent", "email": forgot_data.email}
    ).dict()


@router.post("/patient/reset-password")
async def patient_reset_password(
    reset_data: PasswordResetCreate,
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[Dict[str, str]]:
    """
    Patient password reset with OTP verification
    """
    auth_service = AuthService(db)
    result = await auth_service.reset_password(
        reset_data.email,
        reset_data.otp_code,
        reset_data.new_password
    )
    
    return SuccessResponse(
        success=True,
        message="Password reset successful",
        data={"status": "password_reset", "email": reset_data.email}
    ).dict()


@router.post("/patient/change-password")
async def patient_change_password(
    change_data: PasswordChangeUpdate,
    current_user: User = Depends(require_patient()),
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[Dict[str, str]]:
    """
    Patient change password (authenticated)
    """
    auth_service = AuthService(db)
    # No need for manual validation - require_patient() handles it
    
    result = await auth_service.change_password(
        current_user.id,
        change_data.current_password,
        change_data.new_password
    )
    
    return SuccessResponse(
        success=True,
        message="Password changed successfully",
        data={"status": "success"}
    ).dict()


# ============================================================================
# COMMON ENDPOINTS (All User Types)
# ============================================================================

@router.post("/logout")
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[Dict[str, str]]:
    """
    Universal logout for all user types
    """
    # TODO: Implement token blacklisting when Redis is added
    return SuccessResponse(
        success=True,
        message="Logged out successfully",
        data={"status": "logged_out"}
    ).dict()


@router.get("/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
) -> APIResponse[UserInfoOut]:
    """
    Get current authenticated user information (all user types)
    """
    auth_service = AuthService(db)
    user_info = await auth_service.get_current_user_info(current_user)
    
    return SuccessResponse(
        success=True,
        message="User information retrieved successfully",
        data=user_info
    ).dict()