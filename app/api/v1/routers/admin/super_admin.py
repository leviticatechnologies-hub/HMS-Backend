"""
Super Admin API endpoints for platform-level administrative operations.
Handles hospital management, subscription control, analytics, and compliance monitoring.
"""
import os
import uuid
import aiofiles
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm.attributes import flag_modified
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from app.api.deps import get_db_session, require_super_admin
from app.services.super_admin_service import SuperAdminService
from app.services.auth_service import AuthService
from app.models.user import User
from app.core.enums import UserRole, UserStatus, HospitalStatus
from app.schemas.admin import (
    HospitalUpdate, AdminStatusUpdate, HospitalStatusUpdate,
    HospitalAdminCreate, SubscriptionPlanCreate, SubscriptionPlanUpdate,
    PlanAssignmentCreate, HospitalListOut, HospitalDetailsOut,
    SuperAdminMeOut,
    SuperAdminMeUpdate,
    SuperAdminSecurityOut,
    SuperAdminSessionOut,
    SuperAdminPasswordChange,
)
from app.schemas.response import SuccessResponse
from app.core.utils import parse_date_string, absolute_public_asset_url

router = APIRouter(prefix="/super-admin")
# Legacy frontend paths used `super_admin` + snake_case segments; keep aliases mounted separately.
router_super_admin_compat = APIRouter(prefix="/super_admin", tags=["Super Admin - Profile Settings (compat)"])

_SUPER_ADMIN_SECURITY_META_KEY = "super_admin_security"


def _user_metadata_as_dict(user: User) -> Dict[str, Any]:
    raw = user.user_metadata
    if raw is None or not isinstance(raw, dict):
        return {}
    return dict(raw)


def _default_security_preferences() -> Dict[str, Any]:
    return {
        "enable_login_alerts": True,
        "enable_suspicious_activity_alerts": True,
        "inactivity_timeout_minutes": 30,
        "enable_account_auto_lock": True,
        "active_sessions": [],
    }


def _parse_session_items(raw_sessions: Any) -> List[SuperAdminSessionOut]:
    if not isinstance(raw_sessions, list):
        return []
    out: List[SuperAdminSessionOut] = []
    for item in raw_sessions:
        if not isinstance(item, dict):
            continue
        try:
            out.append(SuperAdminSessionOut.model_validate(item))
        except Exception:
            continue
    return out


def _safe_int_timeout_minutes(raw: Any, default: int = 30) -> int:
    """Avoid 500s when user_metadata has a non-numeric inactivity_timeout_minutes."""
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return default
    return max(5, min(24 * 60, v))


def _safe_bool_pref(raw: Any, default: bool = True) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    s = str(raw).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return default


def build_super_admin_me(user: User) -> SuperAdminMeOut:
    """Assemble profile + security block for Super Admin settings UI."""
    md = _user_metadata_as_dict(user)
    stored = md.get(_SUPER_ADMIN_SECURITY_META_KEY)
    if not isinstance(stored, dict):
        stored = {}
    base_prefs = _default_security_preferences()
    merged_prefs = {**base_prefs, **{k: v for k, v in stored.items() if k in base_prefs}}

    sessions = _parse_session_items(merged_prefs.get("active_sessions"))
    totp_on = bool(getattr(user, "totp_enabled", False))

    security = SuperAdminSecurityOut(
        is_two_factor_enabled=totp_on,
        enable_login_alerts=_safe_bool_pref(merged_prefs.get("enable_login_alerts"), True),
        enable_suspicious_activity_alerts=_safe_bool_pref(
            merged_prefs.get("enable_suspicious_activity_alerts"), True
        ),
        inactivity_timeout_minutes=_safe_int_timeout_minutes(
            merged_prefs.get("inactivity_timeout_minutes"), 30
        ),
        enable_account_auto_lock=_safe_bool_pref(
            merged_prefs.get("enable_account_auto_lock"), True
        ),
        active_sessions=sessions,
    )

    fn = user.first_name or ""
    ln = user.last_name or ""
    full = f"{fn} {ln}".strip()

    return SuperAdminMeOut(
        first_name=fn,
        last_name=ln,
        full_name=full or fn or ln,
        email=user.email or "",
        phone_number=user.phone if user.phone is not None else "",
        profile_picture_url=absolute_public_asset_url(user.avatar_url),
        middle_name=user.middle_name,
        timezone=user.timezone,
        language=user.language,
        security=security,
    )


# ============================================================================
# DEPENDENCY FUNCTIONS
# ============================================================================

