"""
Super Admin service for platform-level administrative operations.
Handles hospital management, subscription control, analytics, and compliance monitoring.
"""
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc, asc
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
import re
import random
import string

from app.models.user import User, Role, AuditLog
from app.models.tenant import Hospital, SubscriptionPlanModel, HospitalSubscription
from app.core.enums import UserRole, UserStatus, SubscriptionStatus, SubscriptionPlan, AuditAction, HospitalStatus
from app.core.security import SecurityManager
from app.core.utils import parse_date_string


def generate_staff_id(role: str, department_name: str, first_name: str, last_name: str) -> str:
    """
    Generate a 10-character staff ID like an ID card.
    Format: [ROLE][DEPT][NAME][NUM]
    
    Examples:
    - DR-CARD-JS01 (Doctor, Cardiology, John Smith)
    - NR-ORTH-SJ02 (Nurse, Orthopedics, Sarah Johnson)
    - RC-EMER-MK03 (Receptionist, Emergency, Mike Kumar)
    """
    # Role codes (2 chars)
    role_codes = {
        "DOCTOR": "DR",
        "NURSE": "NR", 
        "RECEPTIONIST": "RC",
        "PHARMACIST": "PH",
        "LAB_TECH": "LT"
    }
    
    # Get role code
    role_code = role_codes.get(role.upper(), "ST")  # Default to ST (Staff)
    
    # Department code (4 chars) - take first 4 chars of department name
    dept_code = re.sub(r'[^A-Z]', '', department_name.upper())[:4]
    if len(dept_code) < 4:
        dept_code = dept_code.ljust(4, 'X')
    
    # Name code (2 chars) - first char of first name + first char of last name
    name_code = (first_name[0] + last_name[0]).upper()
    
    # Random number (2 chars)
    random_num = f"{random.randint(1, 99):02d}"
    
    # Combine: DR-CARD-JS01 (10 chars with dashes, 8 chars without)
    staff_id = f"{role_code}{dept_code}{name_code}{random_num}"
    
    return staff_id


