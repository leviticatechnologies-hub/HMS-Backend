"""
Super Admin API endpoints for platform-level administrative operations.
Handles hospital management, subscription control, analytics, and compliance monitoring.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from app.api.deps import get_db_session, require_super_admin
from app.services.super_admin_service import SuperAdminService
from app.models.user import User
from app.core.enums import UserRole, UserStatus, HospitalStatus
from app.schemas.admin import (
    HospitalUpdate, AdminStatusUpdate, HospitalStatusUpdate,
    HospitalAdminCreate, SubscriptionPlanCreate, SubscriptionPlanUpdate,
    PlanAssignmentCreate, HospitalListOut, HospitalDetailsOut,
    SuperAdminUserUpdate, SuperAdminUserStatusUpdate
)
from app.schemas.response import SuccessResponse
from app.core.utils import parse_date_string

router = APIRouter(prefix="/super-admin")


# ============================================================================
# DEPENDENCY FUNCTIONS
# ============================================================================

async def get_super_admin_service(db: AsyncSession = Depends(get_db_session)) -> SuperAdminService:
    """Get Super Admin service instance"""
    return SuperAdminService(db)


# Note: verify_super_admin function removed - using centralized require_super_admin() dependency


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
    
    # Validate status value
    valid_statuses = [HospitalStatus.ACTIVE, HospitalStatus.SUSPENDED, HospitalStatus.INACTIVE]
    if status_data.status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_STATUS", 
                "message": f"Invalid status. Valid options: {', '.join(valid_statuses)}"
            }
        )
    
    result = await service.update_hospital_status(hospital_uuid, status_data.status)
    return result


@router.delete("/hospitals/{hospital_id}", tags=["Super Admin - Hospital Management"])
async def delete_hospital(
    hospital_id: str,
    confirm: bool = Query(False, description="Must be true to confirm deletion"),
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service)
):
    """
    Soft delete hospital: set status INACTIVE, block all users.
    Requires confirm=true. Use with extreme caution.
    """
    try:
        hospital_uuid = uuid.UUID(hospital_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_HOSPITAL_ID", "message": "Invalid hospital ID format"})
    result = await service.delete_hospital(hospital_uuid, confirm=confirm)
    return result


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
# SUPER ADMIN - USER ACCOUNTS (NO POST CREATE)
# ============================================================================

@router.get("/users", tags=["Super Admin - User Accounts"])
async def get_all_users(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    """
    Get all hospital administrator users with their hospital details.
    """
    return await service.get_super_admin_users(page=page, limit=limit)


@router.put("/users/{user_id}", tags=["Super Admin - User Accounts"])
@router.patch("/users/{user_id}", tags=["Super Admin - User Accounts"])
async def update_user(
    user_id: str,
    payload: SuperAdminUserUpdate,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    """
    Update an existing hospital admin user + its hospital details.
    Note: admin_password is intentionally not supported here (POST create removed).
    """
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_USER_ID", "message": "Invalid user ID format"},
        )
    return await service.update_super_admin_user(uid, payload.model_dump())


@router.patch("/users/{user_id}/status", tags=["Super Admin - User Accounts"])
async def toggle_user_status(
    user_id: str,
    payload: SuperAdminUserStatusUpdate,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    """Toggle hospital admin ACTIVE / INACTIVE (INACTIVE maps to BLOCKED)."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_USER_ID", "message": "Invalid user ID format"},
        )
    return await service.set_super_admin_user_status(uid, payload.status)


@router.delete("/users/{user_id}", tags=["Super Admin - User Accounts"])
async def delete_user(
    user_id: str,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
):
    """Soft delete hospital admin user (sets status to BLOCKED)."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_USER_ID", "message": "Invalid user ID format"},
        )
    return await service.delete_super_admin_user(uid)


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

    # On resolve/close, email the person who raised the ticket.
    if str(new_status).upper() in {"RESOLVED", "CLOSED"}:
        try:
            from sqlalchemy import select
            from app.models.support import SupportTicket
            from app.models.user import User as UserModel
            from app.services.email_service import EmailService

            ticket_r = await service.db.execute(select(SupportTicket).where(SupportTicket.id == t_uuid).limit(1))
            ticket = ticket_r.scalar_one_or_none()
            if ticket:
                user_r = await service.db.execute(
                    select(UserModel.email).where(UserModel.id == ticket.raised_by_user_id).limit(1)
                )
                email = user_r.scalar_one_or_none()
                if email:
                    subject = f"Support Ticket {ticket.id} marked {str(new_status).upper()}"
                    notes = resolution_notes or ticket.resolution_notes or ""
                    html = f"""
                    <p>Hello,</p>
                    <p>Your support ticket has been updated.</p>
                    <p><b>Ticket ID:</b> {ticket.id}</p>
                    <p><b>Status:</b> {str(new_status).upper()}</p>
                    <p><b>Notes:</b> {notes}</p>
                    <p>Regards,<br/>Support Team</p>
                    """
                    text = f"Ticket {ticket.id} status: {str(new_status).upper()}\nNotes: {notes}"
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
    """Dashboard: hospitals, active subscriptions, total revenue, patient trends, occupancy rates."""
    result = await service.get_platform_analytics()
    return result


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
    hospital_name: Optional[str] = None  # If None, notify all hospital admins
    subject: str
    message: str


@router.post("/notifications/send-to-hospital-admins", tags=["Super Admin - Notifications"])
async def notify_hospital_admins(
    body: NotifyHospitalAdminsRequest,
    current_user: User = Depends(require_super_admin()),
    service: SuperAdminService = Depends(get_super_admin_service),
    db: AsyncSession = Depends(get_db_session),
):
    """Send email notification to hospital admin(s). Optionally filter by hospital_name."""
    from sqlalchemy import select, func
    from app.models.tenant import Hospital

    h_uuid = None
    if body.hospital_name:
        name = (body.hospital_name or "").strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "EMPTY_HOSPITAL_NAME", "message": "hospital_name cannot be empty"},
            )
        r = await db.execute(select(Hospital).where(func.lower(Hospital.name) == name.lower()).limit(1))
        hospital = r.scalar_one_or_none()
        if hospital:
            h_uuid = hospital.id
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": f"No hospital found with name: {name}"},
            )

    result = await service.notify_hospital_admins(h_uuid, body.subject, body.message, current_user.id)
    return result