async def get_super_admin_service(db: AsyncSession = Depends(get_db_session)) -> SuperAdminService:
    """Get Super Admin service instance"""
    return SuperAdminService(db)


async def _bind_super_admin_user_to_db(db: AsyncSession, current_user: User) -> User:
    """
    Re-load the authenticated user in the same DB session used for writes.
    Avoids cross-session refresh/commit errors for profile update endpoints.
    """
    db_user = await db.get(User, current_user.id)
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "USER_NOT_FOUND", "message": "Authenticated user not found"},
        )
    return db_user


class SuperAdminProfileOut(BaseModel):
    email: str
    first_name: str
    last_name: str
    phone: str
    middle_name: Optional[str] = None
    avatar_url: Optional[str] = None
    timezone: Optional[str] = None
    language: Optional[str] = None


class SuperAdminProfileUpdate(BaseModel):
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    middle_name: Optional[str] = Field(None, max_length=100)
    avatar_url: Optional[str] = Field(None, max_length=500)
    timezone: Optional[str] = Field(None, max_length=50)
    language: Optional[str] = Field(None, max_length=10)


# ============================================================================
# SUPER ADMIN PROFILE SETTINGS
# ============================================================================


@router.get("/profile", response_model=SuccessResponse[SuperAdminProfileOut], tags=["Super Admin - Profile Settings"])
async def get_super_admin_profile(
    current_user: User = Depends(require_super_admin()),
):
    data = SuperAdminProfileOut(
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        phone=current_user.phone,
        middle_name=current_user.middle_name,
        avatar_url=absolute_public_asset_url(current_user.avatar_url),
        timezone=current_user.timezone,
        language=current_user.language,
    )
    return SuccessResponse(success=True, message="OK", data=data)


@router.patch("/profile", response_model=SuccessResponse[SuperAdminProfileOut], tags=["Super Admin - Profile Settings"])
async def update_super_admin_profile(
    body: SuperAdminProfileUpdate,
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db_session),
):
    current_user = await _bind_super_admin_user_to_db(db, current_user)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(current_user, k, v)
    await db.commit()
    await db.refresh(current_user)
    data = SuperAdminProfileOut(
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        phone=current_user.phone,
        middle_name=current_user.middle_name,
        avatar_url=absolute_public_asset_url(current_user.avatar_url),
        timezone=current_user.timezone,
        language=current_user.language,
    )
    return SuccessResponse(success=True, message="Profile updated", data=data)


@router.post("/profile", response_model=SuccessResponse[SuperAdminProfileOut], tags=["Super Admin - Profile Settings"])
async def update_super_admin_profile_post_compat(
    body: SuperAdminProfileUpdate,
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db_session),
):
    """Backward-compatible alias for clients still sending POST for profile updates."""
    return await update_super_admin_profile(body=body, current_user=current_user, db=db)


@router.get("/me", response_model=SuccessResponse[SuperAdminMeOut], tags=["Super Admin - Profile Settings"])
async def get_super_admin_me(
    current_user: User = Depends(require_super_admin()),
):
    """
    Current Super Admin profile for the settings UI (Personal + Security summary).
    Two-factor status reflects TOTP (`totp_enabled`); enroll via `/api/v1/auth/2fa/*`.
    """
    return SuccessResponse(
        success=True,
        message="OK",
        data=build_super_admin_me(current_user),
    )