class SuperAdminService:
    """Service class for Super Admin operations"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.security = SecurityManager()
    
    # ============================================================================
    # HOSPITAL MANAGEMENT
    # ============================================================================
    
    async def get_hospitals(
        self, 
        page: int = 1, 
        limit: int = 50,
        status_filter: Optional[str] = None,
        subscription_filter: Optional[str] = None,
        city_filter: Optional[str] = None,
        state_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get paginated list of hospitals with filtering"""
        offset = (page - 1) * limit
        
        # Build query with filters
        query = select(Hospital).options(
            selectinload(Hospital.subscription).selectinload(HospitalSubscription.plan)
        )
        
        # Apply filters
        conditions = []
        if status_filter:
            # Filter by hospital active status (we'll need to add this field)
            pass  # Will implement when we add is_active field to Hospital model
        
        if subscription_filter:
            query = query.join(HospitalSubscription).join(SubscriptionPlanModel)
            conditions.append(SubscriptionPlanModel.name == subscription_filter)
        
        if city_filter:
            conditions.append(Hospital.city.ilike(f"%{city_filter}%"))
        
        if state_filter:
            conditions.append(Hospital.state.ilike(f"%{state_filter}%"))
        
        if conditions:
            query = query.where(and_(*conditions))
        
        # Get total count
        count_query = select(func.count(Hospital.id))
        if conditions:
            count_query = count_query.where(and_(*conditions))
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.offset(offset).limit(limit).order_by(Hospital.created_at.desc())
        result = await self.db.execute(query)
        hospitals = result.scalars().all()
        
        # Format response
        hospital_list = []
        for hospital in hospitals:
            subscription_status = None
            subscription_plan = None
            
            if hospital.subscription:
                subscription_status = hospital.subscription.status
                subscription_plan = hospital.subscription.plan.name if hospital.subscription.plan else None
            
            hospital_list.append({
                "id": str(hospital.id),
                "name": hospital.name,
                "email": hospital.email,
                "city": hospital.city,
                "state": hospital.state,
                "registration_number": hospital.registration_number,
                "subscription_status": subscription_status,
                "subscription_plan": subscription_plan,
                "created_at": hospital.created_at.isoformat(),
                "is_active": True  # Default for now, will implement proper status field
            })
        
        return {
            "hospitals": hospital_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }
    
    async def get_hospital_details(self, hospital_id: uuid.UUID) -> Dict[str, Any]:
        """Get detailed hospital information"""
        # Get hospital with subscription details
        query = select(Hospital).options(
            selectinload(Hospital.subscription).selectinload(HospitalSubscription.plan)
        ).where(Hospital.id == hospital_id)
        
        result = await self.db.execute(query)
        hospital = result.scalar_one_or_none()
        
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Get hospital admin count
        admin_count_query = select(func.count(User.id)).where(
            and_(
                User.hospital_id == hospital_id,
                User.roles.any(Role.name == UserRole.HOSPITAL_ADMIN)
            )
        )
        admin_count_result = await self.db.execute(admin_count_query)
        admin_count = admin_count_result.scalar() or 0
        
        # Get total user count
        user_count_query = select(func.count(User.id)).where(User.hospital_id == hospital_id)
        user_count_result = await self.db.execute(user_count_query)
        user_count = user_count_result.scalar() or 0
        
        # Format subscription details
        subscription_details = None
        if hospital.subscription:
            subscription_details = {
                "plan_name": hospital.subscription.plan.name if hospital.subscription.plan else None,
                "plan_display_name": hospital.subscription.plan.display_name if hospital.subscription.plan else None,
                "status": hospital.subscription.status,
                "start_date": hospital.subscription.start_date.isoformat(),
                "end_date": hospital.subscription.end_date.isoformat(),
                "is_trial": hospital.subscription.is_trial,
                "auto_renew": hospital.subscription.auto_renew,
                "current_usage": hospital.subscription.current_usage
            }
        
        return {
            "id": str(hospital.id),
            "name": hospital.name,
            "registration_number": hospital.registration_number,
            "email": hospital.email,
            "phone": hospital.phone,
            "address": hospital.address,
            "city": hospital.city,
            "state": hospital.state,
            "country": hospital.country,
            "pincode": hospital.pincode,
            "license_number": hospital.license_number,
            "established_date": hospital.established_date.isoformat() if hospital.established_date else None,
            "website": hospital.website,
            "logo_url": hospital.logo_url,
            "settings": hospital.settings,
            "created_at": hospital.created_at.isoformat(),
            "updated_at": hospital.updated_at.isoformat(),
            "subscription": subscription_details,
            "metrics": {
                "total_users": user_count,
                "admin_count": admin_count,
                "is_active": True  # Will implement proper status tracking
            }
        }
    
    async def update_hospital(self, hospital_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update hospital information"""
        # Get hospital
        result = await self.db.execute(select(Hospital).where(Hospital.id == hospital_id))
        hospital = result.scalar_one_or_none()
        
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Check if registration number is being changed and ensure uniqueness
        if "registration_number" in update_data and update_data["registration_number"] != hospital.registration_number:
            existing_hospital = await self.db.execute(
                select(Hospital).where(
                    and_(
                        Hospital.registration_number == update_data["registration_number"],
                        Hospital.id != hospital_id
                    )
                )
            )
            if existing_hospital.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "REGISTRATION_NUMBER_EXISTS", "message": "Hospital with this registration number already exists"}
                )
        
        # Update fields
        for field, value in update_data.items():
            if hasattr(hospital, field) and value is not None:
                setattr(hospital, field, value)
        
        hospital.updated_at = datetime.utcnow()
        await self.db.commit()
        
        # TODO: Add audit log entry
        
        return {
            "id": str(hospital.id),
            "message": "Hospital updated successfully"
        }
    
    async def update_hospital_status(self, hospital_id: uuid.UUID, new_status: str) -> Dict[str, Any]:
        """Update hospital operational status"""
        # Get hospital
        result = await self.db.execute(select(Hospital).where(Hospital.id == hospital_id))
        hospital = result.scalar_one_or_none()
        
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Validate status
        if new_status not in [HospitalStatus.ACTIVE, HospitalStatus.SUSPENDED, HospitalStatus.INACTIVE]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_STATUS", "message": "Invalid status value"}
            )
        
        # Update status
        old_status = hospital.status
        hospital.status = new_status
        hospital.is_active = (new_status == HospitalStatus.ACTIVE)
        hospital.updated_at = datetime.utcnow()
        
        # If suspending or deactivating, update all users' access
        if new_status in [HospitalStatus.SUSPENDED, HospitalStatus.INACTIVE]:
            # Get all users for this hospital
            users_query = select(User).where(User.hospital_id == hospital_id)
            users_result = await self.db.execute(users_query)
            users = users_result.scalars().all()
            
            # Block all users except super admins
            for user in users:
                user_roles = [role.name for role in user.roles]
                if UserRole.SUPER_ADMIN not in user_roles:
                    user.status = UserStatus.BLOCKED
        
        await self.db.commit()
        
        # TODO: Send notification emails to hospital admins
        # TODO: Add audit log entry
        
        return {
            "hospital_id": str(hospital.id),
            "old_status": old_status,
            "new_status": new_status,
            "message": f"Hospital status updated to {new_status}"
        }
    
    # ============================================================================
    # HOSPITAL ADMINISTRATOR MANAGEMENT
    # ============================================================================
    
    async def get_hospital_admins(self, hospital_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Get list of hospital administrators"""
        # Verify hospital exists
        hospital_result = await self.db.execute(select(Hospital).where(Hospital.id == hospital_id))
        if not hospital_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Get hospital admins
        query = select(User).options(
            selectinload(User.roles)
        ).where(
            and_(
                User.hospital_id == hospital_id,
                User.roles.any(Role.name == UserRole.HOSPITAL_ADMIN)
            )
        ).order_by(User.created_at.desc())
        
        result = await self.db.execute(query)
        admins = result.scalars().all()
        
        admin_list = []
        for admin in admins:
            admin_list.append({
                "id": str(admin.id),
                "email": admin.email,
                "first_name": admin.first_name,
                "last_name": admin.last_name,
                "phone": admin.phone,
                "status": admin.status,
                "email_verified": admin.email_verified,
                "last_login": admin.last_login.isoformat() if admin.last_login else None,
                "created_at": admin.created_at.isoformat()
            })
        
        return admin_list
    
    async def update_admin_status(self, admin_id: uuid.UUID, new_status: str) -> Dict[str, Any]:
        """Update hospital administrator status"""
        # Get admin user
        query = select(User).options(selectinload(User.roles)).where(User.id == admin_id)
        result = await self.db.execute(query)
        admin = result.scalar_one_or_none()
        
        if not admin:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ADMIN_NOT_FOUND", "message": "Hospital administrator not found"}
            )
        
        # Verify user is a hospital admin
        user_roles = [role.name for role in admin.roles]
        if UserRole.HOSPITAL_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NOT_HOSPITAL_ADMIN", "message": "User is not a hospital administrator"}
            )
        
        # Validate status
        if new_status not in [UserStatus.ACTIVE, UserStatus.BLOCKED, UserStatus.PENDING]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_STATUS", "message": "Invalid status value"}
            )
        
        # Update status
        old_status = admin.status
        admin.status = new_status
        admin.updated_at = datetime.utcnow()
        
        await self.db.commit()
        
        # TODO: Send notification email on status change
        # TODO: Add audit log entry
        
        return {
            "admin_id": str(admin.id),
            "old_status": old_status,
            "new_status": new_status,
            "message": f"Administrator status updated to {new_status}"
        }

    # ============================================================================
    # SUPER ADMIN - USER ACCOUNTS
    # ============================================================================

    async def get_super_admin_users(
        self,
        page: int = 1,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Get all hospital admin users with their hospital details."""
        from app.models.tenant import Hospital

        offset = (page - 1) * limit

        # Only hospital admins (HOSPITAL_ADMIN). This matches the request shape
        # which includes admin_name and hospital registration details.
        query = (
            select(User, Hospital)
            .options(selectinload(User.roles))
            .join(Hospital, Hospital.id == User.hospital_id)
            .where(
                and_(
                    User.hospital_id.isnot(None),
                    User.roles.any(Role.name == UserRole.HOSPITAL_ADMIN),
                )
            )
            .order_by(User.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        count_query = (
            select(func.count(User.id))
            .where(
                and_(
                    User.hospital_id.isnot(None),
                    User.roles.any(Role.name == UserRole.HOSPITAL_ADMIN),
                )
            )
        )

        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0

        result = await self.db.execute(query)
        rows = result.all()

        users_list: List[Dict[str, Any]] = []
        for user, hospital in rows:
            admin_name = f"{user.first_name} {user.last_name}".strip()
            users_list.append(
                {
                    "id": str(user.id),
                    "hospital_name": hospital.name,
                    "email": user.email,
                    "phone_number": user.phone,
                    "address": hospital.address,
                    "city": hospital.city,
                    "state": hospital.state,
                    "country": hospital.country,
                    "pincode": hospital.pincode,
                    "admin_name": admin_name,
                    "status": user.status,
                    "registration_no": hospital.registration_number,
                    "hospital_logo": hospital.logo_url,
                }
            )

        return {
            "users": users_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit if limit else 1,
            },
            "message": "Users retrieved successfully",
        }

    async def update_super_admin_user(
        self,
        user_id: uuid.UUID,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update an existing hospital admin user + its hospital details."""
        from app.models.tenant import Hospital

        query = select(User).options(selectinload(User.roles)).where(User.id == user_id)
        result = await self.db.execute(query)
        user: Optional[User] = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "USER_NOT_FOUND", "message": "User not found"},
            )

        user_roles = [role.name for role in user.roles]
        if UserRole.HOSPITAL_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NOT_HOSPITAL_ADMIN", "message": "User is not a hospital administrator"},
            )

        if not user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NO_HOSPITAL", "message": "User has no hospital assigned"},
            )

        hosp_result = await self.db.execute(select(Hospital).where(Hospital.id == user.hospital_id))
        hospital: Optional[Hospital] = hosp_result.scalar_one_or_none()
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"},
            )

        normalized_status = payload.get("status")
        if normalized_status not in [UserStatus.ACTIVE, UserStatus.BLOCKED, UserStatus.PENDING]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_STATUS", "message": "Invalid status value"},
            )

        # Uniqueness checks
        email = str(payload["email"]).strip().lower()
        if email != user.email:
            existing_user = await self.db.execute(
                select(User).where(
                    and_(func.lower(User.email) == email, User.id != user_id)
                )
            )
            if existing_user.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "EMAIL_EXISTS", "message": "Email already exists"},
                )

        registration_no = str(payload["registration_no"]).strip()
        if registration_no != hospital.registration_number:
            existing_hospital = await self.db.execute(
                select(Hospital).where(
                    and_(
                        Hospital.registration_number == registration_no,
                        Hospital.id != hospital.id,
                    )
                )
            )
            if existing_hospital.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "REGISTRATION_NO_EXISTS", "message": "Hospital registration number already exists"},
                )

        # Update hospital
        hospital.name = payload["hospital_name"]
        hospital.email = email
        hospital.phone = payload["phone_number"]
        hospital.address = payload["address"]
        hospital.city = payload["city"]
        hospital.state = payload["state"]
        hospital.country = payload["country"]
        hospital.pincode = payload["pincode"]
        hospital.registration_number = registration_no
        if payload.get("hospital_logo") is not None:
            hospital.logo_url = payload["hospital_logo"]

        # Update user (admin)
        admin_name = payload["admin_name"].strip()
        parts = admin_name.split()
        first_name = parts[0] if parts else ""
        last_name = " ".join(parts[1:]) if len(parts) > 1 else (parts[0] if parts else "")

        user.email = email
        user.phone = payload["phone_number"]
        user.first_name = first_name
        user.last_name = last_name
        user.status = normalized_status
        user.updated_at = datetime.utcnow()

        await self.db.commit()

        return {
            "user_id": str(user.id),
            "message": "User updated successfully",
        }

    async def set_super_admin_user_status(self, user_id: uuid.UUID, new_status: str) -> Dict[str, Any]:
        """Toggle ACTIVE / INACTIVE for hospital admin user."""
        query = select(User).options(selectinload(User.roles)).where(User.id == user_id)
        result = await self.db.execute(query)
        user: Optional[User] = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "USER_NOT_FOUND", "message": "User not found"},
            )

        user_roles = [role.name for role in user.roles]
        if UserRole.HOSPITAL_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NOT_HOSPITAL_ADMIN", "message": "User is not a hospital administrator"},
            )

        if new_status not in [UserStatus.ACTIVE, UserStatus.BLOCKED, UserStatus.PENDING]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_STATUS", "message": "Invalid status value"},
            )

        old_status = user.status
        user.status = new_status
        user.updated_at = datetime.utcnow()
        await self.db.commit()

        return {
            "user_id": str(user.id),
            "old_status": old_status,
            "new_status": new_status,
            "message": "User status updated successfully",
        }

    async def delete_super_admin_user(self, user_id: uuid.UUID) -> Dict[str, Any]:
        """
        Soft delete for safety: set user status to BLOCKED.
        (Hard delete may break FK constraints from other tables.)
        """
        query = select(User).options(selectinload(User.roles)).where(User.id == user_id)
        result = await self.db.execute(query)
        user: Optional[User] = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "USER_NOT_FOUND", "message": "User not found"},
            )

        user_roles = [role.name for role in user.roles]
        if UserRole.HOSPITAL_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NOT_HOSPITAL_ADMIN", "message": "User is not a hospital administrator"},
            )

        user.status = UserStatus.BLOCKED
        user.updated_at = datetime.utcnow()
        await self.db.commit()

        return {
            "user_id": str(user.id),
            "message": "User deleted successfully",
        }
    
    # ============================================================================
    # SUBSCRIPTION PLAN MANAGEMENT
    # ============================================================================
    
    async def create_subscription_plan(self, plan_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new subscription plan"""
        from app.models.tenant import SubscriptionPlanModel
        
        # Check if plan with same name already exists
        existing_plan = await self.db.execute(
            select(SubscriptionPlanModel).where(SubscriptionPlanModel.name == plan_data['name'])
        )
        if existing_plan.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "PLAN_EXISTS", "message": "Subscription plan with this name already exists"}
            )
        
        # Create subscription plan
        plan = SubscriptionPlanModel(
            id=uuid.uuid4(),
            name=plan_data['name'],
            display_name=plan_data['display_name'],
            description=plan_data.get('description'),
            monthly_price=plan_data['monthly_price'],
            yearly_price=plan_data['yearly_price'],
            max_doctors=plan_data['max_doctors'],
            max_patients=plan_data['max_patients'],
            max_appointments_per_month=plan_data['max_appointments_per_month'],
            max_storage_gb=plan_data['max_storage_gb'],
            features=plan_data.get('features', {})
        )
        
        self.db.add(plan)
        await self.db.commit()
        
        return {
            "plan_id": str(plan.id),
            "name": plan.name,
            "display_name": plan.display_name,
            "message": "Subscription plan created successfully"
        }
    
    async def get_subscription_plans(self) -> List[Dict[str, Any]]:
        """Get all subscription plans"""
        from app.models.tenant import SubscriptionPlanModel
        
        result = await self.db.execute(
            select(SubscriptionPlanModel).order_by(SubscriptionPlanModel.created_at.desc())
        )
        plans = result.scalars().all()
        
        plan_list = []
        for plan in plans:
            plan_list.append({
                "id": str(plan.id),
                "name": plan.name,
                "display_name": plan.display_name,
                "description": plan.description,
                "monthly_price": float(plan.monthly_price),
                "yearly_price": float(plan.yearly_price),
                "max_doctors": plan.max_doctors,
                "max_patients": plan.max_patients,
                "max_appointments_per_month": plan.max_appointments_per_month,
                "max_storage_gb": plan.max_storage_gb,
                "features": plan.features,
                "created_at": plan.created_at.isoformat(),
                "updated_at": plan.updated_at.isoformat()
            })
        
        return plan_list
    
    async def update_subscription_plan(self, plan_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing subscription plan"""
        from app.models.tenant import SubscriptionPlanModel
        
        # Get plan
        result = await self.db.execute(select(SubscriptionPlanModel).where(SubscriptionPlanModel.id == plan_id))
        plan = result.scalar_one_or_none()
        
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PLAN_NOT_FOUND", "message": "Subscription plan not found"}
            )
        
        # Update fields
        for field, value in update_data.items():
            if hasattr(plan, field) and value is not None:
                setattr(plan, field, value)
        
        plan.updated_at = datetime.utcnow()
        await self.db.commit()
        
        return {
            "plan_id": str(plan.id),
            "message": "Subscription plan updated successfully"
        }
    
    async def delete_subscription_plan(self, plan_id: uuid.UUID) -> Dict[str, Any]:
        """Delete a subscription plan"""
        from app.models.tenant import SubscriptionPlanModel, HospitalSubscription
        
        # Get plan
        result = await self.db.execute(select(SubscriptionPlanModel).where(SubscriptionPlanModel.id == plan_id))
        plan = result.scalar_one_or_none()
        
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PLAN_NOT_FOUND", "message": "Subscription plan not found"}
            )
        
        # Check if plan has active subscribers
        subscribers_result = await self.db.execute(
            select(func.count(HospitalSubscription.id)).where(HospitalSubscription.plan_id == plan_id)
        )
        subscriber_count = subscribers_result.scalar()
        
        if subscriber_count > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PLAN_HAS_SUBSCRIBERS", "message": f"Cannot delete plan with {subscriber_count} active subscribers"}
            )
        
        # Delete plan
        await self.db.delete(plan)
        await self.db.commit()
        
        return {
            "plan_id": str(plan.id),
            "message": "Subscription plan deleted successfully"
        }
    
    async def assign_subscription_plan_by_names(
        self, 
        hospital_name: str, 
        plan_name: str,
        assignment_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Assign a subscription plan to a hospital using names instead of IDs"""
        from app.models.tenant import SubscriptionPlanModel, HospitalSubscription
        
        # Find hospital by name
        hospital_result = await self.db.execute(
            select(Hospital).where(Hospital.name == hospital_name)
        )
        hospital = hospital_result.scalar_one_or_none()
        
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": f"Hospital '{hospital_name}' not found"}
            )
        
        # Find plan by name
        plan_result = await self.db.execute(
            select(SubscriptionPlanModel).where(SubscriptionPlanModel.name == plan_name)
        )
        plan = plan_result.scalar_one_or_none()
        
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PLAN_NOT_FOUND", "message": f"Subscription plan '{plan_name}' not found"}
            )
        
        # Use the existing logic with the found IDs
        return await self.assign_subscription_plan(hospital.id, plan.id, assignment_data)

    async def assign_subscription_plan(
        self, 
        hospital_id: uuid.UUID, 
        plan_id: uuid.UUID,
        assignment_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Assign a subscription plan to a hospital"""
        from app.models.tenant import SubscriptionPlanModel, HospitalSubscription
        
        # Verify hospital exists
        hospital = await self._get_hospital_by_id(hospital_id)
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Verify plan exists
        plan_result = await self.db.execute(select(SubscriptionPlanModel).where(SubscriptionPlanModel.id == plan_id))
        plan = plan_result.scalar_one_or_none()
        
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PLAN_NOT_FOUND", "message": "Subscription plan not found"}
            )
        
        # Check if hospital already has a subscription
        existing_subscription = await self.db.execute(
            select(HospitalSubscription).where(HospitalSubscription.hospital_id == hospital_id)
        )
        current_subscription = existing_subscription.scalar_one_or_none()
        
        # Calculate dates with proper format handling
        start_date = parse_date_string(assignment_data.get('start_date')) or datetime.utcnow()
        if assignment_data.get('end_date'):
            end_date = parse_date_string(assignment_data['end_date'])
        else:
            # Default to 1 year from start date
            end_date = start_date.replace(year=start_date.year + 1)
        
        if current_subscription:
            # Update existing subscription
            current_subscription.plan_id = plan_id
            current_subscription.start_date = start_date
            current_subscription.end_date = end_date
            current_subscription.is_trial = assignment_data.get('is_trial', False)
            current_subscription.auto_renew = assignment_data.get('auto_renew', True)
            current_subscription.status = SubscriptionStatus.ACTIVE
            current_subscription.updated_at = datetime.utcnow()
            
            message = "Hospital subscription updated successfully"
        else:
            # Create new subscription
            subscription = HospitalSubscription(
                id=uuid.uuid4(),
                hospital_id=hospital_id,
                plan_id=plan_id,
                status=SubscriptionStatus.ACTIVE,
                start_date=start_date,
                end_date=end_date,
                is_trial=assignment_data.get('is_trial', False),
                auto_renew=assignment_data.get('auto_renew', True),
                current_usage={}
            )
            
            self.db.add(subscription)
            message = "Hospital subscription created successfully"

        # If the hospital is active, unblock hospital admins/staff that were blocked
        # due to hospital deactivation so they can login again (subscription gating is enforced at login).
        if hospital.is_active and hospital.status == HospitalStatus.ACTIVE:
            users_q = (
                select(User)
                .options(selectinload(User.roles))
                .where(User.hospital_id == hospital_id)
            )
            users_result = await self.db.execute(users_q)
            users = users_result.scalars().all()

            allowed_roles = {
                UserRole.HOSPITAL_ADMIN,
                UserRole.DOCTOR,
                UserRole.NURSE,
                UserRole.RECEPTIONIST,
                UserRole.PHARMACIST,
                UserRole.LAB_TECH,
            }

            for u in users:
                if u.status != UserStatus.BLOCKED:
                    continue
                role_names = {r.name for r in (u.roles or [])}
                if role_names.intersection(allowed_roles):
                    u.status = UserStatus.ACTIVE

        await self.db.commit()
        
        return {
            "hospital_name": hospital.name,
            "plan_name": plan.name,
            "message": message
        }
    
    async def get_hospital_subscription(self, hospital_id: uuid.UUID) -> Dict[str, Any]:
        """Get hospital subscription details"""
        from app.models.tenant import HospitalSubscription, SubscriptionPlanModel
        
        # Verify hospital exists
        hospital = await self._get_hospital_by_id(hospital_id)
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Get subscription with plan details
        query = select(HospitalSubscription).options(
            selectinload(HospitalSubscription.plan)
        ).where(HospitalSubscription.hospital_id == hospital_id)
        
        result = await self.db.execute(query)
        subscription = result.scalar_one_or_none()
        
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "SUBSCRIPTION_NOT_FOUND", "message": "Hospital subscription not found"}
            )
        
        # Get usage metrics (placeholder - would be calculated from actual usage)
        current_usage = subscription.current_usage or {}
        
        return {
            "hospital_name": hospital.name,
            "plan": {
                "name": subscription.plan.name,
                "display_name": subscription.plan.display_name,
                "monthly_price": float(subscription.plan.monthly_price),
                "yearly_price": float(subscription.plan.yearly_price),
                "features": subscription.plan.features
            },
            "status": subscription.status,
            "start_date": subscription.start_date.isoformat(),
            "end_date": subscription.end_date.isoformat(),
            "is_trial": subscription.is_trial,
            "trial_end_date": subscription.trial_end_date.isoformat() if subscription.trial_end_date else None,
            "auto_renew": subscription.auto_renew,
            "current_usage": current_usage,
            "limits": {
                "max_doctors": subscription.plan.max_doctors,
                "max_patients": subscription.plan.max_patients,
                "max_appointments_per_month": subscription.plan.max_appointments_per_month,
                "max_storage_gb": subscription.plan.max_storage_gb
            },
            "created_at": subscription.created_at.isoformat(),
            "updated_at": subscription.updated_at.isoformat()
        }
    
    async def get_hospital_subscription_by_name(self, hospital_name: str) -> Dict[str, Any]:
        """Get hospital subscription details using hospital name"""
        from app.models.tenant import HospitalSubscription, SubscriptionPlanModel
        
        # Find hospital by name
        hospital_result = await self.db.execute(
            select(Hospital).where(Hospital.name == hospital_name)
        )
        hospital = hospital_result.scalar_one_or_none()
        
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": f"Hospital '{hospital_name}' not found"}
            )
        
        # Use the existing method with the found hospital ID
        return await self.get_hospital_subscription(hospital.id)
    
    # ============================================================================
    # HELPER METHODS
    # ============================================================================
    
    async def _get_hospital_by_id(self, hospital_id: uuid.UUID) -> Optional[Hospital]:
        """Get hospital by ID"""
        result = await self.db.execute(select(Hospital).where(Hospital.id == hospital_id))
        return result.scalar_one_or_none()
    
    async def _verify_super_admin_access(self, user: User) -> None:
        """Verify user has Super Admin access"""
        user_roles = [role.name for role in user.roles]
        if UserRole.SUPER_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "INSUFFICIENT_PERMISSIONS", "message": "Super Admin access required"}
            )
    
    async def _log_admin_action(
        self, 
        user_id: uuid.UUID, 
        action: str, 
        resource_type: str, 
        resource_id: Optional[uuid.UUID] = None,
        description: str = "",
        old_values: Optional[Dict] = None,
        new_values: Optional[Dict] = None
    ) -> None:
        """Log administrative action for audit purposes"""
        audit_log = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            description=description,
            old_values=old_values,
            new_values=new_values,
            is_sensitive=True  # All Super Admin actions are sensitive
        )
        
        self.db.add(audit_log)
        # Note: Commit will be handled by the calling method

    # ============================================================================
    # PLATFORM ANALYTICS & DASHBOARD
    # ============================================================================

    async def get_platform_analytics(self) -> Dict[str, Any]:
        """Get platform-wide dashboard: hospitals, subscriptions, revenue, patient trends, occupancy."""
        from app.models.billing import BillingPayment, Bill
        from app.models.patient import PatientProfile, Appointment, Admission
        from app.models.hospital import Bed, Ward

        # Total hospitals
        hospitals_count = await self.db.execute(select(func.count(Hospital.id)))
        total_hospitals = hospitals_count.scalar() or 0

        # Active hospitals (status ACTIVE)
        active_hospitals = await self.db.execute(
            select(func.count(Hospital.id)).where(Hospital.status == HospitalStatus.ACTIVE)
        )
        active_hospitals_count = active_hospitals.scalar() or 0

        # Subscriptions by plan
        sub_query = (
            select(SubscriptionPlanModel.name, func.count(HospitalSubscription.id))
            .select_from(SubscriptionPlanModel)
            .join(HospitalSubscription, HospitalSubscription.plan_id == SubscriptionPlanModel.id)
            .where(HospitalSubscription.status == SubscriptionStatus.ACTIVE)
            .group_by(SubscriptionPlanModel.name)
        )
        sub_result = await self.db.execute(sub_query)
        subscriptions_by_plan = {row[0]: row[1] for row in sub_result.all()}

        # Total revenue (sum of SUCCESS payments across all hospitals)
        rev_query = select(
            func.coalesce(func.sum(BillingPayment.amount), 0).label("total")
        ).where(BillingPayment.status == "SUCCESS")
        rev_result = await self.db.execute(rev_query)
        total_revenue = float(rev_result.scalar() or 0)

        # Patient count (all hospitals)
        patient_count = await self.db.execute(select(func.count(PatientProfile.id)))
        total_patients = patient_count.scalar() or 0

        # Appointments this month
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        appt_count = await self.db.execute(
            select(func.count(Appointment.id)).where(Appointment.created_at >= month_start)
        )
        appointments_this_month = appt_count.scalar() or 0

        # Occupancy: total beds vs occupied
        beds_total = await self.db.execute(select(func.count(Bed.id)))
        beds_occupied = await self.db.execute(
            select(func.count(Bed.id)).where(Bed.status == "OCCUPIED")
        )
        total_beds = beds_total.scalar() or 0
        occupied_beds = beds_occupied.scalar() or 0
        occupancy_rate = (occupied_beds / total_beds * 100) if total_beds > 0 else 0

        return {
            "hospitals": {
                "total": total_hospitals,
                "active": active_hospitals_count,
            },
            "subscriptions": {
                "by_plan": subscriptions_by_plan,
                "active_count": sum(subscriptions_by_plan.values()),
            },
            "revenue": {
                "total": total_revenue,
            },
            "patients": {
                "total": total_patients,
                "appointments_this_month": appointments_this_month,
            },
            "occupancy": {
                "total_beds": total_beds,
                "occupied_beds": occupied_beds,
                "occupancy_rate_percent": round(occupancy_rate, 2),
            },
        }

    async def delete_hospital(self, hospital_id: uuid.UUID, confirm: bool = False) -> Dict[str, Any]:
        """Soft delete hospital: set status INACTIVE, block users. Requires confirm=True."""
        hospital = await self._get_hospital_by_id(hospital_id)
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        if not confirm:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "CONFIRM_REQUIRED", "message": "Set confirm=true to delete hospital"}
            )
        old_status = hospital.status
        hospital.status = HospitalStatus.INACTIVE
        hospital.is_active = False
        hospital.updated_at = datetime.utcnow()
        # Block all users
        users_result = await self.db.execute(select(User).where(User.hospital_id == hospital_id))
        for user in users_result.scalars().all():
            user_roles = [r.name for r in user.roles]
            if UserRole.SUPER_ADMIN not in user_roles:
                user.status = UserStatus.BLOCKED
        await self.db.commit()
        return {"hospital_id": str(hospital_id), "message": "Hospital deactivated successfully"}

    async def reset_admin_password(self, admin_id: uuid.UUID) -> Dict[str, Any]:
        """Reset hospital admin password: generate temp, set hash, return temp password."""
        query = select(User).options(selectinload(User.roles)).where(User.id == admin_id)
        result = await self.db.execute(query)
        admin = result.scalar_one_or_none()
        if not admin:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ADMIN_NOT_FOUND", "message": "Administrator not found"}
            )
        user_roles = [r.name for r in admin.roles]
        if UserRole.HOSPITAL_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NOT_HOSPITAL_ADMIN", "message": "User is not a hospital administrator"}
            )
        temp_password = self.security.generate_temp_password(12)
        admin.password_hash = self.security.hash_password(temp_password)
        admin.password_changed_at = datetime.utcnow()
        admin.updated_at = datetime.utcnow()
        await self.db.commit()
        return {"admin_id": str(admin_id), "temp_password": temp_password, "email": admin.email}

    async def get_platform_audit_logs(
        self, skip: int = 0, limit: int = 50, resource_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get platform audit logs (across all hospitals)."""
        conditions = []
        if resource_type:
            conditions.append(AuditLog.resource_type == resource_type)
        count_q = select(func.count(AuditLog.id)).where(and_(*conditions)) if conditions else select(func.count(AuditLog.id))
        total = (await self.db.execute(count_q)).scalar() or 0
        q = select(AuditLog).order_by(desc(AuditLog.created_at)).offset(skip).limit(limit)
        if conditions:
            q = q.where(and_(*conditions))
        r = await self.db.execute(q)
        logs = r.scalars().all()
        log_list = []
        for log in logs:
            log_list.append({
                "id": str(log.id),
                "user_id": str(log.user_id),
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "description": log.description,
                "hospital_id": str(log.hospital_id) if log.hospital_id else None,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            })
        return {"logs": log_list, "total": total, "skip": skip, "limit": limit}

    async def create_support_ticket(self, hospital_id: uuid.UUID, raised_by_user_id: uuid.UUID, subject: str, description: str, priority: str = "NORMAL") -> Dict[str, Any]:
        """Create support ticket (can be called by Hospital Admin or Super Admin on their behalf)."""
        from app.models.support import SupportTicket
        ticket = SupportTicket(hospital_id=hospital_id, raised_by_user_id=raised_by_user_id, subject=subject, description=description, status="OPEN", priority=priority)
        self.db.add(ticket)
        await self.db.flush()
        await self.db.commit()
        return {"ticket_id": str(ticket.id), "status": "OPEN", "message": "Ticket created"}

    async def list_support_tickets(
        self, hospital_id: Optional[uuid.UUID] = None, status: Optional[str] = None, skip: int = 0, limit: int = 50
    ) -> Dict[str, Any]:
        """List support tickets with optional filters."""
        from app.models.support import SupportTicket
        conditions = []
        if hospital_id:
            conditions.append(SupportTicket.hospital_id == hospital_id)
        if status:
            conditions.append(SupportTicket.status == status)
        count_q = select(func.count(SupportTicket.id))
        if conditions:
            count_q = count_q.where(and_(*conditions))
        total = (await self.db.execute(count_q)).scalar() or 0
        q = select(SupportTicket).order_by(desc(SupportTicket.created_at)).offset(skip).limit(limit)
        if conditions:
            q = q.where(and_(*conditions))
        r = await self.db.execute(q)
        tickets = r.scalars().all()
        return {"tickets": [{"id": str(t.id), "hospital_id": str(t.hospital_id), "subject": t.subject, "status": t.status, "priority": t.priority, "created_at": t.created_at.isoformat()} for t in tickets], "total": total, "skip": skip, "limit": limit}

    async def update_support_ticket_status(self, ticket_id: uuid.UUID, new_status: str, resolution_notes: Optional[str] = None, assigned_to_user_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
        """Update support ticket status (Super Admin only)."""
        from app.models.support import SupportTicket
        r = await self.db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
        ticket = r.scalar_one_or_none()
        if not ticket:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "TICKET_NOT_FOUND", "message": "Support ticket not found"})
        valid = ["OPEN", "IN_PROGRESS", "ESCALATED", "RESOLVED", "CLOSED"]
        if new_status not in valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_STATUS", "message": f"Valid: {valid}"})
        ticket.status = new_status
        if resolution_notes is not None:
            ticket.resolution_notes = resolution_notes
        if assigned_to_user_id is not None:
            ticket.assigned_to_user_id = assigned_to_user_id
        if new_status in ("RESOLVED", "CLOSED"):
            ticket.resolved_at = datetime.utcnow()
        ticket.updated_at = datetime.utcnow()
        await self.db.commit()
        return {"ticket_id": str(ticket_id), "status": new_status, "message": "Ticket updated"}

    async def notify_hospital_admins(self, hospital_id: Optional[uuid.UUID], subject: str, message: str, created_by_user_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
        """Send notification to hospital admins. If hospital_id is None, send to all hospital admins."""
        from app.services.notifications import NotificationService
        query = select(User).options(selectinload(User.roles)).where(User.roles.any(Role.name == UserRole.HOSPITAL_ADMIN))
        if hospital_id:
            query = query.where(User.hospital_id == hospital_id)
        r = await self.db.execute(query)
        admins = r.scalars().all()
        if not admins:
            return {"sent": 0, "message": "No hospital admins found"}
        sent = 0
        for admin in admins:
            if not admin.email:
                continue
            hid = admin.hospital_id
            if not hid:
                continue
            try:
                svc = NotificationService(self.db, hid)
                key = f"super_admin_notify:{hid}:{admin.id}:{datetime.utcnow().isoformat()}"
                await svc.send(channel="EMAIL", to=admin.email, idempotency_key=key, event_type="ADMIN_NOTIFICATION", raw_message=message, subject=subject, created_by_user_id=created_by_user_id)
                sent += 1
            except Exception:
                pass
        await self.db.commit()
        return {"sent": sent, "total_admins": len(admins), "message": f"Queued {sent} notification(s)"}