@router.patch("/me", response_model=SuccessResponse[SuperAdminMeOut], tags=["Super Admin - Profile Settings"])
async def update_super_admin_me(
    body: SuperAdminMeUpdate,
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Update Super Admin profile fields and security preferences stored in `user_metadata`.

    Does **not** enable or disable TOTP — use `/api/v1/auth/2fa/setup`, `/verify`, and `/disable` for 2FA.
    Password changes are not handled here (use a dedicated change-password flow when available).
    """
    current_user = await _bind_super_admin_user_to_db(db, current_user)
    payload = body.model_dump(exclude_unset=True)

    if "email" in payload and payload["email"] is not None:
        new_email = str(payload["email"]).strip()
        current_email_norm = (current_user.email or "").strip().lower()
        new_email_norm = new_email.lower()
        if new_email_norm != current_email_norm:
            dup = await db.execute(
                select(User.id).where(
                    and_(func.lower(User.email) == new_email_norm, User.id != current_user.id)
                )
            )
            if dup.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "EMAIL_IN_USE", "message": "This email is already registered"},
                )
            current_user.email = new_email_norm

    if "first_name" in payload:
        current_user.first_name = payload["first_name"] or ""
    if "last_name" in payload:
        current_user.last_name = payload["last_name"] or ""
    if "middle_name" in payload:
        current_user.middle_name = payload["middle_name"]
    if "phone_number" in payload:
        current_user.phone = payload["phone_number"] if payload["phone_number"] is not None else ""
    if "profile_picture_url" in payload:
        current_user.avatar_url = payload["profile_picture_url"]
    if "timezone" in payload:
        current_user.timezone = payload["timezone"]
    if "language" in payload:
        current_user.language = payload["language"]

    if body.security is not None:
        sec_updates = body.security.model_dump(exclude_unset=True)
        if sec_updates:
            md = _user_metadata_as_dict(current_user)
            existing = md.get(_SUPER_ADMIN_SECURITY_META_KEY)
            if not isinstance(existing, dict):
                existing = {}
            merged = {**_default_security_preferences(), **existing}
            for key, val in sec_updates.items():
                if key in merged:
                    merged[key] = val
            md[_SUPER_ADMIN_SECURITY_META_KEY] = merged
            current_user.user_metadata = md
            flag_modified(current_user, "user_metadata")

    await db.commit()
    await db.refresh(current_user)

    return SuccessResponse(
        success=True,
        message="Profile updated",
        data=build_super_admin_me(current_user),
    )


async def _super_admin_change_password_impl(
    body: SuperAdminPasswordChange,
    current_user: User,
    db: AsyncSession,
) -> SuccessResponse:
    """Shared handler: session-bound user + auth service."""
    current_user = await _bind_super_admin_user_to_db(db, current_user)
    auth_service = AuthService(db)
    await auth_service.change_password(
        current_user.id,
        body.current_password,
        body.new_password,
    )
    return SuccessResponse(
        success=True,
        message="Password changed successfully",
        data={"status": "success"},
    )


@router.post("/me/change-password", tags=["Super Admin - Profile Settings"])
@router.post("/me/change_password", tags=["Super Admin - Profile Settings"], include_in_schema=False)
async def super_admin_change_password(
    body: SuperAdminPasswordChange,
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db_session),
):
    """Change Super Admin password (current + new + confirm). Uses same rules as other roles."""
    return await _super_admin_change_password_impl(body, current_user, db)


@router_super_admin_compat.post("/me/change-password", include_in_schema=False)
@router_super_admin_compat.post("/me/change_password", include_in_schema=False)
async def super_admin_change_password_legacy_prefix(
    body: SuperAdminPasswordChange,
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db_session),
):
    """Legacy: `POST /api/v1/super_admin/me/change_password` (underscore prefix + segment)."""
    return await _super_admin_change_password_impl(body, current_user, db)


_SUPERADMIN_AVATAR_MAX_BYTES = 5 * 1024 * 1024
_SUPERADMIN_AVATAR_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
}


@router.post("/me/avatar", response_model=SuccessResponse[SuperAdminMeOut], tags=["Super Admin - Profile Settings"])
async def upload_super_admin_avatar(
    file: Optional[UploadFile] = File(None, description="Profile image: JPG, PNG, or GIF; max 5MB"),
    avatar: Optional[UploadFile] = File(None, description="Compatibility alias for file"),
    image: Optional[UploadFile] = File(None, description="Compatibility alias for file"),
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Upload a profile picture for the Super Admin. Saves under `/uploads/superadmin_avatars/`
    and sets `profile_picture_url` on GET `/super-admin/me`.
    """
    current_user = await _bind_super_admin_user_to_db(db, current_user)
    upload = file or avatar or image
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "MISSING_AVATAR_FILE",
                "message": "Upload file is required (multipart field: file, avatar, or image)",
            },
        )

    ct = (upload.content_type or "").split(";")[0].strip().lower()
    if ct not in _SUPERADMIN_AVATAR_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_AVATAR_TYPE",
                "message": "Allowed types: JPG, PNG, GIF",
            },
        )
    ext = _SUPERADMIN_AVATAR_TYPES[ct]
    upload_root = os.path.join("uploads", "superadmin_avatars")
    os.makedirs(upload_root, exist_ok=True)
    out_name = f"{current_user.id}{ext}"
    dest = os.path.join(upload_root, out_name)
    try:
        for name in os.listdir(upload_root):
            if name.startswith(str(current_user.id) + "."):
                try:
                    os.remove(os.path.join(upload_root, name))
                except OSError:
                    pass
    except OSError:
        pass

    total = 0
    async with aiofiles.open(dest, "wb") as out_f:
        while True:
            chunk = await upload.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > _SUPERADMIN_AVATAR_MAX_BYTES:
                try:
                    os.remove(dest)
                except OSError:
                    pass
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "AVATAR_TOO_LARGE",
                        "message": "Maximum file size is 5MB",
                    },
                )
            await out_f.write(chunk)

    public_url = f"/uploads/superadmin_avatars/{out_name}"
    current_user.avatar_url = public_url
    await db.commit()
    await db.refresh(current_user)
    return SuccessResponse(
        success=True,
        message="Profile picture updated",
        data=build_super_admin_me(current_user),
    )


# ============================================================================
# HOSPITAL MANAGEMENT ENDPOINTS
# ============================================================================

@router.get("/hospitals", response_model=HospitalListOut, tags=["Super Admin - Hospital Management"])
async def list_hospitals(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by hospital status"),
    subscription: Optional[str] = Query(None, description="Filter by subscription plan"),
    city: Optional[str] = Query(None, description="Filter by city"),
    state: Optional[str] = Query(None, description="Filter by state"),
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Get paginated list of hospitals with filtering options.
    
    Supports filtering by:
    - Hospital status (active, inactive, suspended)
    - Subscription plan (FREE, STANDARD, PREMIUM)
    - City and state
    """
    result = await service.get_hospitals(
        page=page,
        limit=limit,
        status_filter=status,
        subscription_filter=subscription,
        city_filter=city,
        state_filter=state
    )
    return result


@router.get("/hospitals/{hospital_id}", response_model=HospitalDetailsOut, tags=["Super Admin - Hospital Management"])
async def get_hospital_details(
    hospital_id: str,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Get detailed information about a specific hospital.
    
    Returns complete hospital information including:
    - Basic hospital details
    - Subscription information
    - Usage metrics
    - Administrator contact information
    """
    try:
        hospital_uuid = uuid.UUID(hospital_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_HOSPITAL_ID", "message": "Invalid hospital ID format"}
        )
    
    result = await service.get_hospital_details(hospital_uuid)
    return result


@router.put("/hospitals/{hospital_id}", tags=["Super Admin - Hospital Management"])
async def update_hospital(
    hospital_id: str,
    update_data: HospitalUpdate,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Update hospital information.
    
    Allows updating hospital details with proper validation:
    - Ensures registration number uniqueness
    - Maintains audit trail
    - Validates all input fields
    """
    try:
        hospital_uuid = uuid.UUID(hospital_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_HOSPITAL_ID", "message": "Invalid hospital ID format"}
        )
    
    # Convert to dict, excluding None values
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    
    if not update_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NO_UPDATE_DATA", "message": "No valid update data provided"}
        )
    
    result = await service.update_hospital(hospital_uuid, update_dict)
    return result


@router.patch("/hospitals/{hospital_id}/status", tags=["Super Admin - Hospital Management"])
async def update_hospital_status(
    hospital_id: str,
    status_data: HospitalStatusUpdate,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Update hospital status (ACTIVE, SUSPENDED, INACTIVE).
    
    Status changes affect:
    - User access to the hospital tenant
    - Subscription billing
    - System notifications
    """
    try:
        hospital_uuid = uuid.UUID(hospital_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_HOSPITAL_ID", "message": "Invalid hospital ID format"}
        )
    
    valid_statuses = {
        HospitalStatus.ACTIVE.value,
        HospitalStatus.SUSPENDED.value,
        HospitalStatus.INACTIVE.value,
    }
    new_status = (status_data.status or "").strip().upper()
    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_STATUS",
                "message": f"Invalid status. Valid options: {', '.join(sorted(valid_statuses))}",
            },
        )

    result = await service.update_hospital_status(hospital_uuid, new_status)
    return result


@router.delete("/hospitals/{hospital_id}", tags=["Super Admin - Hospital Management"])
async def delete_hospital(
    hospital_id: str,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    """
    Soft-delete hospital (sets INACTIVE, blocks tenant users). Super Admin JWT is sufficient.
    Returns standard `{ success, message, data }` for frontend clients.
    """
    try:
        hospital_uuid = uuid.UUID(hospital_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_HOSPITAL_ID", "message": "Invalid hospital ID format"},
        )
    result = await service.delete_hospital(hospital_uuid)
    return SuccessResponse(
        success=True,
        message=result.get("message", "Hospital deactivated successfully"),
        data=result,
    ).dict()


@router.post("/hospitals/{hospital_id}/deactivate", tags=["Super Admin - Hospital Management"])
async def deactivate_hospital_post(
    hospital_id: str,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    """
    Same as DELETE /hospitals/{id} for environments where DELETE is blocked or axios/fetch misconfigured.
    """
    try:
        hospital_uuid = uuid.UUID(hospital_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_HOSPITAL_ID", "message": "Invalid hospital ID format"},
        )
    result = await service.delete_hospital(hospital_uuid)
    return SuccessResponse(
        success=True,
        message=result.get("message", "Hospital deactivated successfully"),
        data=result,
    ).dict()


# ============================================================================
# HOSPITAL ADMINISTRATOR MANAGEMENT ENDPOINTS
# ============================================================================

@router.get("/hospitals/{hospital_id}/admins", tags=["Super Admin - Hospital Administrator Management"])
async def list_hospital_admins(
    hospital_id: str,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Get list of administrators for a specific hospital.
    
    Returns administrator information including:
    - Contact details
    - Account status
    - Last login information
    - Creation date
    """
    try:
        hospital_uuid = uuid.UUID(hospital_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_HOSPITAL_ID", "message": "Invalid hospital ID format"}
        )
    
    result = await service.get_hospital_admins(hospital_uuid)
    return {"admins": result}


@router.post("/hospitals/{hospital_id}/admins", status_code=status.HTTP_201_CREATED, tags=["Super Admin - Hospital Administrator Management"])
async def create_hospital_admin(
    hospital_id: str,
    admin_data: HospitalAdminCreate,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Create a new hospital administrator.
    
    Creates a hospital admin with:
    - Secure password provided by Super Admin
    - Email domain validation against hospital approved domains
    - Immediate account activation (no email verification needed)
    """
    try:
        hospital_uuid = uuid.UUID(hospital_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_HOSPITAL_ID", "message": "Invalid hospital ID format"}
        )
    
    # Use existing auth service for admin creation
    from app.services.auth_service import AuthService
    auth_service = AuthService(service.db)
    
    result = await auth_service.create_hospital_admin(
        hospital_uuid,
        admin_data.model_dump()
    )
    return result


@router.patch("/hospital-admins/{admin_id}/status", tags=["Super Admin - Hospital Administrator Management"])
async def update_admin_status(
    admin_id: str,
    status_data: AdminStatusUpdate,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Update hospital administrator status.
    
    Supported status changes:
    - ACTIVE: Administrator can access the system
    - BLOCKED: Administrator access is blocked
    - PENDING: Administrator account is pending activation
    
    Status changes trigger notification emails and audit logs.
    """
    try:
        admin_uuid = uuid.UUID(admin_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_ADMIN_ID", "message": "Invalid administrator ID format"}
        )
    
    # Validate status value
    valid_statuses = [UserStatus.ACTIVE, UserStatus.BLOCKED, UserStatus.PENDING]
    if status_data.status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_STATUS", 
                "message": f"Invalid status. Valid options: {', '.join(valid_statuses)}"
            }
        )
    
    result = await service.update_admin_status(admin_uuid, status_data.status)
    return result


@router.post("/hospital-admins/{admin_id}/reset-password", tags=["Super Admin - Hospital Administrator Management"])
async def reset_admin_password(
    admin_id: str,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Reset hospital administrator password.
    Generates a new secure temporary password. Share it securely with the admin.
    """
    try:
        admin_uuid = uuid.UUID(admin_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_ADMIN_ID", "message": "Invalid administrator ID format"})
    result = await service.reset_admin_password(admin_uuid)
    return result


# ============================================================================
# SUBSCRIPTION PLAN MANAGEMENT ENDPOINTS
# ============================================================================

@router.post("/plans", status_code=status.HTTP_201_CREATED, tags=["Super Admin - Subscription Plan Management"])
async def create_subscription_plan(
    plan_data: SubscriptionPlanCreate,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Create a new subscription plan.
    
    Defines feature sets, user limits, and billing parameters for hospital subscriptions.
    """
    # Validate plan name
    from app.core.enums import SubscriptionPlan
    valid_plan_names = [SubscriptionPlan.FREE, SubscriptionPlan.STANDARD, SubscriptionPlan.PREMIUM]
    if plan_data.name not in valid_plan_names:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_PLAN_NAME", 
                "message": f"Invalid plan name. Valid options: {', '.join(valid_plan_names)}"
            }
        )
    
    result = await service.create_subscription_plan(plan_data.model_dump())
    return result


@router.get("/plans", tags=["Super Admin - Subscription Plan Management"])
async def list_subscription_plans(
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    List all subscription plans.
    
    Returns all available subscription plans with their features and pricing.
    """
    plans = await service.get_subscription_plans()
    return {"plans": plans}


@router.put("/plans/{plan_id}", tags=["Super Admin - Subscription Plan Management"])
async def update_subscription_plan(
    plan_id: str,
    plan_data: SubscriptionPlanUpdate,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Update an existing subscription plan.
    
    Handles plan updates with version control and migration paths for existing subscribers.
    """
    try:
        plan_uuid = uuid.UUID(plan_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_PLAN_ID", "message": "Invalid plan ID format"}
        )
    
    # Convert to dict, excluding None values
    update_dict = {k: v for k, v in plan_data.dict().items() if v is not None}
    
    if not update_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NO_UPDATE_DATA", "message": "No valid update data provided"}
        )
    
    result = await service.update_subscription_plan(plan_uuid, update_dict)
    return result


@router.delete("/plans/{plan_id}", tags=["Super Admin - Subscription Plan Management"])
async def delete_subscription_plan(
    plan_id: str,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Delete a subscription plan.
    
    Can only delete plans that have no active subscribers.
    """
    try:
        plan_uuid = uuid.UUID(plan_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_PLAN_ID", "message": "Invalid plan ID format"}
        )
    
    result = await service.delete_subscription_plan(plan_uuid)
    return result


# ============================================================================
# HOSPITAL SUBSCRIPTION MANAGEMENT ENDPOINTS
# ============================================================================

@router.post("/hospitals/{hospital_name}/assign-plan", tags=["Super Admin - Hospital Subscription Management"])
async def assign_subscription_plan(
    hospital_name: str,
    assignment_data: PlanAssignmentCreate,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Assign a subscription plan to a hospital using hospital name and plan name.
    
    Handles plan assignments, upgrades, downgrades, and feature transitions.
    """
    result = await service.assign_subscription_plan_by_names(
        hospital_name, 
        assignment_data.plan_name, 
        assignment_data.model_dump()
    )
    return result


@router.get("/hospitals/{hospital_name}/subscription", tags=["Super Admin - Hospital Subscription Management"])
async def get_hospital_subscription(
    hospital_name: str,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Get hospital subscription details using hospital name.
    
    Returns current subscription, usage metrics, and billing status.
    """
    result = await service.get_hospital_subscription_by_name(hospital_name)
    return result


# ============================================================================
# SUPPORT TICKET MANAGEMENT ENDPOINTS
# ============================================================================

# NOTE: Super Admin can no longer create tickets directly.
# Tickets must be created by Hospital Admin or Staff.


@router.get("/support/tickets", tags=["Super Admin - Support Management"])
async def list_support_tickets(
    hospital_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """List support tickets with optional filters."""
    h_uuid = uuid.UUID(hospital_id) if hospital_id else None
    result = await service.list_support_tickets(hospital_id=h_uuid, status=status_filter, skip=skip, limit=limit)
    return result


@router.patch("/support/tickets/{ticket_id}/status", tags=["Super Admin - Support Management"])
async def update_support_ticket_status(
    ticket_id: str,
    status_data: Dict[str, Any],
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Update support ticket status.
    Handles escalated support tickets requiring Super Admin intervention.
    Body: {"status": "RESOLVED", "resolution_notes": "...", "assigned_to_user_id": "uuid" (optional)}
    """
    try:
        t_uuid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_TICKET_ID", "message": "Invalid ticket ID format"})
    new_status = status_data.get("status")
    if not new_status:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "MISSING_STATUS", "message": "status is required"})
    resolution_notes = status_data.get("resolution_notes")
    assigned_to = status_data.get("assigned_to_user_id")
    assigned_uuid = uuid.UUID(assigned_to) if assigned_to else None
    result = await service.update_support_ticket_status(
        t_uuid,
        new_status,
        resolution_notes=resolution_notes,
        assigned_to_user_id=assigned_uuid,
    )

    # On resolve/close, email the person who raised the ticket (users always on platform DB).
    if str(new_status).upper() in {"RESOLVED", "CLOSED"}:
        try:
            from sqlalchemy import select
            from app.models.user import User as UserModel
            from app.services.email_service import EmailService
            from app.database.session import AsyncSessionLocal

            raised_by = result.get("raised_by_user_id")
            if raised_by:
                async with AsyncSessionLocal() as pdb:
                    user_r = await pdb.execute(
                        select(UserModel.email).where(UserModel.id == uuid.UUID(str(raised_by))).limit(1)
                    )
                    email = user_r.scalar_one_or_none()
                    if email:
                        notes = resolution_notes or ""
                        subject = f"Support Ticket {t_uuid} marked {str(new_status).upper()}"
                        html = f"""
                        <p>Hello,</p>
                        <p>Your support ticket has been updated.</p>
                        <p><b>Ticket ID:</b> {t_uuid}</p>
                        <p><b>Status:</b> {str(new_status).upper()}</p>
                        <p><b>Notes:</b> {notes}</p>
                        <p>Regards,<br/>Support Team</p>
                        """
                        text = f"Ticket {t_uuid} status: {str(new_status).upper()}\nNotes: {notes}"
                        await EmailService().send_email(str(email), subject, html, text)
        except Exception:
            pass

    return result


# ============================================================================
# ANALYTICS & MONITORING
# ============================================================================

@router.get("/analytics/overview", tags=["Super Admin - Analytics & Monitoring"])
async def get_platform_analytics(
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """Dashboard overview KPI cards: total appointments, beds, billing, doctors; subscription breakdown."""
    result = await service.get_platform_analytics()
    return result


@router.get("/dashboard/overview-cards", tags=["Super Admin - Analytics & Monitoring"])
async def get_dashboard_overview_cards(
    period_days: int = Query(
        30,
        ge=1,
        le=365,
        description="Window length in days for growth % (current vs previous window of same length).",
    ),
    trend_months: int = Query(
        6,
        ge=1,
        le=24,
        description="Number of calendar months of per-month trend points for charts.",
    ),
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    """
    **Super Admin dashboard top cards** (Levitica-style overview):

    - **total_hospitals**: Count of hospitals with `status=ACTIVE` and `is_active=true`.
    - **active_plans**: Count of `ACTIVE` subscriptions on a **non-FREE** plan (paid tiers).
    - **platform_revenue**: Sum of all **SUCCESS** `BillingPayment` rows (all hospitals; amounts as stored).

    **growth_percent** compares the **current** rolling window to the **previous** window of `period_days`
    (new hospitals, new paid subscriptions, and revenue with `paid_at` in window). Use **trend** for bar/sparkline charts.
    """
    data = await service.get_dashboard_overview_cards(
        period_days=period_days,
        trend_months=trend_months,
    )
    return SuccessResponse(success=True, message="Dashboard overview cards", data=data).dict()


# ============================================================================
# SUBSCRIPTION / FINANCIAL / PERFORMANCE ANALYTICS
# ============================================================================

@router.get("/subscription-analytics", tags=["Super Admin - Analytics & Monitoring"])
async def get_subscription_analytics(
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    """Subscription analytics for dashboard (summary + table + charts)."""
    data = await service.get_subscription_analytics()
    return SuccessResponse(success=True, message="Subscription analytics", data=data).dict()

class AnalyticsFilter(BaseModel):
    date_from: Optional[str] = None  # YYYY-MM-DD or ISO
    date_to: Optional[str] = None
    plan_name: Optional[str] = None  # FREE/STANDARD/PREMIUM
    status: Optional[str] = None  # ACTIVE/EXPIRED/CANCELLED/SUSPENDED

@router.post("/subscription-analytics", tags=["Super Admin - Analytics & Monitoring"])
async def get_subscription_analytics_filtered(
    body: AnalyticsFilter,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    df = parse_date_string(body.date_from) if body.date_from else None
    dt = parse_date_string(body.date_to) if body.date_to else None
    data = await service.get_subscription_analytics(
        date_from=df,
        date_to=dt,
        plan_name=body.plan_name,
        status=body.status,
    )
    return SuccessResponse(success=True, message="Subscription analytics", data=data).dict()

@router.get("/financial-analytics", tags=["Super Admin - Analytics & Monitoring"])
async def get_financial_analytics(
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    """Financial analytics for dashboard (summary + transactions + charts)."""
    data = await service.get_financial_analytics()
    return SuccessResponse(success=True, message="Financial analytics", data=data).dict()

class FinancialAnalyticsFilter(BaseModel):
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    hospital_id: Optional[str] = None  # UUID

@router.post("/financial-analytics", tags=["Super Admin - Analytics & Monitoring"])
async def get_financial_analytics_filtered(
    body: FinancialAnalyticsFilter,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    import uuid as _uuid
    df = parse_date_string(body.date_from) if body.date_from else None
    dt = parse_date_string(body.date_to) if body.date_to else None
    hid = _uuid.UUID(body.hospital_id) if body.hospital_id else None
    data = await service.get_financial_analytics(date_from=df, date_to=dt, hospital_id=hid)
    return SuccessResponse(success=True, message="Financial analytics", data=data).dict()


@router.get("/performance-analytics", tags=["Super Admin - Analytics & Monitoring"])
async def get_performance_analytics(
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    """Platform performance analytics (best-effort; no full telemetry stored yet)."""
    data = await service.get_performance_analytics()
    return SuccessResponse(success=True, message="Performance analytics", data=data).dict()


@router.get("/audit-logs", tags=["Super Admin - Analytics & Monitoring"])
async def get_audit_logs(
    resource_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """Get platform audit logs across all hospitals."""
    result = await service.get_platform_audit_logs(skip=skip, limit=limit, resource_type=resource_type)
    return result


# ============================================================================
# NOTIFICATIONS TO HOSPITAL ADMINS
# ============================================================================

class NotifyHospitalAdminsRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "hospital_name": "City General Hospital",
                    "subject": "Scheduled maintenance",
                    "message": "The admin portal will be unavailable Saturday 02:00–04:00 UTC.",
                },
                {
                    "hospital_id": "550e8400-e29b-41d4-a716-446655440000",
                    "subject": "Scheduled maintenance",
                    "message": "The admin portal will be unavailable Saturday 02:00–04:00 UTC.",
                },
            ]
        }
    }

    hospital_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Target hospital (preferred). Only users with the Hospital Admin role and this hospital_id receive the notification.",
    )
    hospital_name: Optional[str] = Field(
        default=None,
        description="Name of the target hospital; matched case-insensitively. Alternative to hospital_id.",
    )
    notify_all_hospitals: bool = Field(
        default=False,
        description="If true, notify every Hospital Admin on the platform. Do not combine with hospital_id or hospital_name.",
    )
    subject: str = Field(
        ...,
        description="Title of the notification (email subject line).",
        examples=["Scheduled maintenance"],
    )
    message: str = Field(
        ...,
        description="Content of the notification (email body).",
        examples=["The admin portal will be unavailable Saturday 02:00–04:00 UTC."],
    )


@router.post(
    "/notifications/send-to-hospital-admins",
    tags=["Super Admin - Notifications"],
    summary="Send notifications to Hospital Admins of a hospital",
    response_description="Queued job counts; includes hospital_id when a single hospital was targeted.",
)
async def notify_hospital_admins(
    body: NotifyHospitalAdminsRequest,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
    db: AsyncSession = Depends(get_db_session),
):
    """
    **Super Admin → Hospital Admin notification (scoped by hospital)**

    Enables the Super Admin to send email notifications to **all Hospital Admin users** belonging to **one**
    specific hospital (or, if `notify_all_hospitals` is true, to every Hospital Admin on the platform).

    **Inputs**

    - **`hospital_name`** — Name of the target hospital (case-insensitive match). Use if you do not have the UUID.
    - **`hospital_id`** — Preferred: exact hospital UUID; avoids ambiguity when names are similar.
    - **`subject`** — Title of the notification.
    - **`message`** — Content of the notification.

    **Outcome**

    - If the hospital exists (`hospital_id` or `hospital_name`), notifications are **queued** for **each** Hospital
      Admin linked to that hospital (each with a valid email and `hospital_id`).
    - If the hospital does **not** exist, **404** is returned with `HOSPITAL_NOT_FOUND`.
    - If neither hospital targeting nor `notify_all_hospitals` is provided, **400** `HOSPITAL_SCOPE_REQUIRED`.

    Requires a **Super Admin** JWT.
    """
    from sqlalchemy import select, func
    from app.models.tenant import Hospital

    h_uuid: Optional[uuid.UUID] = None
    has_name = bool(body.hospital_name and str(body.hospital_name).strip())

    if body.notify_all_hospitals:
        if body.hospital_id is not None or has_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "CONFLICTING_HOSPITAL_SCOPE",
                    "message": "Do not set hospital_id or hospital_name when notify_all_hospitals is true.",
                },
            )
        h_uuid = None
    elif body.hospital_id is not None:
        r = await db.execute(select(Hospital).where(Hospital.id == body.hospital_id).limit(1))
        hospital = r.scalar_one_or_none()
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": f"No hospital found with id: {body.hospital_id}"},
            )
        h_uuid = hospital.id
    elif has_name:
        name = str(body.hospital_name).strip()
        r = await db.execute(select(Hospital).where(func.lower(Hospital.name) == name.lower()).limit(1))
        hospital = r.scalar_one_or_none()
        if hospital:
            h_uuid = hospital.id
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": f"No hospital found with name: {name}"},
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "HOSPITAL_SCOPE_REQUIRED",
                "message": "Provide hospital_id, hospital_name, or set notify_all_hospitals to true.",
            },
        )

    result = await service.notify_hospital_admins(h_uuid, body.subject, body.message, current_user.id)
    return result